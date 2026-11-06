"""金額の字の頭の「¥」(候補 C56。持ち主の決め 11-06 18:2x「全体的に頭に￥の方が見やすくない？」)。

core の seizou_tags の filter (yen・yen_if・without_yen_unit) と、値を描く dict の全部が持つ "money" の鍵と、
画面と印刷の金額の字を見る。作り物のデータだけを使う (請求書は tests/test_billing_views.py・部品は
tests/test_core_money_hidden.py・会社ごとの項目は tests/test_core_ux_fixes.py の形を写した)。
"""

import csv
import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.template.defaultfilters import floatformat
from django.template.loader import render_to_string
from django.urls import reverse

import seizou
from seizou.assistant import tools
from seizou.billing import services as billing_services
from seizou.core import access
from seizou.core import fields as company_fields
from seizou.core.templatetags.seizou_tags import comma, without_yen_unit, yen, yen_if
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.templatetags.orders_detail import order_aside
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.templatetags.purchasing_tags import purchase_order_aside
from seizou.shipping import services as shipping_services

SRC = Path(seizou.__file__).resolve().parent


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def no_registration(monkeypatch):
    """会社ごとの項目の登録を空から始める (ほかの試験の登録を残さない)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.fixture
def customer(user):
    # 取引先コードは数の形の字にする (金額の印が偽の行に ¥ が付かないことを、数の形の字で見る)。
    return masters_services.create_partner({"code": "5001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "7001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("480")}, user
    )


@pytest.fixture
def order(user, customer, item):
    """明細 1 行 (10 × 1,500 = 15,000。納期 11-20)。"""
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500"), "due_date": date(2026, 11, 20)}],
        user,
        order_date=date(2026, 11, 2),
    )


@pytest.fixture
def invoice(user, customer, order):
    """10 を出荷した請求書 (小計 15,000・消費税 1,500・合計 16,500)。入金はまだ無い。"""
    shipment = shipping_services.ship(order.lines.get(), Decimal("10"), date(2026, 11, 5), user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 30))


@pytest.fixture
def purchase_order(user, subcontractor, item):
    """「作成」の発注 (送らない)。明細 1 行 10 × 250 = 2,500 と、受入 4 (= 1,000)。"""
    po = purchasing_services.create_purchase_order(
        subcontractor,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("250"), "due_date": date(2026, 11, 9)}],
        user,
        order_date=date(2026, 11, 2),
        send=False,
    )
    purchasing_services.receive(po.lines.get(), Decimal("4"), date(2026, 11, 3), user)
    return po


def _body(client, url, **extra):
    response = client.get(url, **extra)
    assert response.status_code == 200, url
    return response, response.content.decode()


def _list_row_cells(body):
    """一覧の表の本体の枡 (td の中の字。タグを外した字)。"""
    tbody = body[body.index("<tbody") : body.index("</tbody>")]
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<td[^>]*>(.*?)</td>", tbody, re.S)]


# ---------------------------------------------------------------- (a)〜(f) filter


def test_yen_puts_mark_in_front():
    assert yen(Decimal("100450.00")) == "¥100,450"
    assert yen(Decimal("1234.50")) == "¥1,234.5"
    assert yen(1200) == "¥1,200"


def test_yen_minus_sign_goes_first():
    assert yen(Decimal("-1000")) == "-¥1,000"
    assert yen(Decimal("-0.00")) == "¥0"
    assert yen(Decimal("-0.4"), 0) == "¥0"


def test_yen_leaves_non_numbers():
    assert yen(None) == ""
    assert yen("") == ""
    assert yen(access.MASK) == access.MASK
    assert yen("abc") == "abc"
    assert yen("(読めません)") == "(読めません)"
    assert yen(True) is True


def test_yen_keeps_digits_and_text():
    assert yen(Decimal("1234.567"), 0) == "¥1,235"  # comma(…, 0) と同じ丸め
    assert yen(floatformat(Decimal("1234.50"), "-2g")) == "¥1,234.50"  # 印刷の字の数の部分を変えない
    assert yen("1234.50") == "¥1234.50"  # 読み取りの字に区切りを足さない


def test_yen_if_switches_by_money():
    assert yen_if(Decimal("1200"), True) == "¥1,200"
    assert yen_if(Decimal("1200"), False) == "1,200"
    assert yen_if(access.MASK, True) == access.MASK


def test_comma_has_no_yen():
    # 入力の欄の字 (C43) が comma を使うので、comma は ¥ を付けない。
    assert comma(Decimal("1200")) == "1,200"


# ---------------------------------------------------------------- (g) テンプレートの字


def test_templates_draw_values_through_yen_if():
    found = []
    for path in sorted(SRC.rglob("*.html")):
        source = path.read_text(encoding="utf-8")
        for word in ("cell.value|comma", "field.value|comma", "|comma:0 }} 円", "|comma }} 円", "(円)</th>"):
            if word in source:
                found.append(f"{path.relative_to(SRC)}: {word}")
    assert found == []


# ---------------------------------------------------------------- (i) 見出しの単位


def test_without_yen_unit():
    assert without_yen_unit("標準単価 (円)") == "標準単価"
    assert without_yen_unit("単価") == "単価"
    assert without_yen_unit(None) == ""


# ---------------------------------------------------------------- (h) 画面と印刷


@pytest.mark.django_db
def test_invoice_list_total_and_minus_balance(logged_in, user, invoice):
    _, body = _body(logged_in, reverse("billing:invoice_list"))
    assert "¥16,500" in _list_row_cells(body)

    billing_services.record_payment(invoice, date(2026, 12, 25), Decimal("17500"), user)  # 合計より 1,000 多い入金
    _, body = _body(logged_in, reverse("billing:invoice_list"))
    assert '<span class="text-error">-¥1,000</span>' in body


@pytest.mark.django_db
def test_item_detail_modal_and_list_show_yen(logged_in, item):
    response, detail = _body(logged_in, reverse("masters:item_detail", args=[item.pk]))
    assert "¥480" in detail and "標準単価</dt>" in detail and "標準単価 (円)</dt>" not in detail
    # 文脈の項目の名は「標準単価 (円)」のまま (描く所で外す)。
    assert any(field["label"] == "標準単価 (円)" for field in response.context["fields"])

    _, modal = _body(logged_in, reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true")
    assert "¥480" in modal and "標準単価</dt>" in modal and "標準単価 (円)</dt>" not in modal

    _, listing = _body(logged_in, reverse("masters:item_list"))
    heads = " ".join(re.findall(r"<th[^>]*>(.*?)</th>", listing, re.S))
    assert "標準単価" in heads and "標準単価 (円)" not in heads
    assert "¥480" in _list_row_cells(listing)


@pytest.mark.django_db
def test_item_csv_keeps_plain_numbers(logged_in, item):
    response = logged_in.get(reverse("masters:item_list") + "?format=csv")
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header, values = rows[0], rows[1]
    assert "標準単価 (円)" in header
    value = values[header.index("標準単価 (円)")]
    item.refresh_from_db()
    assert value == str(item.standard_price)
    assert "¥" not in value and "円" not in value


@pytest.mark.django_db
def test_receipt_list_amount_has_yen(logged_in, purchase_order):
    _, body = _body(logged_in, reverse("purchasing:receipt_list"))
    assert "¥1,000" in _list_row_cells(body)


@pytest.mark.django_db
def test_order_detail_head_chip_total_has_yen(logged_in, order):
    _, body = _body(logged_in, reverse("orders:order_detail", args=[order.pk]))
    chips = re.findall(r'<span class="text-base-content/70">([^<]+)</span><span class="font-medium tabular-nums">([^<]+)</span>', body)
    assert ("合計", "¥15,000") in chips


@pytest.mark.django_db
def test_input_screens_line_totals_have_yen(logged_in):
    for name in ("orders:order_create", "purchasing:purchaseorder_create"):
        _, body = _body(logged_in, reverse(name))
        assert "金額 ¥<span" in body, name


@pytest.mark.django_db
def test_invoice_print_total_has_yen(logged_in, invoice):
    _, body = _body(logged_in, reverse("billing:invoice_print", args=[invoice.pk]))
    assert '<td class="num">¥16,500</td>' in body
    assert "(円)" not in body


def test_extracted_lines_unit_price_has_yen():
    lines = [
        {"line_no": 1, "part_no": "A-100", "part_name": "作り物の軸", "qty": "10", "unit": "個", "unit_price": "120", "due_date": "2026-11-20"},
        {"line_no": 2, "part_no": "A-200", "part_name": "作り物の板", "qty": "5", "unit": "個", "unit_price": None, "due_date": "2026-11-20"},
    ]
    html = render_to_string("intake/_lines.html", {"object": SimpleNamespace(extracted={"lines": lines}), "seizou": {"hide_money": False}})
    assert ">¥120<" in html
    assert "¥<" not in html  # 単価の無い行は空 (¥ だけを出さない)


def test_assistant_order_summary_unit_price_has_yen():
    summary = tools.summarize(
        "create_order",
        {"customer_code": "C001", "order_date": "2026-11-06", "lines": [{"part_no": "WK-1001", "qty": "10", "unit_price": "480"}]},
    )
    assert summary[-1] == "1. WK-1001 × 10 (単価 ¥480)"
    unreadable = tools.summarize("create_order", {"customer_code": "C001", "order_date": "2026-11-06", "lines": [{"part_no": "WK-1001", "qty": "10"}]})
    assert unreadable[-1] == "1. WK-1001 × 10 (単価 (読めません))"


# ---------------------------------------------------------------- (j) dict の鍵 (無いとテンプレートが落ちる)


@pytest.fixture
def item_with_company_fields(item, no_registration, monkeypatch):
    company_fields.register(
        "masters.Item",
        "fields.ItemExt",
        [{"label": "材質の印", "in_list": True}, {"label": "膜厚の印", "in_list": True}],
    )
    values = {"ext01": "作り物の材質", "ext02": Decimal("12.5")}
    monkeypatch.setattr(company_fields, "ext_value", lambda obj, name: values.get(name, "x"))
    return item


@pytest.mark.django_db
def test_detail_and_modal_rows_all_have_money_key(logged_in, item_with_company_fields):
    item = item_with_company_fields
    response, detail = _body(logged_in, reverse("masters:item_detail", args=[item.pk]))
    assert all("money" in field for field in response.context["fields"])
    assert {field["label"] for field in response.context["fields"] if field["money"]} == {"標準単価 (円)"}
    assert "作り物の材質" in detail and "12.5" in detail and "¥12.5" not in detail

    response, modal = _body(logged_in, reverse("masters:item_modal", args=[item.pk]), HTTP_HX_REQUEST="true")
    assert all("money" in field for field in response.context["fields"])
    assert "作り物の材質" in modal and "12.5" in modal and "¥12.5" not in modal


@pytest.mark.django_db
def test_order_and_purchase_order_aside_rows_all_have_money_key(logged_in, order, purchase_order):
    for name, obj, aside_of in (
        ("orders:order_detail", order, order_aside),
        ("purchasing:purchaseorder_detail", purchase_order, purchase_order_aside),
    ):
        response, body = _body(logged_in, reverse(name, args=[obj.pk]))
        rows = [row for card in aside_of(obj, response.context["fields"])["cards"] for row in card["rows"]]
        assert all("money" in row for row in rows), name
        assert [row["label"] for row in rows if row["money"]] == ["明細の合計"], name
        # 右の列の「取引先コード」(数の形の字) は ¥ 無しで出る。
        aside = body[body.index('<aside id="detail-aside"') : body.index("</aside>")]
        code = obj.customer.code if name.startswith("orders") else obj.partner.code
        assert f">{code}</dd>" in aside and f"¥{code}" not in aside, name
