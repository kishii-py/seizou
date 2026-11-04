"""書式つきのメモの services (N114。決め 11-04 14:5x) の試験。

見る所: 保存のときの HTML の掃除 (script・外の src・javascript: の link・許さない style が消え、書式は残る)、
1 つの記録に 1 つのメモ (2 回目は上書き)、notes の表が部品の表を変えないこと (R16)。
"""

import importlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import migrations

import seizou
from seizou.core.rules import check
from seizou.masters import services as masters_services
from seizou.notes import services
from seizou.notes.models import Note
from seizou.orders import services as orders_services

SEIZOU_DIR = Path(seizou.__file__).parent


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("note-writer", password="pass-for-tests-only")


@pytest.fixture
def order(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )


@pytest.mark.django_db
def test_save_note_cleans_html(user, order):
    dirty = (
        '<h2>段取り</h2><p><strong>太字</strong> <span style="color: rgb(230, 0, 0); position: fixed">赤</span></p>'
        '<script>alert(1)</script><ul><li>箇条書き</li></ul><blockquote>引用</blockquote>'
        '<img src="https://example.invalid/x.png"><img src="/media/notes/2026/11/a.png" alt="写真" onerror="alert(2)">'
        '<a href="javascript:alert(3)">悪い link</a><a href="https://example.invalid/">外の link</a>'
        '<iframe src="https://example.invalid/"></iframe><p class="ql-indent-1" onclick="x()">字</p>'
    )

    note = services.save_note(order, dirty, user)
    body = Note.objects.get(pk=note.pk).body_html

    assert "<h2>段取り</h2>" in body
    assert "<strong>太字</strong>" in body
    assert '<span style="color:rgb(230, 0, 0)">赤</span>' in body
    assert "<ul><li>箇条書き</li></ul>" in body
    assert "<blockquote>引用</blockquote>" in body
    assert '<img src="/media/notes/2026/11/a.png" alt="写真">' in body
    assert '<a href="https://example.invalid/" rel="noopener noreferrer">外の link</a>' in body
    for gone in ("<script", "alert(", "example.invalid/x.png", "javascript:", "<iframe", "position", "onerror", "onclick", "class="):
        assert gone not in body, gone
    assert body.count("<img") == 1
    assert note.updated_by == user
    # 字も写真も無い本文 (Quill の空の本文) は空の字で持つ (閲覧の形が「メモはまだありません。」を出す)
    assert services.save_note(order, "<p><br></p>", user).body_html == ""


@pytest.mark.django_db
def test_one_note_per_record_second_save_overwrites(user, order):
    first = services.save_note(order, "<p>1 回目</p>", user)
    second = services.save_note(order, "<p>2 回目</p>", user)

    assert first.pk == second.pk
    assert Note.objects.count() == 1
    assert Note.objects.get().body_html == "<p>2 回目</p>"
    assert services.note_for(order).pk == first.pk
    with pytest.raises(ValueError, match="長すぎます"):
        services.save_note(order, "<p>" + "字" * services.MAX_BODY_CHARS + "</p>", user)
    assert Note.objects.get().body_html == "<p>2 回目</p>"


def test_notes_tables_do_not_change_part_tables():
    result = check([SEIZOU_DIR / "notes"])
    assert result.violations == ()

    migration = importlib.import_module("seizou.notes.migrations.0001_initial").Migration
    created = {op.name.lower() for op in migration.operations if isinstance(op, migrations.CreateModel)}
    assert created == {"note", "noteimage"}
    for op in migration.operations:
        assert isinstance(op, (migrations.CreateModel, migrations.AddConstraint)), op
        if isinstance(op, migrations.AddConstraint):
            assert op.model_name == "note"
    assert sorted(path.name for path in (SEIZOU_DIR / "notes" / "migrations").glob("0*.py")) == ["0001_initial.py"]
