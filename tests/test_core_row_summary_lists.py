"""発注・出荷・請求書の一覧の行の「要点を見る」(目の印) と、その小窓 (受け渡しの seizou-部品の6案-20261107/指示書.md の 6。本人の決め 11-07 18:2x〜18:4x「5はmodalでいいかな」)。

受注と指示書の一覧 (tests/test_core_row_summary.py。401 回目) と同じ形を、発注の一覧 (PurchaseOrderModalView)・出荷の一覧 (ShipmentModalView)・
請求書の一覧 (InvoiceModalView) に付けた。行の右に目の印の link (core/_row_summary_link.html) があり、押すと小窓 (#seizou-modal) に要点と
「詳細を見る」が出る。読むだけ。同じ app のほかの一覧 (受入・納品書) には付けない。
作り物のデータは services で作る。名前と番号は作り物。HTTP は外へ出さない (つながりは作らない)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.core import edi_codes
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrderLine
from seizou.shipping import services as shipping_services

HTMX = {"HTTP_HX_REQUEST": "true"}
HEADER = '<th class="whitespace-nowrap font-medium">要点</th>'
MONEY_ONLY = {"access_control": {"department": "off", "write": "off", "money": "on"}}


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def purchase_order(user, subcontractor, shaft):
    lines = [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)}]
    po = purchasing_services.create_purchase_order(subcontractor, lines, user, order_date=date(2026, 11, 2), send=False)
    purchasing_services.receive(po.lines.get(), Decimal("4"), date(2026, 11, 10), user)
    return po


def eye_links(html: str) -> list[tuple[str, str]]:
    """目の印の link の (href = 詳細, hx-get = 小窓) の並び。"""
    return re.findall(r'<a class="btn btn-ghost btn-xs" href="([^"]+)" hx-get="([^"]+)"[^>]*aria-label="要点を見る"', html)


def dt(label: str) -> str:
    """小窓の項目の見出しの字 (core/_modal.html と同じ形)。"""
    return f'<dt class="text-base-content/70">{label}</dt>'


def screen(url_name):
    from seizou.core.screens import all_screens

    return next(s for s in all_screens() if s.url_name == url_name)


# ---- 発注の一覧と小窓 ----


def po_modal(client, po):
    return client.get(reverse("purchasing:purchaseorder_modal", args=[po.pk]), **HTMX)


def test_purchaseorder_list_rows_have_summary_link(logged_in, purchase_order):
    html = logged_in.get(reverse("purchasing:purchaseorder_list")).content.decode()

    assert html.count(HEADER) == 1
    expected = [(f"/purchasing/orders/{purchase_order.pk}/", f"/purchasing/orders/{purchase_order.pk}/modal/")]
    assert eye_links(html) == expected
    link = re.search(r'<a class="btn btn-ghost btn-xs"[^>]*aria-label="要点を見る"[^>]*>', html).group(0)
    assert 'hx-target="#seizou-modal-box"' in link and 'hx-swap="innerHTML"' in link and "showModal()" in link
    # 行の番号の link (詳細へ移る) と、2 秒ごとの更新の状態の字 (data-live) はそのまま
    assert f'href="/purchasing/orders/{purchase_order.pk}/" title="{purchase_order.po_no}"' in html
    assert "data-live=" in html
    # htmx の行の取り直し (2 秒ごとの更新と同じ要求) でも同じ
    partial = logged_in.get(reverse("purchasing:purchaseorder_list"), **HTMX).content.decode()
    assert partial.count(HEADER) == 1 and eye_links(partial) == expected


def test_receipt_list_is_unchanged(logged_in, purchase_order):
    html = logged_in.get(reverse("purchasing:receipt_list")).content.decode()

    assert purchase_order.po_no in html  # 受入の行は出ている
    assert HEADER not in html and eye_links(html) == []


def test_purchaseorder_modal_shows_summary_and_detail_link(logged_in, purchase_order):
    response = po_modal(logged_in, purchase_order)
    html = response.content.decode()

    assert response.status_code == 200 and "<html" not in html
    # 発注日は土台の項目の字 (Django の日付の形)、納期は札と明細の表の字
    for word in (purchase_order.po_no, "作り物の協力企業", "2026年11月2日", "2026-11-20", "T-100", "作り物の軸", "10 個", "¥1,000"):
        assert word in html, word
    # 頭の札 (協力企業・一番早い納期・合計) を項目の先頭に並べる
    assert dt("協力企業") in html and dt("一番早い納期") in html and dt("合計") in html
    # 状態は題の横の印 (項目の「状態」は出さない)。取引先は札の「協力企業」と同じなので項目に出さない
    assert "下書き</span>" in html and dt("状態") not in html and dt("取引先") not in html
    # 明細の受入の数
    assert ">受入の数</th>" in html and '<td class="whitespace-nowrap text-right tabular-nums">4</td>' in html
    assert f'href="/purchasing/orders/{purchase_order.pk}/">詳細を見る' in html
    # 発注には編集の口が無い (「編集」は出ない)
    assert html.count("btn-primary") == 1 and "編集</a>" not in html


def test_purchaseorder_modal_skips_cancelled_lines(logged_in, user, subcontractor, shaft):
    other = masters_services.create_item({"part_no": "T-200", "name": "作り物の座金", "unit": "個"}, user)
    lines = [
        {"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100")},
        {"item": other, "qty": Decimal("3"), "unit_price": Decimal("50")},
    ]
    po = purchasing_services.create_purchase_order(subcontractor, lines, user, order_date=date(2026, 11, 2), send=False)
    # 2 行目を取り消した明細 (明細状態コード 2) にする (送った発注の変更が行を残して付ける印と同じ)
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=2).update(line_status_code=edi_codes.LINE_DELETED)

    html = po_modal(logged_in, po).content.decode()

    assert "T-100" in html and "T-200" not in html
    assert html.count("<tr>") == 1
    assert "¥1,000" in html and "¥1,150" not in html  # 合計の札も取り消した明細を数えない


def test_purchaseorder_modal_lists_first_five_lines(logged_in, user, subcontractor, shaft):
    lines = [{"item": shaft, "qty": Decimal(n), "unit_price": Decimal("10")} for n in range(1, 8)]
    po = purchasing_services.create_purchase_order(subcontractor, lines, user, order_date=date(2026, 11, 2), send=False)

    html = po_modal(logged_in, po).content.decode()

    assert html.count("<tr>") == 5
    assert "先頭の 5 行を出しています (全 7 行)" in html


def test_purchaseorder_modal_hides_money_without_permission(settings, logged_in, purchase_order):
    settings.SEIZOU = MONEY_ONLY

    html = po_modal(logged_in, purchase_order).content.decode()

    assert "¥" not in html and "金額" not in html and dt("合計") not in html
    assert "T-100" in html and "10 個" in html and "作り物の協力企業" in html


def test_purchaseorder_modal_without_htmx_goes_to_detail(logged_in, purchase_order):
    response = logged_in.get(reverse("purchasing:purchaseorder_modal", args=[purchase_order.pk]))

    assert response.status_code == 302 and response["Location"] == f"/purchasing/orders/{purchase_order.pk}/"


def test_purchaseorder_modal_guards(client, logged_in, purchase_order):
    assert logged_in.get("/purchasing/orders/999999/modal/", **HTMX).status_code == 404

    client.logout()
    response = client.get(reverse("purchasing:purchaseorder_modal", args=[purchase_order.pk]), **HTMX)
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_purchaseorder_modal_url_and_screen():
    assert reverse("purchasing:purchaseorder_modal", args=[1]) == "/purchasing/orders/1/modal/"
    found = screen("purchasing:purchaseorder_modal")
    assert found.name == "発注の小窓" and "協力企業・日付・状態" in found.usecase


# ---- 出荷の一覧と小窓 ----


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def order(user, customer, shaft):
    lines = [{"item": shaft, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)}]
    return orders_services.create_order(customer, lines, user, order_date=date(2026, 11, 2), customer_order_no="PO-77")


@pytest.fixture
def shipment(user, order):
    return shipping_services.ship(order.lines.get(), Decimal("4"), date(2026, 11, 5), user)


def shipment_modal(client, shipment):
    return client.get(reverse("shipping:shipment_modal", args=[shipment.pk]), **HTMX)


def test_shipment_list_rows_have_summary_link(logged_in, shipment):
    html = logged_in.get(reverse("shipping:shipment_list")).content.decode()

    assert html.count(HEADER) == 1
    expected = [(f"/shipping/{shipment.pk}/", f"/shipping/{shipment.pk}/modal/")]
    assert eye_links(html) == expected
    assert f'href="/shipping/{shipment.pk}/" title="{shipment.shipment_no}"' in html
    # htmx の行の取り直しでも同じ
    partial = logged_in.get(reverse("shipping:shipment_list"), **HTMX).content.decode()
    assert partial.count(HEADER) == 1 and eye_links(partial) == expected


def test_deliverynote_list_is_unchanged(logged_in, user, customer, shipment):
    note = shipping_services.create_delivery_note(customer, [shipment], user)

    html = logged_in.get(reverse("shipping:deliverynote_list")).content.decode()

    assert note.note_no in html  # 納品書の行は出ている
    assert HEADER not in html and eye_links(html) == []


def test_shipment_modal_shows_summary_and_detail_link(logged_in, order, shipment):
    response = shipment_modal(logged_in, shipment)
    html = response.content.decode()

    assert response.status_code == 200 and "<html" not in html
    for word in (shipment.shipment_no, "作り物の得意先", "PO-77", f"{order.order_no}-1 T-100", "2026年11月5日"):
        assert word in html, word
    # 頭の札 (得意先・得意先の注文番号) を土台の項目の上に並べる
    assert html.index(dt("得意先")) < html.index(dt("得意先の注文番号")) < html.index(dt("出荷番号"))
    assert dt("数量") in html and dt("出荷日") in html and dt("納品書") in html
    # 金額は出さない
    assert "¥" not in html
    assert f'href="/shipping/{shipment.pk}/">詳細を見る' in html
    # 出荷には編集の口がある (書く権限のある人に「編集」)
    assert f'href="/shipping/{shipment.pk}/edit/">' in html and html.count("btn-primary") == 1


def test_shipment_modal_without_htmx_goes_to_detail(client, logged_in, shipment):
    response = logged_in.get(reverse("shipping:shipment_modal", args=[shipment.pk]))
    assert response.status_code == 302 and response["Location"] == f"/shipping/{shipment.pk}/"

    assert logged_in.get("/shipping/999999/modal/", **HTMX).status_code == 404
    client.logout()
    response = client.get(reverse("shipping:shipment_modal", args=[shipment.pk]), **HTMX)
    assert response.status_code == 302 and "/login/" in response["Location"]


def test_shipment_modal_url_and_screen():
    assert reverse("shipping:shipment_modal", args=[1]) == "/shipping/1/modal/"
    found = screen("shipping:shipment_modal")
    assert found.name == "出荷の小窓" and "得意先・得意先の注文番号" in found.usecase
