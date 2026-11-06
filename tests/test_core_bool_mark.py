"""真偽の値の印 (初見テスト R5 の候補 C55。本人の決め 11-06 17:1x「はいとかはわかりにくいな。こういう感じで全体的にアイコンで統一したい」)。

真はチェックの丸の絵、偽は何も出さない (「—」も「いいえ」も出さない)。一覧・詳細・小窓・検索の全部をそろえ、CSV は字のまま。
(a) tag・(b) 工程の詳細・(c) 部品の詳細・(d) 部品の小窓・(e) マニュアルの一覧・(f) 会社ごとの真偽の項目・(g) CSV・(h) 描き方の抜け。
試験の DB に拡張の表 (fields.ItemExt) は無いので、会社ごとの項目は ext_value を置き換える。
"""

import csv
import io
from pathlib import Path

import pytest
from django.template import Context, Template
from django.urls import reverse

import seizou
from seizou.core import fields as company_fields
from seizou.core.templatetags.seizou_icons import PATHS
from seizou.manuals import services as manuals_services
from seizou.masters import services as masters_services
from seizou.masters.models import Process

MARK = PATHS["check-circle"]
SRC = Path(seizou.__file__).resolve().parent


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


def _mark(value) -> str:
    return Template("{% load seizou_tags %}{% bool_mark v %}").render(Context({"v": value})).strip()


def _dd(html: str, label: str) -> str:
    """見出し (dt) の字の後の最初の dd の中身。行の div の class では探さない。"""
    start = html.index(f">{label}</dt>")
    begin = html.index("<dd", start)
    inner = html.index(">", begin) + 1
    return html[inner : html.index("</dd>", inner)]


def _row(html: str, pk) -> str:
    begin = html.index(f'id="row-{pk}"')
    return html[begin : html.index("</tr>", begin)]


def _field(response, label: str) -> dict:
    return next(item for item in response.context["fields"] if item["label"] == label)


def _items(user):
    """使用中の部品 T-100 と、使用停止の部品 T-900 (図面は無い)。"""
    used = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    stopped = masters_services.create_item({"part_no": "T-900", "name": "作り物の古い軸", "unit": "個"}, user)
    masters_services.update_item(stopped, {"active": False}, user)
    return used, stopped


# ---------------------------------------------------------------- (a) tag


def test_bool_mark_draws_check_circle_for_true():
    html = _mark(True)

    assert 'role="img"' in html and 'aria-label="はい"' in html
    assert MARK in html
    assert "text-primary" in html


@pytest.mark.parametrize("value", [False, None, 1, "はい"])
def test_bool_mark_draws_nothing_for_false_and_other_values(value):
    assert _mark(value) == ""


# ---------------------------------------------------------------- (b) 工程の詳細


@pytest.mark.django_db
def test_process_detail_marks_true_and_leaves_false_empty(logged_in):
    process = Process.objects.create(code="TURN", name="作り物の旋削", use_in_plan=True, is_outsourced=False)

    response = logged_in.get(reverse("masters:process_detail", args=[process.pk]))

    assert response.status_code == 200
    for label in ("計画に使う", "使用中"):
        assert _field(response, label)["boolean"] is True and _field(response, label)["value"] is True
    outsourced = _field(response, "協力企業に出すか")
    assert outsourced["boolean"] is True and outsourced["value"] is False
    html = response.content.decode()
    assert MARK in _dd(html, "計画に使う") and 'aria-label="はい"' in _dd(html, "計画に使う")
    assert MARK in _dd(html, "使用中")
    assert _dd(html, "協力企業に出すか").strip() == ""


# ---------------------------------------------------------------- (c) 部品の詳細


@pytest.mark.django_db
def test_item_detail_marks_active_and_leaves_inactive_empty(logged_in, user):
    used, stopped = _items(user)

    used_html = logged_in.get(reverse("masters:item_detail", args=[used.pk])).content.decode()
    stopped_html = logged_in.get(reverse("masters:item_detail", args=[stopped.pk])).content.decode()

    assert MARK in _dd(used_html, "使用中")
    assert "使用停止の部品です。" not in used_html
    assert _dd(stopped_html, "使用中").strip() == ""
    assert "使用停止の部品です。" in stopped_html


# ---------------------------------------------------------------- (d) 部品の小窓


@pytest.mark.django_db
def test_item_modal_marks_active_and_leaves_inactive_empty(logged_in, user):
    used, stopped = _items(user)

    used_html = logged_in.get(reverse("masters:item_modal", args=[used.pk]), HTTP_HX_REQUEST="true").content.decode()
    stopped_html = logged_in.get(reverse("masters:item_modal", args=[stopped.pk]), HTTP_HX_REQUEST="true").content.decode()

    assert MARK in _dd(used_html, "使用中")
    assert _dd(stopped_html, "使用中").strip() == ""
    assert "—" not in _dd(stopped_html, "使用中") and "いいえ" not in stopped_html


# ---------------------------------------------------------------- (e) マニュアルの一覧


@pytest.mark.django_db
def test_manual_list_marks_protected_only(logged_in, user):
    protected = manuals_services.create_manual(None, "作り物の保護した手順", "steps", user)
    manuals_services.protect_manual(protected, True, user)
    plain = manuals_services.create_manual(None, "作り物の手順", "steps", user)

    html = logged_in.get(reverse("manuals:manual_list")).content.decode()

    assert MARK in _row(html, protected.pk) and 'aria-label="はい"' in _row(html, protected.pk)
    assert MARK not in _row(html, plain.pk)
    assert "いいえ" not in _row(html, plain.pk)


# ---------------------------------------------------------------- (f) 会社ごとの真偽の項目


@pytest.mark.django_db
def test_company_bool_field_is_marked_in_list_detail_and_modal(logged_in, user, no_registration, monkeypatch):
    company_fields.register("masters.Item", "fields.ItemExt", [{"label": "作り物の印", "type": "bool"}])
    monkeypatch.setattr(company_fields, "ext_value", lambda obj, name: obj.part_no == "P-1001")
    marked = masters_services.create_item({"part_no": "P-1001", "name": "作り物の軸"}, user)
    blank = masters_services.create_item({"part_no": "P-1002", "name": "作り物の板"}, user)

    listed = logged_in.get(reverse("masters:item_list")).content.decode()
    assert "作り物の印" in listed[listed.index("<thead") : listed.index("</thead>")]
    assert listed.count(MARK) == 1
    assert MARK in _row(listed, marked.pk) and MARK not in _row(listed, blank.pk)
    assert "True" not in _row(listed, marked.pk) and "False" not in _row(listed, blank.pk)

    for url_name, options in (("masters:item_detail", {}), ("masters:item_modal", {"HTTP_HX_REQUEST": "true"})):
        marked_response = logged_in.get(reverse(url_name, args=[marked.pk]), **options)
        blank_response = logged_in.get(reverse(url_name, args=[blank.pk]), **options)
        assert _field(marked_response, "作り物の印")["boolean"] is True, url_name
        assert MARK in _dd(marked_response.content.decode(), "作り物の印"), url_name
        assert _dd(blank_response.content.decode(), "作り物の印").strip() == "", url_name


# ---------------------------------------------------------------- (g) CSV は字のまま


@pytest.mark.django_db
def test_process_list_csv_keeps_text(logged_in):
    Process.objects.create(code="TURN", name="作り物の旋削", sort_order=10, use_in_plan=True)
    Process.objects.create(code="INSP", name="作り物の検査", sort_order=20)

    text = logged_in.get(reverse("masters:process_list"), {"format": "csv"}).content.decode("utf-8-sig")

    assert "<svg" not in text
    header, *rows = list(csv.reader(io.StringIO(text, newline="")))
    by_code = {row[0]: row for row in rows}
    column = header.index("計画に使う")
    assert by_code["TURN"][column] == "はい"
    assert by_code["INSP"][column] == "いいえ"


# ---------------------------------------------------------------- (h) 描き方の抜け


def test_every_value_template_has_bool_branch():
    """値を描くテンプレートは全部、真偽の枝を持つ (枝の無い所に真偽が来ると英字の True / False が出る)。"""
    templates = {path.relative_to(SRC).as_posix(): path.read_text(encoding="utf-8") for path in SRC.rglob("*.html")}
    cells = {name for name, text in templates.items() if "{{ cell.value" in text}
    fields = {name for name, text in templates.items() if "{{ field.value" in text}

    assert {"core/templates/core/_rows.html", "masters/templates/masters/_row.html", "core/templates/core/search.html"} <= cells
    assert {"core/templates/core/detail.html", "core/templates/core/_modal.html"} <= fields
    assert [name for name in sorted(cells) if "cell.boolean" not in templates[name]] == []
    assert [name for name in sorted(fields) if "field.boolean" not in templates[name]] == []
