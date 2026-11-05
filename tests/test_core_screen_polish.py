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


# ---------------------------------------------------------------- 6. マニュアルの最上位の文

ROOT_EMPTY = "このフォルダの直下にはマニュアルがありません。フォルダを開いてください。"
NONE_YET = "まだ 1 件もありません。"


def test_manual_root_with_folders_says_open_folder(client, user):
    from seizou.manuals import services as manuals_services

    folder = manuals_services.create_folder(None, "段取り", "blue", user)
    manuals_services.create_manual(folder, "段取りの手順", "steps", user)
    client.force_login(user)
    url = reverse("manuals:manual_list")

    html = client.get(url).content.decode()
    assert ROOT_EMPTY in html
    assert NONE_YET not in html

    partial = client.get(url, **HTMX).content.decode()
    assert ROOT_EMPTY in partial
    assert NONE_YET not in partial
    assert 'hx-swap-oob' in partial


def test_manual_root_without_folders_says_none_yet(client, user):
    client.force_login(user)
    html = client.get(reverse("manuals:manual_list")).content.decode()
    assert NONE_YET in html
    assert ROOT_EMPTY not in html


def test_manual_search_miss_keeps_core_sentence(client, user):
    from seizou.manuals import services as manuals_services

    folder = manuals_services.create_folder(None, "段取り", "blue", user)
    manuals_services.create_manual(folder, "段取りの手順", "steps", user)
    client.force_login(user)
    html = client.get(reverse("manuals:manual_list"), {"q": "当たらない言葉"}).content.decode()
    assert "に合う物はありません。" in html
    assert ROOT_EMPTY not in html


# ---------------------------------------------------------------- 2. 取消の確かめの画面の案内と釦

SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
REQUIRED_GUIDE = "「必須」の欄を埋めて"


def _shipment(user, order, qty="4"):
    from seizou.shipping import services as shipping_services

    return shipping_services.ship(order.lines.get(), Decimal(qty), SHIPPED_AT, user)


def _delivery_note(user, order, shipments):
    from seizou.shipping import services as shipping_services

    return shipping_services.create_delivery_note(order.customer, shipments, user)


def _invoice(user, order):
    from seizou.billing import services as billing_services

    shipment = _shipment(user, order)
    _delivery_note(user, order, [shipment])
    shipment.refresh_from_db()
    return billing_services.create_invoice(order.customer, [shipment], user, issued_on=ISSUED_ON)


def test_cancel_confirm_screens_say_cancel(client, user, order):
    from seizou.billing import services as billing_services

    invoice = _invoice(user, order)
    # 入金は別の請求書に記録する (入金の残る請求書の取消の確かめの画面は「取り消す」を出さない = N89)。
    payment = billing_services.record_payment(_invoice(user, order), ISSUED_ON, Decimal("1000"), user)
    client.force_login(user)
    for url in (
        reverse("orders:order_cancel", args=[order.pk]),
        reverse("billing:invoice_cancel", args=[invoice.pk]),
        reverse("billing:payment_cancel", args=[payment.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        html = response.content.decode()
        assert "取り消す</button>" in html, url
        assert REQUIRED_GUIDE not in html, url
        assert "保存</button>" not in html, url


def test_create_screen_keeps_save_guide(client, user):
    client.force_login(user)
    html = client.get(reverse("masters:item_create")).content.decode()
    assert "「必須」の欄を埋めて「保存」を押します。" in html
    assert "保存</button>" in html


def test_has_required_filter():
    from seizou.billing.forms import InvoiceCancelForm, PaymentForm
    from seizou.core.templatetags.seizou_tags import has_required
    from seizou.orders.forms import OrderCancelForm

    assert has_required(InvoiceCancelForm()) is False
    assert has_required(OrderCancelForm()) is False
    assert has_required(PaymentForm()) is True


# ---------------------------------------------------------------- 8. 次の一手の釦


def _selected_option(html, name, value):
    """select の name の欄で、value の option に selected があるか。"""
    import re

    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert select, f"{name} の選ぶ欄が無い"
    return re.search(rf'<option value="{value}"[^>]*\bselected\b', select.group(1)) is not None


def test_shipment_detail_links_to_delivery_note(client, user, order):
    import re

    shipment = _shipment(user, order)
    client.force_login(user)
    detail_url = reverse("shipping:shipment_detail", args=[shipment.pk])
    html = client.get(detail_url).content.decode()
    link = f'{reverse("shipping:deliverynote_create")}?shipments={shipment.pk}'
    # 開いた詳細の道を ?next= で渡す (納品書作成の「キャンセル」で出荷の詳細へ戻る)
    assert f'<a class="btn btn-outline border-base-300" href="{link}&amp;next={detail_url}">' in html
    assert "納品書作成" in html
    assert html.count("btn-primary") == 1  # 主色は「編集」の 1 つのまま

    form = client.get(link).content.decode()
    checkbox = re.search(rf'<input type="checkbox" name="shipments" value="{shipment.pk}"[^>]*>', form)
    assert checkbox and "checked" in checkbox.group(0)


def test_shipment_in_issued_note_has_no_delivery_note_button(client, user, order):
    shipment = _shipment(user, order)
    _delivery_note(user, order, [shipment])
    client.force_login(user)
    html = client.get(reverse("shipping:shipment_detail", args=[shipment.pk])).content.decode()
    assert "納品書作成" not in html


def test_delivery_note_detail_links_to_invoice(client, user, order):
    from seizou.shipping import services as shipping_services

    note = _delivery_note(user, order, [_shipment(user, order)])
    client.force_login(user)
    detail_url = reverse("shipping:deliverynote_detail", args=[note.pk])
    html = client.get(detail_url).content.decode()
    link = f'{reverse("billing:invoice_create")}?customer={order.customer_id}&amp;next={detail_url}'
    assert f'href="{link}"' in html
    assert f'<a class="btn btn-outline border-base-300" href="{link}">' in html
    assert "請求書作成" in html
    assert "btn-primary" not in html  # 納品書には「編集」が無い。足した釦も主色にしない

    form = client.get(link).content.decode()
    assert _selected_option(form, "customer", order.customer_id)

    shipping_services.cancel_delivery_note(note, user)
    assert "請求書作成" not in client.get(detail_url).content.decode()


# ---------------------------------------------------------------- 9. AI チャットの鍵なしの文

NO_KEY_FOR_ALL = "AI チャットは、管理者が設定すると使えます。"


@pytest.mark.parametrize("who", ["user", "manager"])
def test_assistant_panel_without_key(request, client, monkeypatch, who):
    from seizou.assistant import services as assistant_services

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client.force_login(request.getfixturevalue(who))
    html = client.get(reverse("core:home")).content.decode()
    assert NO_KEY_FOR_ALL in html
    assert 'name="question"' not in html
    if who == "manager":
        assert assistant_services.MESSAGE_NO_KEY in html
    else:
        assert "ANTHROPIC_API_KEY" not in html


# ---------------------------------------------------------------- 4. 数の字の末尾の 0


def test_comma_drops_trailing_zeros():
    from seizou.core.templatetags.seizou_tags import comma

    assert comma(Decimal("10.00")) == "10"
    assert comma(Decimal("1234.50")) == "1,234.5"
    assert comma(Decimal("12000.00")) == "12,000"
    assert comma(Decimal("0.00")) == "0"
    assert comma(Decimal("-2.00")) == "-2"
    assert comma(Decimal("-0.00")) == "0"
    assert comma(Decimal("1E+3")) == "1,000"
    assert comma(1200) == "1,200"
    assert comma(None) == ""
    assert comma(Decimal("10"), 2) == "10.00"  # 小数の桁を決める形は今のまま


def test_order_detail_quantity_without_trailing_zeros(client, user, order):
    client.force_login(user)
    html = client.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert ">10</td>" in html
    assert ">10.00</td>" not in html
    assert ">1,200</td>" in html  # 単価 1200.00
