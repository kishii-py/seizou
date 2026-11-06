"""発注の詳細の明細の「出荷の知らせ」の列と、出荷ごとの「受入を記録する」の入口 (purchasing)。EDI の 3 段目。

Django の test client で HTML を見る。つながりの無い協力企業で発注を作り、requests.post は呼んだら落とす物に差し替える。
出荷の知らせの行は connect.services.record_shipping_notice で作る (受け口を通さない近道)。取引先と品目は作り物。日は固定の値を渡す。
"""

import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine, Receipt

ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)


class FakePost:
    def __init__(self):
        self.calls = 0

    def __call__(self, url, data=None, headers=None, timeout=None):
        self.calls += 1
        raise AssertionError("つながりが無いのに HTTP を呼んだ")


@pytest.fixture
def fake_post(monkeypatch):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    return fake


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _order(partner, item, user, *, send=True):
    """数量 10 の明細が 1 行の発注 (納期あり = 納期の枡に「—」を出さない)。"""
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25"), "due_date": DUE_DATE}],
        user,
        order_date=ORDER_DATE,
        send=send,
    )


def _notice(line, qty, shipped_at, shipment_no):
    return connect_services.record_shipping_notice(
        line,
        Decimal(qty),
        shipped_at,
        None,
        message_id=uuid.uuid4(),
        shipment_no=shipment_no,
        supplier_order_no="SO-0001",
    )


def _lines_table(body):
    """発注の詳細の明細の表 (見出しの並びと、1 行目の枡の並び)。"""
    start = body.index('id="po-lines-title"')
    section = body[start : body.index("</section>", start)]
    heads = [re.sub(r"<[^>]+>", "", head) for head in re.findall(r"<th\b[^>]*>(.*?)</th>", section, re.S)]
    tbody = section[section.index("<tbody>") : section.index("</tbody>")]
    row = re.search(r"<tr>(.*?)</tr>", tbody, re.S).group(1)
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
    return heads, cells


def _notice_cell(body):
    heads, cells = _lines_table(body)
    assert len(heads) == len(cells)
    return cells[heads.index("出荷の知らせ")]


def _detail(client, po):
    return client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()


@pytest.mark.django_db
def test_shipping_notice_summary_and_line_property(user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user)
    line = po.lines.get()

    assert services.shipping_notice_summary(line) == {"rows": [], "count": 0, "total": 0, "total_text": "0"}

    later = _notice(line, "6", date(2026, 11, 7), "SH-0002")
    earlier = _notice(line, "4", date(2026, 11, 5), "SH-0001")
    summary = services.shipping_notice_summary(line)

    assert summary["count"] == 2
    assert summary["total"] == Decimal("10")
    # 出荷日の古い順
    assert summary["rows"] == [earlier, later]
    assert PurchaseOrderLine.objects.get(pk=line.pk).shipping_notice == summary


@pytest.mark.django_db
def test_order_detail_shows_notice_column_and_receipt_link(client_logged_in, user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user)
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")

    body = _detail(client_logged_in, po)

    heads, _cells = _lines_table(body)
    at = heads.index("受入の残")
    assert heads[at : at + 3] == ["受入の残", "出荷の知らせ", "納期"]
    cell = _notice_cell(body)
    assert "2026-11-05" in cell
    assert "×4" in cell
    assert "受入を記録する" in cell
    next_part = f'&amp;next={reverse("purchasing:purchaseorder_detail", args=[po.pk])}'
    link = f'{reverse("purchasing:receipt_create")}?line={line.pk}&amp;qty=4{next_part}'
    assert f'href="{link}"' in cell
    assert "合計" not in cell
    # 今までどおり明細の pk だけを渡す「受入を登録」もある
    assert f'href="{reverse("purchasing:receipt_create")}?line={line.pk}{next_part}"' in body
    assert "受入を登録" in body
    # 「受入を登録」は表の先頭の列 (表が箱より広い幅でも横に送らずに押せる。C22)
    heads, cells = _lines_table(body)
    assert heads[0] == "操作"
    assert "受入を登録" in cells[0]
    assert f'href="{reverse("purchasing:receipt_create")}?line={line.pk}{next_part}"' in cells[0]

    _notice(line, "6", date(2026, 11, 7), "SH-0002")
    cell = _notice_cell(_detail(client_logged_in, po))

    assert "合計" in cell
    assert "10" in cell[cell.index("合計") :]
    assert cell.count("受入を記録する") == 2


@pytest.mark.django_db
def test_order_detail_without_notices_shows_dash(client_logged_in, user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user)

    body = _detail(client_logged_in, po)

    assert "—" in _notice_cell(body)
    assert "受入を記録する" not in body


@pytest.mark.django_db
def test_receipt_link_hidden_when_remaining_is_zero_or_cancelled(
    client_logged_in, user, subcontractor, item, fake_post
):
    po = _order(subcontractor, item, user)
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")
    services.receive(line, Decimal("10"), date(2026, 11, 6), user)

    cell = _notice_cell(_detail(client_logged_in, po))

    assert "受入を記録する" not in cell
    assert "2026-11-05" in cell and "×4" in cell

    draft = _order(subcontractor, item, user, send=False)
    _notice(draft.lines.get(), "4", date(2026, 11, 5), "SH-0003")
    cancelled = services.cancel_purchase_order(draft, user)
    assert cancelled.status == PurchaseOrder.STATUS_CANCELLED

    cell = _notice_cell(_detail(client_logged_in, cancelled))

    assert "受入を記録する" not in cell
    assert "2026-11-05" in cell and "×4" in cell
    assert fake_post.calls == 0


def _input(html, name):
    return re.search(rf'<input[^>]*name="{name}"[^>]*>', html).group(0)


@pytest.mark.django_db
def test_receipt_create_opens_with_notice_values(client_logged_in, user, subcontractor, item, fake_post):
    po = _order(subcontractor, item, user)
    line = po.lines.get()
    _notice(line, "4", date(2026, 11, 5), "SH-0001")
    url = reverse("purchasing:receipt_create")

    response = client_logged_in.get(url, {"line": line.pk, "qty": "4", "received_on": "2026-11-05"})

    assert response.status_code == 200
    initial = response.context["form"].initial
    # 受入の残 (10) でなく出荷の数が入る
    assert initial["qty"] == "4"
    assert initial["received_on"] == "2026-11-05"
    html = response.content.decode()
    assert 'value="4"' in _input(html, "qty")
    assert 'value="2026-11-05"' in _input(html, "received_on")

    response = client_logged_in.post(url, {"line": line.pk, "qty": "4", "received_on": "2026-11-05", "note": ""})

    receipt = Receipt.objects.get()
    assert response.status_code == 302
    assert receipt.qty == Decimal("4")
    assert receipt.received_on == date(2026, 11, 5)

    # qty の無い GET は今までどおり受入の残 (10 − 4 = 6) が初期値
    plain = client_logged_in.get(url, {"line": line.pk})
    assert plain.context["form"].initial["qty"] == Decimal("6")
    assert fake_post.calls == 0
