"""請求書の一覧の「残のある物だけ」を、絞り込みの枠の中の印 (flag) にした形 (N127。土台は N122 の flag。本人の決め 11-04 17:3x)。

印の GET は f_unpaid (1 が入、0 が切、無いか読めない値は既定の切)。枠の欄から送ると隠しの欄の 0 とチェックの 1 の両方が届き、土台は後の 1 を読む。
古い切り替え (「すべて / 残のある物だけ」のボタンと ?unpaid=1) は無く、古い GET は読まない (別名を足さない)。
入金の有無は印の when が見る (取消でない入金が 1 つも無い請求書)。状態の絞りが空なら状態を発行に絞り、状態の絞りに読める値があれば状態はそちらを使う (N120 の形のまま)。
取引先と品目は作り物。相手の会社に HTTP を送らない (requests.post は呼んだら落とす物に差し替える)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services

BOX = 'name="f_unpaid" value="1" class="checkbox checkbox-sm"'
HIDDEN = '<input type="hidden" name="f_unpaid" value="0">'


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def invoices(user):
    """発行日の同じ請求書 5 つ (未入金・入金済み・一部入金・入金を取り消して未入金に戻った物・取消)。並びは作った順の逆。"""
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)

    def invoice():
        order = orders_services.create_order(
            customer, [{"item": item, "qty": Decimal("2"), "unit_price": Decimal("1000")}], user, order_date=date(2026, 10, 1)
        )
        shipment = shipping_services.ship(order.lines.get(), Decimal("2"), date(2026, 10, 2), user)
        shipping_services.create_delivery_note(customer, [shipment], user)
        shipment.refresh_from_db()
        return billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 10, 31))

    made = {name: invoice() for name in ("unpaid", "paid", "partial", "refunded", "cancelled")}
    billing_services.record_payment(made["paid"], date(2026, 11, 25), made["paid"].total, user)
    billing_services.record_payment(made["partial"], date(2026, 11, 25), Decimal("1000"), user)
    payment = billing_services.record_payment(made["refunded"], date(2026, 11, 25), made["refunded"].total, user)
    billing_services.cancel_payment(payment, user)
    billing_services.cancel_invoice(made["cancelled"], user)
    return made


def _pks(response):
    return [row["pk"] for row in response.context["rows"]]


def _newest_first(invoices, *names):
    return [invoices[name].pk for name in reversed(names)]


ALL = ("unpaid", "paid", "partial", "refunded", "cancelled")


@pytest.mark.django_db
def test_unpaid_flag_is_off_by_default_inside_the_frame(client_logged_in, invoices):
    url = reverse("billing:invoice_list")

    response = client_logged_in.get(url)

    # 既定は切 = 全部の請求書。「絞りを外す」は出さない
    assert _pks(response) == _newest_first(invoices, *ALL)
    assert response.context["filter_active"] is False
    body = response.content.decode()
    assert "絞りを外す" not in body
    # 印は絞り込みの枠の中 (キーワードの欄と同じ form。件数の select より前)。隠しの欄の 0 の後にチェックの 1
    box = f"{BOX} data-list-filter>残のある物だけ</label>"
    assert HIDDEN in body and box in body
    assert body.index('role="search"') < body.index(HIDDEN) < body.index(box) < body.index('id="list-per"')
    # 古い切り替えのボタンと隠しの欄と hx-include は無い
    for old in ('id="invoices-unpaid"', 'hx-include="#invoices-unpaid"', 'aria-label="出す物"', 'aria-current="true">すべて'):
        assert old not in body, old
    # 古い GET は読まない (別名を足さない)
    response = client_logged_in.get(url, {"unpaid": "1"})
    assert _pks(response) == _newest_first(invoices, *ALL)
    assert response.context["filter_active"] is False


@pytest.mark.django_db
def test_unpaid_flag_on_lists_only_unpaid_everywhere(client_logged_in, invoices):
    url = reverse("billing:invoice_list")
    unpaid = _newest_first(invoices, "unpaid", "partial", "refunded")  # 一部入金も「残のある物」(本人の決め 11-05 09:3x)

    response = client_logged_in.get(url, {"f_unpaid": "1"})

    # 入は、発行で残 (合計 − 取消でない入金の合計) のある請求書 (未入金・一部入金・入金を取り消した物。入金済みと取消は外れる)
    assert _pks(response) == unpaid
    assert response.context["paginator"].count == 3
    assert response.context["filter_active"] is True
    body = response.content.decode()
    assert f"{BOX} checked data-list-filter>残のある物だけ</label>" in body
    assert "絞りを外す" in body
    # 枠の欄から送った形 (0 と 1) は入、0 だけと読めない値は切 (既定)
    assert _pks(client_logged_in.get(url, {"f_unpaid": ["0", "1"]})) == unpaid
    assert _pks(client_logged_in.get(url, {"f_unpaid": "0"})) == _newest_first(invoices, *ALL)
    assert _pks(client_logged_in.get(url, {"f_unpaid": "作り物の字"})) == _newest_first(invoices, *ALL)
    # 「絞りを外す」の link は f_unpaid を外し、キーワードは残す
    response = client_logged_in.get(url, {"f_unpaid": ["0", "1"], "q": "C001"})
    assert "f_unpaid" not in response.context["query_without_filters"]
    assert "q=C001" in response.context["query_without_filters"]
    assert "f_unpaid=0&f_unpaid=1" in response.context["query_without_page"]
    # htmx の行の差し替えと CSV も同じ 3 件
    rows = client_logged_in.get(url, {"f_unpaid": "1"}, HTTP_HX_REQUEST="true")
    assert "<html" not in rows.content.decode()
    assert _pks(rows) == unpaid
    assert 'id="list-filter-clear"' in rows.content.decode()
    csv_body = client_logged_in.get(url, {"f_unpaid": "1", "format": "csv"}).content.decode("utf-8-sig")
    assert len(csv_body.strip().splitlines()) == 4  # 見出しと 3 行
    assert invoices["unpaid"].invoice_no in csv_body and invoices["paid"].invoice_no not in csv_body


@pytest.mark.django_db
def test_unpaid_flag_with_status_filter(client_logged_in, invoices):
    url = reverse("billing:invoice_list")

    # 状態の絞りだけ: 入金の有無を見ない
    assert _pks(client_logged_in.get(url, {"f_status": "issued"})) == _newest_first(invoices, "unpaid", "paid", "partial", "refunded")
    # 重ねたとき: 状態は絞りの値を使い、印は入金の有無だけを見る (N120 の形のまま)
    assert _pks(client_logged_in.get(url, {"f_unpaid": "1", "f_status": "issued"})) == _newest_first(invoices, "unpaid", "partial", "refunded")
    assert _pks(client_logged_in.get(url, {"f_unpaid": "1", "f_status": "cancelled"})) == [invoices["cancelled"].pk]
    # 状態の読めない値は空と同じ (印が状態を発行に絞る)
    response = client_logged_in.get(url, {"f_unpaid": "1", "f_status": "作り物の字"})
    assert _pks(response) == _newest_first(invoices, "unpaid", "partial", "refunded")
    assert [f["active"] for f in response.context["filters"]] == [False, False, False, True]
    # キーワードと得意先と発行日とも AND で重なる
    customer = invoices["unpaid"].customer_id
    params = {"f_unpaid": "1", "f_customer": str(customer), "f_issued_from": "2026-10-31", "q": invoices["refunded"].invoice_no}
    assert _pks(client_logged_in.get(url, params)) == [invoices["refunded"].pk]
