"""受注の編集で、画面を開いた後にほかの人か取引先の変更で直った受注を、古い画面の保存で黙って元に戻さないこと (N350。指摘 money-1)。

services.order_state_token (開いた時の受注と明細の字) と update_order の expected_state、
受注の編集の画面の隠しの欄 state_token (Django の test client で GET と POST。作り物の利用者でログイン) を見る。
得意先・部品・協力企業は作り物。得意先の fixture と、取引先の変更の受け入れの helper は tests/test_connect_revision_accept.py の物を使う。
取引先から受け入れる変更を通す試験では、相手の会社への HTTP は requests.post を偽の物に差し替える (同じファイルの fixture の fake)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.orders.models import Order
from seizou.purchasing.models import PurchaseOrderLine
from seizou.shipping import services as shipping_services
from tests.test_connect_revision_accept import (  # noqa: F401 (fixture)
    _accept_first,
    _revise,
    _rows,
    _send,
    _send_first,
    connections,
    customer,
    fake,
    items,
    order_secret,
    reply_secret,
    subcontractor,
)
from tests.test_orders_views import _management

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
HEX64 = re.compile(r"[0-9a-f]{64}")
# 右上の知らせは最初の文 (「保存できません。」) を太字の枠に分けて描くので、画面の字は 2 つに分けて探す
STALE_HEAD, STALE_REST = (part.strip() for part in services.STALE_ORDER_MESSAGE.split("。", 1))


def _refused_as_stale(body):
    return 'data-notice="stop"' in body and f"{STALE_HEAD}。" in body and STALE_REST in body


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-900", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, shaft):  # noqa: F811
    """明細 2 行 (数 10・単価 1180 と、数 5・単価 300) の受注。"""
    return services.create_order(
        customer,
        [
            {"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("1180")},
            {"item": shaft, "qty": Decimal("5"), "unit_price": Decimal("300")},
        ],
        user,
        order_date=ORDER_DATE,
    )


def _lines(order):
    return list(order.lines.order_by("line_no"))


def _change_qty(order, user, qty):
    """ほかの人の保存の役: 明細 1 の数だけを qty に直す (expected_state を渡さない)。"""
    first, second = _lines(order)
    services.update_order(order, {}, [{"id": first.pk, "qty": Decimal(qty)}, {"id": second.pk}], user)


# ---------------------------------------------------------------- (a) 字の作り方


@pytest.mark.django_db
def test_token_changes_with_qty_but_not_with_shipping_or_no_bill(user, order):
    token = services.order_state_token(order)
    assert HEX64.fullmatch(token)
    first, second = _lines(order)
    # 渡した明細の並びに依らない (画面は行番号の順で読んだ並びを渡す)
    assert services.order_state_token(order, [second, first]) == token

    # 出荷で受注の状態が変わっただけでは変わらない
    shipping_services.ship(first, Decimal("4"), SHIPPED_AT, user)
    order.refresh_from_db()
    assert order.status == Order.STATUS_PARTIAL
    assert services.order_state_token(order) == token
    # 請求しない印を付けただけでは変わらない
    services.mark_no_bill(first, user)
    assert services.order_state_token(order) == token

    _change_qty(order, user, "12")
    order.refresh_from_db()
    assert services.order_state_token(order) != token


@pytest.mark.django_db
def test_token_is_keyed_and_does_not_show_prices(settings, order):
    """字は鍵のある hmac (sha256 の 16 進 64 字)。単価の字を含まず、鍵が違えば字も違う (隠しの欄の値から単価を割り出せない)。"""
    token = services.order_state_token(order)
    assert HEX64.fullmatch(token)
    assert "1180.00" not in token and "1,180" not in token
    settings.SECRET_KEY = "seizou-tests-only-another-plain-key"
    assert services.order_state_token(order) != token


# ---------------------------------------------------------------- (b)〜(d) update_order の expected_state


@pytest.mark.django_db
def test_stale_token_refuses_without_writing(user, order):
    opened = services.order_state_token(order)
    _change_qty(order, user, "12")
    count = ChangeLog.objects.count()
    first, second = _lines(order)

    # 古い画面の保存の役: 数は開いた時の 10 のまま送り、備考だけ直す
    with pytest.raises(ValueError) as caught:
        services.update_order(
            order,
            {"note": "古い画面で書いた備考"},
            [{"id": first.pk, "qty": Decimal("10")}, {"id": second.pk}],
            user,
            expected_state=opened,
        )
    assert str(caught.value) == services.STALE_ORDER_MESSAGE
    first.refresh_from_db()
    order.refresh_from_db()
    assert first.qty == Decimal("12")
    assert order.note == ""
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_without_token_saves_as_before(user, order):
    """expected_state を渡さない呼び (取引先の変更の受け入れと同じ形) と空の字は、今のまま通る。"""
    services.order_state_token(order)
    _change_qty(order, user, "12")
    first, second = _lines(order)

    services.update_order(order, {"note": "渡さない"}, [{"id": first.pk}, {"id": second.pk}], user)
    order.refresh_from_db()
    assert order.note == "渡さない"
    services.update_order(order, {"note": "空の字"}, [{"id": first.pk}, {"id": second.pk}], user, expected_state="")
    order.refresh_from_db()
    assert order.note == "空の字"


@pytest.mark.django_db
def test_matching_token_saves(user, order):
    """開いてから誰も直していなければ、字が合って保存できる。"""
    opened = services.order_state_token(order)
    first, second = _lines(order)
    services.update_order(
        order,
        {"note": "開いてから誰も直していない"},
        [{"id": first.pk, "qty": Decimal("11")}, {"id": second.pk}],
        user,
        expected_state=opened,
    )
    first.refresh_from_db()
    order.refresh_from_db()
    assert first.qty == Decimal("11")
    assert order.note == "開いてから誰も直していない"


# ---------------------------------------------------------------- (e)〜(h) 受注の編集の画面 (隠しの欄 state_token)


def _token(client, order):
    """受注の編集を GET で開き、隠しの欄 state_token の値を返す (ブラウザで画面を開くのと同じ)。"""
    body = client.get(reverse("orders:order_update", args=[order.pk])).content.decode()
    tags = re.findall(r'<input[^>]*name="state_token"[^>]*>', body)
    assert len(tags) == 1
    assert 'type="hidden"' in tags[0]
    return re.search(r'value="([^"]*)"', tags[0]).group(1)


def _edit_post(order, token=None, *, note=None, qty=None):
    """受注の編集の画面が送る値 (今の表の値をそのまま欄に入れた形)。qty は {行番号: 数}、note は受注の備考。

    token が None なら state_token を送らない (隠しの欄の無い古い画面と、既にある試験と同じ形)。
    """
    order.refresh_from_db()
    rows = list(order.lines.order_by("line_no"))
    data = {
        "customer": str(order.customer_id),
        "order_date": order.order_date.isoformat(),
        "customer_order_no": order.customer_order_no,
        "note": order.note if note is None else note,
        **_management(len(rows), len(rows)),
    }
    for n, row in enumerate(rows):
        data.update(
            {
                f"lines-{n}-order_line": str(row.pk),
                f"lines-{n}-item": str(row.item_id),
                f"lines-{n}-customer_part_no": row.customer_part_no,
                f"lines-{n}-qty": str((qty or {}).get(row.line_no, row.qty)),
                f"lines-{n}-unit": row.unit_code,
                f"lines-{n}-price_basis": row.price_basis,
                f"lines-{n}-unit_price": str(row.unit_price),
                f"lines-{n}-quote_no": row.quote_no,
                f"lines-{n}-due_date": row.due_date.isoformat() if row.due_date else "",
                f"lines-{n}-note": row.note,
            }
        )
    if token is not None:
        data["state_token"] = token
    return data


@pytest.mark.django_db
def test_edit_screen_has_hidden_token_and_create_screen_does_not(logged_in, order):
    token = _token(logged_in, order)
    assert HEX64.fullmatch(token)
    assert token == services.order_state_token(order)
    assert 'name="state_token"' not in logged_in.get(reverse("orders:order_create")).content.decode()


@pytest.mark.django_db
def test_second_tab_save_is_refused_after_first_tab_changed_qty(logged_in, order):
    """同じ受注の編集を 2 つのタブで開き、1 つ目で数を直して保存した後、2 つ目で備考だけ直して保存すると断る。"""
    url = reverse("orders:order_update", args=[order.pk])
    first_tab = _token(logged_in, order)
    second_tab = _token(logged_in, order)
    # どちらのタブも、開いた時の値で欄が埋まっている (送る前に組む)
    first_post = _edit_post(order, first_tab, qty={1: "12"})
    second_post = _edit_post(order, second_tab, note="古いタブで書いた備考")

    assert logged_in.post(url, first_post).status_code == 302
    response = logged_in.post(url, second_post)
    assert response.status_code == 200
    assert _refused_as_stale(response.content.decode())
    first, _second = _lines(order)
    assert first.qty == Decimal("12")
    order.refresh_from_db()
    assert order.note == ""


@pytest.mark.django_db
def test_post_without_token_saves_as_before(logged_in, order):
    """state_token を送らない POST (隠しの欄の無い古い画面・既にある試験と同じ形) は今のまま保存できる。"""
    response = logged_in.post(reverse("orders:order_update", args=[order.pk]), _edit_post(order, qty={1: "12"}))
    assert response.status_code == 302
    first, _second = _lines(order)
    assert first.qty == Decimal("12")


@pytest.mark.django_db
def test_stale_save_after_accepting_partner_change_is_refused(fake, user, subcontractor, items, logged_in):  # noqa: F811
    """編集の画面を開いた後に取引先の第 2 版 (受信した注文の変更) を受け入れると、古い画面の保存は断る。受け入れた値は残る。"""
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    url = reverse("orders:order_update", args=[order.pk])
    opened = _token(logged_in, order)
    stale_post = _edit_post(order, opened, note="古い画面で書いた備考")

    # 第 2 版: 明細 1 の数 300 → 200、明細 4 を足す。明細 3 は消さずに残す (古い画面の明細が全部残り、保存が services まで届く形)
    revised = _revise(po, items)
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=3).update(line_status_code="39")
    _send(revised, user)
    _buyer, own = items
    connect_services.accept(Inbox.objects.get(revises=base), {"T-400": own[3]}, user)
    assert _rows(order)[1].qty == Decimal("200")

    response = logged_in.post(url, stale_post)
    assert response.status_code == 200
    assert _refused_as_stale(response.content.decode())
    rows = _rows(order)
    assert sorted(rows) == [1, 2, 3, 4]
    assert rows[1].qty == Decimal("200")
    order.refresh_from_db()
    assert "古い画面で書いた備考" not in order.note


@pytest.mark.django_db
def test_stale_save_after_accepting_change_that_removed_a_line_is_refused(fake, user, subcontractor, items, logged_in):  # noqa: F811
    """受け入れた第 2 版が明細を消したときも、古い画面の保存は通らない (消えた明細を指す欄で form が止める)。受け入れた値は残る。"""
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    url = reverse("orders:order_update", args=[order.pk])
    stale_post = _edit_post(order, _token(logged_in, order), note="古い画面で書いた備考")

    _send(_revise(po, items), user)  # 明細 1 の数 300 → 200、明細 3 を消す、明細 4 を足す
    _buyer, own = items
    connect_services.accept(Inbox.objects.get(revises=base), {"T-400": own[3]}, user)

    response = logged_in.post(url, stale_post)
    assert response.status_code == 200
    assert 'data-notice="stop"' in response.content.decode()
    rows = _rows(order)
    assert sorted(rows) == [1, 2, 4]
    assert rows[1].qty == Decimal("200")
    order.refresh_from_db()
    assert "古い画面で書いた備考" not in order.note


# ---------------------------------------------------------------- 3 金額の閲覧の無い人


@pytest.mark.django_db
def test_edit_form_does_not_open_for_user_without_money(settings, logged_in, order):
    """受注の変更は金額に関わる操作なので、金額の閲覧の無い人には、編集の画面を開いた時点で断る (本人の決め 11-12 07:2x)。

    直す前は、単価の欄の無い画面が開き、隠しの欄に鍵のある hmac の字 (金額を読めない形) を置いていた。開けない画面には、
    隠しの欄 state_token も単価の欄も出ない。字そのものが鍵のある hmac の 16 進 64 字である事は、services の関数で見る。
    """
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    response = logged_in.get(reverse("orders:order_update", args=[order.pk]))
    body = response.content.decode()
    assert response.status_code == 403
    assert "金額の閲覧の権限が無い方は" in body
    assert 'name="state_token"' not in body
    assert 'name="lines-0-unit_price"' not in body
    assert HEX64.fullmatch(services.order_state_token(order))
