"""指示書の詳細の取消が出ない人への訳と頼む先 (初見テスト R5 の V17・V18・U19・U20。候補 C12 の 1 と 2。決め C12-1 = a)。

1 = 「?」の説明の断片 (標準の組と 2 社の種) の取消の行を「出ない人がいる」形にする。
2 = 部署と編集の判定が on の会社で、発行の指示書の詳細を開いた「取消」の出ない人 (現場の人と事務の人の形) に、
頭の下の青の 1 行で出ない訳と頼む先を出す。フルコントロールの人と、判定が log の会社と、取消の指示書には出さない。
Django の test client で HTML を見る (本物のブラウザは使わない)。名前は作り物だけ。
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou import cli
from seizou.accounts import services as accounts_services
from seizou.core import access
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services
from tests.test_core_access import account

ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS = (
    ROOT / "src" / "seizou" / "core" / "manual_default" / "workorders_workorder_detail.html",
    ROOT / "examples" / "companies" / "a_machining" / "manual" / "workorders_workorder_detail.html",
    ROOT / "examples" / "companies" / "b_plating" / "manual" / "workorders_workorder_detail.html",
)
NEW_LINE = (
    "<li>「取消」で、発行の指示書を取り消す。実績の入った指示書は取り消せない。"
    "権限の設定によっては、「取消」が出ない人がいる。要るときはアカウント管理の担当者に頼む。</li>"
)
OLD_LINE = "<li>「取消」で指示書を取り消す。実績の入った指示書は取り消せない。</li>"
HTMX = {"HTTP_HX_REQUEST": "true"}


def test_fragments_say_some_people_have_no_cancel():
    """3 本はバイトで同じ。新しい行が 1 回ずつ、古い行は 0 件。「できること」の li は 4 つのまま。"""
    blobs = [path.read_bytes() for path in FRAGMENTS]
    assert blobs[0] == blobs[1] == blobs[2]
    for path in FRAGMENTS:
        text = path.read_text(encoding="utf-8")
        assert text.count(NEW_LINE) == 1, path
        assert OLD_LINE not in text, path
        can_do = re.search(r"<h3>できること</h3>\s*<ul>(.*?)</ul>", text, re.S).group(1)
        # 「できること」の行は 5 つ (取消の行を置き換えただけで数は変えない。上限 6 = test_examples_manual の test_fragment_shape)
        assert can_do.count("<li>") == 5, path


@pytest.mark.django_db
def test_modal_shows_contact_after_copy(client, settings, tmp_path):
    """A 社の種を置き場に写した後、「?」の小窓 (HX-Request) の中身に頼む先が出る。"""
    settings.BASE_DIR = tmp_path
    user = get_user_model().objects.create_user(username="tester", password="tests-only")
    client.force_login(user)
    cli.copy_manual(ROOT / "examples" / "companies" / "a_machining", tmp_path)

    response = client.get(reverse("core:manual_fragment", args=["workorders_workorder_detail"]), **HTMX)

    assert response.status_code == 200
    html = response.content.decode()
    assert "アカウント管理の担当者に頼む" in html
    assert "権限の設定によっては、「取消」が出ない人がいる。" in html


# ---------------------------------------------------------------- 2 = 詳細の頭の下の青の 1 行 (V17・U19)

NOTICE = re.compile(r'<div class="alert alert-info alert-soft mb-4" role="note">(?:(?!</div>).)*取消は、(?:(?!</div>).)*</div>', re.S)
NOTICE_TEXT = "取消は、指示書の編集の権限があり、部署で指示書を使う人にだけ出ます。"
MORE_MENU = "data-detail-more"
#: 部署と編集の判定を on にした会社 (金額の判定は既定のまま)
ON = {"department": "on", "write": "on"}
LOG = {"department": "log", "write": "log"}


@pytest.fixture
def workorder(user):
    """発行 (未着手) の指示書 1 枚。受注の明細 1 行から 10 個、工程 1 つ。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    cut = masters_services.create_process({"code": "CUT", "name": "切断", "sort_order": 1}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )
    return workorders_services.issue_workorder(
        order.lines.get(), Decimal("10"), [{"process": cut, "planned_qty": Decimal("10")}], user
    )


def genba():
    """現場の人の形: 部署が製造 (指示書・実績・在庫・部品マスター)、編集の権限なし。"""
    department = accounts_services.create_department("作り物の製造の部署", ["workorders", "progress", "stock", "masters"], [], None)
    return account("seisaku", departments=[department])


def jimu():
    """事務の人の形: 部署が受注と出荷 (指示書が無い)、伝票編集の権限あり (tests/test_core_department_buttons の jimu と同じ)。"""
    department = accounts_services.create_department(
        "作り物の受注と出荷の部署", ["orders", "intake", "connect", "shipping", "purchasing", "stock", "masters"], ["orders", "shipping"], None
    )
    return account("hanbai", departments=[department], permissions=["slip_edit"])


def admin():
    """比べの形: フルコントロール。"""
    return account("kanrisha", full_control=True)


def detail(client, person, workorder):
    client.force_login(get_user_model().objects.get(pk=person.pk))
    response = client.get(reverse("workorders:workorder_detail", args=[workorder.pk]))
    assert response.status_code == 200
    return response.content.decode()


def cancel_href(workorder):
    return f'href="{reverse("workorders:workorder_cancel", args=[workorder.pk])}"'


@pytest.mark.django_db
def test_genba_sees_reason_and_contact_instead_of_cancel(client, settings, workorder):
    settings.SEIZOU = {"access_control": dict(ON)}
    body = detail(client, genba(), workorder)

    assert MORE_MENU not in body
    assert cancel_href(workorder) not in body
    (notice,) = NOTICE.findall(body)
    assert NOTICE_TEXT in notice
    assert access.CONTACT in notice
    assert "href" not in notice
    # 置き場は #workorder-live の中の頭 (タブの並びより上。小窓で保存した後の取り直しでも描き直す)
    live = body.index('<div id="workorder-live">')
    assert live < body.index(notice) < body.index('role="tablist"', live)


@pytest.mark.django_db
def test_jimu_sees_the_same_notice(client, settings, workorder):
    settings.SEIZOU = {"access_control": dict(ON)}
    body = detail(client, jimu(), workorder)

    assert cancel_href(workorder) not in body
    assert len(NOTICE.findall(body)) == 1


@pytest.mark.django_db
def test_admin_keeps_more_and_cancel_without_notice(client, settings, workorder):
    settings.SEIZOU = {"access_control": dict(ON)}
    body = detail(client, admin(), workorder)

    assert MORE_MENU in body
    assert body.count(cancel_href(workorder)) == 1
    assert "text-error" in body[body.index(MORE_MENU) :]
    assert NOTICE.findall(body) == []
    assert NOTICE_TEXT not in body


@pytest.mark.django_db
def test_log_company_shows_cancel_to_genba(client, settings, workorder):
    settings.SEIZOU = {"access_control": dict(LOG)}
    body = detail(client, genba(), workorder)

    assert MORE_MENU in body
    assert body.count(cancel_href(workorder)) == 1
    assert NOTICE_TEXT not in body


@pytest.mark.django_db
def test_cancelled_workorder_has_no_notice(client, settings, user, workorder):
    settings.SEIZOU = {"access_control": dict(ON)}
    workorders_services.cancel_workorder(workorder, user, reason="作り物の取消")
    for person in (genba(), admin()):
        body = detail(client, person, workorder)
        assert NOTICE_TEXT not in body, person.username
        assert cancel_href(workorder) not in body, person.username
