"""請求書の一覧と発注の一覧の絞り (N120。土台は N117 の BaseListView.filters。本人の決め 11-04 15:0x)。

GET の名前は f_<name> (期間は f_<name>_from と f_<name>_to)。空と読めない値は絞らない。
請求書の「未入金だけ」(絞り込みの枠の印 f_unpaid=1。N127 で ?unpaid=1 から移した) は、状態の絞りに読める値があれば状態はそちらを使う。
発注の 2 秒ごとの更新の要求は HTTP_HX_REQUEST="true" と見出し X-Seizou-Live (tests/test_purchasing_live.py と同じ形)。
相手の会社に HTTP を送らない (send=False・つながりなし。requests.post は呼んだら落とす物に差し替える)。取引先と品目は作り物。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _partner(user, code, role):
    return masters_services.create_partner({"code": code, "name": f"作り物の取引先 {code}", "role": role}, user)


def _invoice(user, customer, item, issued_on):
    """出荷 1 つ (数量 2・単価 1,000) の請求書。"""
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("2"), "unit_price": Decimal("1000")}], user, order_date=date(2026, 10, 1)
    )
    shipment = shipping_services.ship(order.lines.get(), Decimal("2"), date(2026, 10, 2), user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return billing_services.create_invoice(customer, [shipment], user, issued_on=issued_on)


def _sent_order(user, partner, item, *dues):
    """納期ごとに明細 1 行 (数量 10・単価 100) の発注を作り、「送った」にする。"""
    lines = [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": due} for due in dues]
    po = purchasing_services.create_purchase_order(partner, lines, user, order_date=date(2026, 11, 2), send=False)
    return purchasing_services.mark_sent(po, user)


def _pks(response):
    return [row["pk"] for row in response.context["rows"]]


# --- 請求書の一覧 ---


@pytest.mark.django_db
def test_invoice_status_filter_takes_priority_over_unpaid(client_logged_in, user, item):
    customer = _partner(user, "C001", "customer")
    unpaid = _invoice(user, customer, item, date(2026, 10, 31))
    paid = _invoice(user, customer, item, date(2026, 10, 31))
    billing_services.record_payment(paid, date(2026, 11, 25), paid.total, user)
    cancelled = _invoice(user, customer, item, date(2026, 10, 31))
    billing_services.cancel_invoice(cancelled, user)
    url = reverse("billing:invoice_list")

    # 状態の絞りだけ: 入金の有無を見ない
    response = client_logged_in.get(url, {"f_status": "cancelled"})
    assert _pks(response) == [cancelled.pk]
    assert response.context["filter_active"] is True
    assert _pks(client_logged_in.get(url, {"f_status": "issued"})) == [paid.pk, unpaid.pk]
    # 「未入金だけ」だけ: 今までどおり、発行で取消でない入金の無い請求書
    assert _pks(client_logged_in.get(url, {"f_unpaid": "1"})) == [unpaid.pk]
    # 両方: 状態は絞りの値を使い、「未入金だけ」は入金の有無だけを見る
    assert _pks(client_logged_in.get(url, {"f_unpaid": "1", "f_status": "cancelled"})) == [cancelled.pk]
    assert _pks(client_logged_in.get(url, {"f_unpaid": "1", "f_status": "issued"})) == [unpaid.pk]
    # 状態の絞りの読めない値は空と同じ (「未入金だけ」が今までどおり発行に絞る)
    response = client_logged_in.get(url, {"f_unpaid": "1", "f_status": "作り物の字"})
    assert _pks(response) == [unpaid.pk]
    # 「未入金だけ」の印が入なので絞りは効いている (N127)。状態の読めない字は数えない
    assert response.context["filter_active"] is True
    assert [f["active"] for f in response.context["filters"]] == [False, False, False, True]


@pytest.mark.django_db
def test_invoice_issued_range_and_customer(client_logged_in, user, item):
    first = _partner(user, "C001", "customer")
    second = _partner(user, "C002", "both")
    early = _invoice(user, first, item, date(2026, 10, 31))
    middle = _invoice(user, second, item, date(2026, 11, 15))
    late = _invoice(user, first, item, date(2026, 11, 30))
    url = reverse("billing:invoice_list")

    body = client_logged_in.get(url).content.decode()
    for name in ("f_status", "f_customer", "f_issued_from", "f_issued_to"):
        assert f'name="{name}"' in body, name
    for label in ("状態", "得意先", "発行日"):
        assert f">{label}</label>" in body, label
    assert 'id="invoices-unpaid"' not in body  # 古い「すべて / 未入金だけ」の切り替えは無い (N127。印は枠の中)

    # 発行日の期間は両端を含み、片側だけでも絞れる
    response = client_logged_in.get(url, {"f_issued_from": "2026-11-15", "f_issued_to": "2026-11-30"})
    assert _pks(response) == [late.pk, middle.pk]
    assert response.context["paginator"].count == 2
    assert _pks(client_logged_in.get(url, {"f_issued_to": "2026-10-31"})) == [early.pk]
    # 得意先と発行日をかけ合わせる (AND)。「未入金だけ」とも重ねられる
    assert _pks(client_logged_in.get(url, {"f_customer": str(first.pk), "f_issued_from": "2026-11-01"})) == [late.pk]
    assert _pks(client_logged_in.get(url, {"f_unpaid": "1", "f_customer": str(second.pk)})) == [middle.pk]


# --- 発注の一覧 ---


@pytest.mark.django_db
def test_purchase_order_due_filter_lists_each_order_once(client_logged_in, user, item):
    partner = _partner(user, "S001", "subcontractor")
    both = _sent_order(user, partner, item, date(2026, 11, 10), date(2026, 11, 12))  # 明細 2 行とも期間の中
    one = _sent_order(user, partner, item, date(2026, 11, 11), date(2026, 11, 30))  # 1 行だけ期間の中
    _sent_order(user, partner, item, date(2026, 12, 1))  # 期間の外
    purchasing_services.receive(both.lines.order_by("line_no").first(), Decimal("4"), date(2026, 11, 3), user)
    url = reverse("purchasing:purchaseorder_list")

    response = client_logged_in.get(url, {"f_due_from": "2026-11-10", "f_due_to": "2026-11-12"})

    assert _pks(response) == [one.pk, both.pk]
    assert response.context["paginator"].count == 2
    # 合計の行も発注ごとに 1 回 (受入の数 4、受入の残 16 + 20)
    totals = {spec.name: cell["value"] for spec, cell in zip(response.context["columns"], response.context["totals"])}
    assert totals["received_total"] == Decimal("4")
    assert totals["receive_remaining"] == Decimal("36")
    # 並び替え (annotate の列) と CSV も同じ 2 件
    response = client_logged_in.get(url, {"f_due_from": "2026-11-10", "f_due_to": "2026-11-12", "sort": "receive_remaining"})
    assert _pks(response) == [both.pk, one.pk]
    csv_body = client_logged_in.get(url, {"f_due_from": "2026-11-10", "f_due_to": "2026-11-12", "format": "csv"}).content.decode("utf-8-sig")
    assert csv_body.count(both.po_no) == 1 and csv_body.count(one.po_no) == 1
    assert len(csv_body.strip().splitlines()) == 3  # 見出しと 2 行


@pytest.mark.django_db
def test_purchase_order_poll_keeps_filters_and_partner_choices(client_logged_in, user, item):
    first = _partner(user, "S001", "subcontractor")
    second = _partner(user, "S002", "both")
    customer_only = _partner(user, "C001", "customer")
    sent = _sent_order(user, first, item, date(2026, 11, 10))
    accepted = _sent_order(user, second, item, date(2026, 11, 10))
    url = reverse("purchasing:purchaseorder_list")

    body = client_logged_in.get(url).content.decode()
    for name in ("f_status", "f_partner", "f_due_from", "f_due_to"):
        assert f'name="{name}"' in body, name
    # 取引先の候補は、取引先の種類が協力企業か両方の取引先だけ (得意先だけの取引先は出ない)
    select = body[body.index('<select id="list-f-partner"') :]
    options = re.findall(r'<option value="([^"]*)"', select[: select.index("</select>")])
    assert options == ["", str(first.pk), str(second.pk)]
    # 2 秒ごとの更新の要素は今のまま (画面の今の URL の query を付けて取りに行く)
    assert 'id="purchaseorder-poll"' in body and "location.search" in body

    before = f"po-{sent.pk}:sent:0:0 po-{accepted.pk}:sent:0:0"
    purchasing_services.mark_accepted(accepted, user)
    # 2 秒ごとの更新と同じ要求に絞りの GET を付けると、同じ条件の行だけが返る
    response = client_logged_in.get(
        url,
        {"f_status": "accepted", "f_due_from": "2026-11-01", "sort": "-po_no"},
        HTTP_HX_REQUEST="true",
        HTTP_X_SEIZOU_LIVE=before,
    )
    rows = response.content.decode()
    assert "<html" not in rows
    assert f'<tr id="row-{accepted.pk}"' in rows
    assert f'<tr id="row-{sent.pk}"' not in rows
    assert f'data-live="po-{accepted.pk}:accepted:0:0"' in rows and "seizou-changed" in rows
    assert 'id="list-filter-clear"' in rows  # 「絞りを外す」の link (oob)
    links = [href for href in re.findall(r'hx-get="([^"]+)"', rows) if "sort=" in href]
    assert links
    for href in links:
        assert "f_status=accepted" in href and "f_due_from=2026-11-01" in href, href
    # 候補に無い取引先 (得意先だけ) の pk は絞らない (2 件とも出る)。候補の取引先なら 1 件
    response = client_logged_in.get(url, {"f_partner": str(customer_only.pk)}, HTTP_HX_REQUEST="true")
    assert sorted(_pks(response)) == sorted([sent.pk, accepted.pk])
    response = client_logged_in.get(url, {"f_partner": str(first.pk)}, HTTP_HX_REQUEST="true")
    assert _pks(response) == [sent.pk]
