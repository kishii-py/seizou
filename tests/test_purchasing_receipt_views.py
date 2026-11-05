"""受入の画面 (一覧と支払の見込みの表・新規登録・候補の口・発注の詳細の「受入を登録」・発注の一覧の受入の列)。

Django の test client で HTML を見る。つながりの無い協力企業で発注を作り、requests.post は偽の物に差し替える
(呼ばれないことを見る)。取引先と品目は作り物。日は timezone.localdate() から数える。
"""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, Receipt
from seizou.stock.models import StockMove


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
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
    ]


def _order(partner, user, *lines):
    """lines の 1 つは (品目, 数量, 単価)。"""
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(price)} for item, qty, price in lines],
        user,
        order_date=timezone.localdate(),
    )


@pytest.mark.django_db
def test_menu_and_list(user, client_logged_in, subcontractor, items, fake_post):
    po = _order(subcontractor, user, (items[0], "10", "25"))
    receipt = services.receive(po.lines.get(), Decimal("4"), timezone.localdate(), user, note="一部")

    home = client_logged_in.get(reverse("core:home")).content.decode()
    assert f'href="{reverse("purchasing:receipt_list")}"' in home
    section = home[home.index("協力企業管理") :]
    assert section.index(reverse("purchasing:purchaseorder_list")) < section.index(reverse("purchasing:receipt_list"))

    body = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    for word in ("受入日", "発注番号", "取引先", "部品", "数量", "金額", "備考"):
        assert word in body, word
    assert f'href="{reverse("purchasing:receipt_create")}"' in body
    assert f'href="{reverse("purchasing:receipt_detail", args=[receipt.pk])}"' in body
    assert po.po_no in body and "一部" in body


@pytest.mark.django_db
def test_create_from_order_detail_link(user, client_logged_in, subcontractor, items, fake_post):
    po = _order(subcontractor, user, (items[0], "10", "25"))
    line = po.lines.get()
    detail_url = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    create_url = f'{reverse("purchasing:receipt_create")}?line={line.pk}&amp;next={detail_url}'

    detail = client_logged_in.get(detail_url).content.decode()
    assert f'href="{create_url}"' in detail
    assert "受入の数" in detail and "受入の残" in detail

    response = client_logged_in.get(reverse("purchasing:receipt_create"), {"line": str(line.pk)})
    assert response.context["form"].initial["qty"] == Decimal("10")
    assert f'data-select-url="{reverse("purchasing:purchaseorderline_select")}"' in response.content.decode()

    response = client_logged_in.post(
        reverse("purchasing:receipt_create"),
        {"line": line.pk, "received_on": timezone.localdate().isoformat(), "qty": "4", "note": ""},
    )
    receipt = Receipt.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("purchasing:receipt_detail", args=[receipt.pk])
    assert StockMove.objects.count() == 1
    assert client_logged_in.get(reverse("purchasing:receipt_create"), {"line": str(line.pk)}).context["form"].initial[
        "qty"
    ] == Decimal("6")

    client_logged_in.post(
        reverse("purchasing:receipt_create"),
        {"line": line.pk, "received_on": timezone.localdate().isoformat(), "qty": "6", "note": ""},
    )
    detail = client_logged_in.get(detail_url).content.decode()
    assert "納品された" in detail
    assert "受入を登録" not in detail  # 残 0 の行に入口が無い
    assert fake_post.calls == 0


@pytest.mark.django_db
def test_select_returns_only_open_lines(user, client_logged_in, subcontractor, items, fake_post):
    po = _order(subcontractor, user, (items[0], "10", "25"), (items[1], "5", "10"))
    first, second = po.lines.order_by("line_no")
    services.receive(second, Decimal("5"), timezone.localdate(), user)
    cancelled = _order(subcontractor, user, (items[0], "3", "25"))
    services.cancel_purchase_order(cancelled, user)

    data = client_logged_in.get(reverse("purchasing:purchaseorderline_select"), {"q": "T-"}).json()
    texts = [item["text"] for item in data["items"]]

    assert texts == [f"{po.po_no}-1 T-100 作り物の軸"]
    assert all(cancelled.po_no not in text for text in texts)
    narrowed = client_logged_in.get(reverse("purchasing:purchaseorderline_select"), {"q": "T-200"}).json()
    assert narrowed["items"] == []


@pytest.mark.django_db
def test_order_list_shows_received_columns(user, client_logged_in, subcontractor, items, fake_post):
    po = _order(subcontractor, user, (items[0], "10", "25"))
    services.receive(po.lines.get(), Decimal("4"), timezone.localdate(), user)

    response = client_logged_in.get(reverse("purchasing:purchaseorder_list"))
    body = response.content.decode()

    assert "受入の数" in body and "受入の残" in body
    row = next(row for row in response.context["rows"] if row["pk"] == po.pk)
    values = [str(cell["value"]) for cell in row["cells"]]
    assert "4" in values or "4.00" in values
    assert "6" in values or "6.00" in values


@pytest.mark.django_db
def test_payable_table_and_money_hidden(user, client_logged_in, subcontractor, items, fake_post, settings):
    today = timezone.localdate()
    last_month = today.replace(day=1) - timedelta(days=1)
    po = _order(subcontractor, user, (items[0], "100", "25"))
    services.receive(po.lines.get(), Decimal("4"), last_month, user)  # 100
    services.receive(po.lines.get(), Decimal("2"), today, user)  # 50

    body = client_logged_in.get(reverse("purchasing:receipt_list")).content.decode()
    table = body[body.index('id="payable-title"') : body.index("</section>", body.index('id="payable-title"'))]
    assert "作り物の協力企業" in table
    assert f"先月 ({last_month.month} 月)" in table and f"今月 ({today.month} 月)" in table
    assert ">100<" in table and ">50<" in table
    assert "合計" in table

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    response = client_logged_in.get(reverse("purchasing:receipt_list"))
    body = response.content.decode()
    table = body[body.index('id="payable-title"') : body.index("</section>", body.index('id="payable-title"'))]
    assert ">100<" not in table and ">50<" not in table
    assert "—" in table
    assert all(row["cells"][5]["value"] == "—" for row in response.context["rows"])
    csv_text = client_logged_in.get(reverse("purchasing:receipt_list"), {"format": "csv"}).content.decode("utf-8")
    assert "金額" not in csv_text.lstrip("﻿").splitlines()[0]


@pytest.mark.django_db
def test_cancelled_order_refused_on_screen(user, client_logged_in, subcontractor, items, fake_post):
    po = _order(subcontractor, user, (items[0], "10", "25"))
    services.cancel_purchase_order(po, user)

    response = client_logged_in.post(
        reverse("purchasing:receipt_create"),
        {"line": po.lines.get().pk, "received_on": timezone.localdate().isoformat(), "qty": "4", "note": ""},
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert "alert-error" in body and "取り消した発注" in body
    assert Receipt.objects.count() == 0
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_CANCELLED


def _row_tds(body, pk):
    """一覧の行 (<tr id="row-<pk>">) の <td> の HTML の並び。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return re.findall(r"<td[^>]*>.*?</td>", match.group(0), re.S)


@pytest.mark.django_db
def test_order_list_marks_received_numbers_and_sorts(user, client_logged_in, subcontractor, items, fake_post):
    partial = _order(subcontractor, user, (items[0], "10", "25"))
    services.receive(partial.lines.get(), Decimal("4"), timezone.localdate(), user)  # 残 6
    over = _order(subcontractor, user, (items[1], "10", "25"))
    services.receive(over.lines.get(), Decimal("12"), timezone.localdate(), user)  # 残 −2 (発注より多くても止めない)
    url = reverse("purchasing:purchaseorder_list")

    response = client_logged_in.get(url)
    labels = [col.label for col in response.context["columns"]]
    received, remaining = labels.index("受入の数"), labels.index("受入の残")
    for index in (received, remaining):
        col = response.context["columns"][index]
        assert col.numeric and col.sortable, col.label
    body = response.content.decode()
    assert "text-error" in _row_tds(body, over.pk)[remaining]
    assert "text-error" not in _row_tds(body, partial.pk)[remaining]

    def values(sort, index):
        rows = client_logged_in.get(url, {"sort": sort}).context["rows"]
        return [Decimal(str(row["cells"][index]["value"])) for row in rows]

    assert values("receive_remaining", remaining) == [Decimal("-2"), Decimal("6")]
    assert values("-received_total", received) == [Decimal("12"), Decimal("4")]


@pytest.mark.django_db
def test_order_list_totals_follow_filter(user, client_logged_in, subcontractor, items, fake_post):
    partial = _order(subcontractor, user, (items[0], "10", "25"))
    services.receive(partial.lines.get(), Decimal("4"), timezone.localdate(), user)  # 受入 4・残 6
    over = _order(subcontractor, user, (items[1], "10", "25"))
    services.receive(over.lines.get(), Decimal("12"), timezone.localdate(), user)  # 受入 12・残 −2
    url = reverse("purchasing:purchaseorder_list")

    response = client_logged_in.get(url)
    labels = [col.label for col in response.context["columns"]]
    received, remaining = labels.index("受入の数"), labels.index("受入の残")
    totals = response.context["totals"]
    assert totals is not None
    assert Decimal(str(totals[received]["value"])) == Decimal("16") and totals[received]["numeric"]
    assert Decimal(str(totals[remaining]["value"])) == Decimal("4") and totals[remaining]["numeric"]
    # 合計に足すのは受入の数と残の 2 列だけ
    assert [index for index, cell in enumerate(totals) if cell["numeric"]] == [received, remaining]
    body = response.content.decode()
    assert "合計" in body[body.index("<tfoot>") : body.index("</tfoot>")]

    # 検索で絞ると合計が追従する (残 6 の発注だけ = 受入 4・残 6)
    totals = client_logged_in.get(url, {"q": partial.po_no}).context["totals"]
    assert Decimal(str(totals[received]["value"])) == Decimal("4")
    assert Decimal(str(totals[remaining]["value"])) == Decimal("6")
