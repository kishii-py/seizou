"""画面の言葉と見た目の小さな直し (精査 A3・A1・A5 の所見)。Django の test client で HTML を見る。

設定の画面の幅・権限の選択肢の字・説明書が無い時の文・取消の受注の「編集」と R05 の文・マニュアルの最上位の文・
取消の確かめの画面の案内と釦・出荷と納品書の次の一手・AI チャットの鍵なしの文・数の字の末尾の 0。
品番と会社名は作り物。
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.core
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

User = get_user_model()

PASSWORD = "polish-tests-2611"
APP_CSS = Path(seizou.core.__file__).resolve().parent / "static" / "css" / "app.css"
ORDER_DATE = date(2026, 11, 2)


@pytest.fixture
def user(db):
    return User.objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def manager(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, item):
    """数量 10.00 の明細 1 行の受注。"""
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10.00"), "unit_price": Decimal("1200.00")}],
        user,
        order_date=ORDER_DATE,
    )


# ---------------------------------------------------------------- 1. 設定の画面の幅


def test_settings_system_rows_wrap_on_narrow_screen(client, manager):
    client.force_login(manager)
    html = client.get(reverse("core:settings")).content.decode()
    assert '<dd class="min-w-0 break-words">' in html
    assert '<dd class="min-w-0 break-words font-mono text-xs">' in html
    assert '<dd id="doctor-result" class="min-w-0 break-words">' in html
    assert '<dt class="whitespace-nowrap' not in html
    assert '<dt class="text-base-content/70 sm:whitespace-nowrap">' in html


def test_app_css_has_sm_nowrap():
    assert ".sm\\:whitespace-nowrap" in APP_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 7. 権限の選択肢の「つながり」


def test_kind_label_for_connections():
    from seizou.core import access

    assert access.KIND_LABELS["connections"] == "取引先との接続"


def test_access_form_shows_connections_label(client, manager):
    from django.apps import apps

    assert apps.is_installed("seizou.accounts")  # 試験の設定は全部の部品を入れる (tests/settings.py の PARTS)
    client.force_login(manager)
    response = client.get(reverse("accounts:access_create"))
    assert response.status_code == 200
    assert "取引先との接続" in response.content.decode()


# ---------------------------------------------------------------- 3. 説明書が無い時の文

SCREEN_NONE = "この画面の説明書はまだありません。管理者が作ります。"
COMPANY_NONE = "この会社の説明書はまだありません。管理者が作ります。"
MANAGER_CODE = "<code>/seizou:manual</code>"
HTMX = {"HTTP_HX_REQUEST": "true"}


def _manual_pages(client):
    """小窓 (「?」)・使い方・印刷用の 3 つの HTML。"""
    fragment = client.get(reverse("core:manual_fragment", args=["masters_item_list"]), **HTMX)
    page = client.get(reverse("core:manual"))
    printed = client.get(reverse("core:manual"), {"print": "1"})
    for response in (fragment, page, printed):
        assert response.status_code == 200
    return [response.content.decode() for response in (fragment, page, printed)]


@pytest.mark.parametrize("who", ["user", "manager"])
def test_missing_screen_manual_sentence(request, client, settings, tmp_path, who):
    settings.BASE_DIR = tmp_path
    (tmp_path / "manual").mkdir()
    client.force_login(request.getfixturevalue(who))
    for html in _manual_pages(client):
        assert SCREEN_NONE in html
        assert (MANAGER_CODE in html) is (who == "manager")
        assert "この画面の説明はまだありません" not in html


@pytest.mark.parametrize("who", ["user", "manager"])
def test_missing_company_manual_sentence(request, client, settings, tmp_path, who):
    settings.BASE_DIR = tmp_path
    client.force_login(request.getfixturevalue(who))
    fragment, page, printed = _manual_pages(client)
    assert SCREEN_NONE in fragment
    for html in (page, printed):
        assert COMPANY_NONE in html
        assert ("Claude Code で <code>/seizou:manual</code> を打つと" in html) is (who == "manager")


# ---------------------------------------------------------------- 5. 取消の受注の「編集」と R05 の文


def test_cancelled_order_detail_has_no_edit(client, user, order):
    client.force_login(user)
    edit_href = f'href="{reverse("orders:order_update", args=[order.pk])}"'
    detail_url = reverse("orders:order_detail", args=[order.pk])
    assert edit_href in client.get(detail_url).content.decode()

    orders_services.cancel_order(order, user)
    html = client.get(detail_url).content.decode()
    assert edit_href not in html
    assert "一覧に戻る" in html


def test_second_cancel_message(user, order):
    orders_services.cancel_order(order, user)
    with pytest.raises(RuleViolation) as caught:
        orders_services.cancel_order(order, user)
    assert caught.value.code == "R05"
    assert caught.value.message == "この受注は取り消し済みです。"
