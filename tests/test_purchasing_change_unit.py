"""送った発注の変更の画面で、足す行の単位を数量単位の一覧から選ぶ (N349。C108 の発注の明細の単位の欄と同じ形)。

相手の受け口への HTTP は requests.post を偽の物に差し替え、送った本文を控える (外へは出ない)。鍵はテストのたびに作り、環境変数に置く。
取引先と品目は作り物。数量の単位の一覧の行は migration だけが入れるので、試験は transaction=True にしない。
取適法の確かめ (purchasing.checks) の黄の知らせが出たときは、「このまま保存」(confirmed=1) で送り直す。
fixture と送る値の組み立ては tests/test_purchasing_change.py の物を写した (既にある試験のファイルを触らないため)。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.masters.services import UNLISTED_LINE_UNIT
from seizou.purchasing import services
from seizou.purchasing.forms import PurchaseOrderChangeForm
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def subcontractor(user, secret):
    partner = masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    return partner


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": f"T-{n}00", "name": f"作り物の部品 {n}", "unit": "個"}, user)
        for n in range(1, 5)
    ]


@pytest.fixture
def rod(user):
    """単位が数量単位の一覧に無い字 (「本」) の部品。"""
    return masters_services.create_item({"part_no": "T-900", "name": "作り物の棒", "unit": "本"}, user)


@pytest.fixture
def fake(monkeypatch):
    post = FakePost()
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def order(subcontractor, items, user, fake):
    """送った (相手が受け取った) 明細 3 行の発注。"""
    po = services.create_purchase_order(
        subcontractor,
        [
            {"item": items[0], "qty": Decimal("300"), "unit_price": Decimal("100"), "due_date": date(2026, 11, 20)},
            {"item": items[1], "qty": Decimal("50"), "unit_price": Decimal("1180"), "due_date": date(2026, 11, 20)},
            {"item": items[2], "qty": Decimal("10"), "unit_price": Decimal("500"), "due_date": date(2026, 11, 25)},
        ],
        user,
        order_date=date(2026, 11, 2),
    )
    assert po.status == PurchaseOrder.STATUS_SENT
    return po


def _url(po):
    return reverse("purchasing:purchaseorder_change", args=[po.pk])


def _form_data(po, changes=None, *, add=(), note=None, confirmed=False):
    """変更の form の送る値。changes = {行番号: {欄: 値}}。add = 足す明細の辞書の並び。"""
    changes = changes or {}
    rows = list(po.lines.exclude(line_status_code="2").order_by("line_no"))
    data = {
        "note": po.note if note is None else note,
        "lines-TOTAL_FORMS": str(len(rows) + len(add)),
        "lines-INITIAL_FORMS": str(len(rows)),
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        values = {"line": row.pk, "qty": row.qty, "unit_price": row.unit_price, "due_date": row.due_date or "", "note": row.note}
        values.update(changes.get(row.line_no, {}))
        data.update({f"lines-{index}-{key}": str(value) for key, value in values.items()})
    for offset, values in enumerate(add, start=len(rows)):
        data.update({f"lines-{offset}-{key}": str(value) for key, value in values.items()})
    if confirmed:
        data["confirmed"] = "1"
    return data


def _add(item, unit):
    return [{"item": item.pk, "qty": "5", "unit": unit, "unit_price": "400", "due_date": "", "note": ""}]


def _send_change(client, po, add):
    """行を足す変更を送る。黄の確かめが出て 200 なら、「このまま保存」で送り直す。"""
    response = client.post(_url(po), _form_data(po, add=add))
    if response.status_code == 200 and 'data-notice="check"' in response.content.decode():
        response = client.post(_url(po), _form_data(po, add=add, confirmed=True))
    return response


def _pair(po, line_no):
    line = po.lines.get(line_no=line_no)
    return line.unit, line.unit_code


@pytest.mark.django_db
def test_change_screen_has_unit_select_on_new_row(client_logged_in, order):
    body = client_logged_in.get(_url(order)).content.decode()
    url = reverse("masters:quantityunit_select")
    tag = re.search(r'<select name="lines-3-unit"[^>]*>', body)
    assert tag and f'data-select-url="{url}"' in tag.group(0)
    # 既にある明細の行は単位を欄にせず、字で出す
    assert 'name="lines-0-unit"' not in body
    first = body[body.index('data-change-line="1"') :]
    assert re.search(r">個</td>", first[: first.index("</tr>")])
    template = body[body.index('<template id="po-line-template">') :]
    assert '<select name="lines-__prefix__-unit"' in template


@pytest.mark.django_db
def test_new_row_without_unit_copies_item_unit(client_logged_in, order, items):
    response = _send_change(client_logged_in, order, _add(items[3], ""))
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.revision_no == 2
    assert _pair(order, 4) == ("個", "H87")


@pytest.mark.django_db
def test_new_row_with_chosen_unit_saves_name_and_code(client_logged_in, order, items):
    response = _send_change(client_logged_in, order, _add(items[3], "KGM"))
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.revision_no == 2
    assert _pair(order, 4) == ("kg", "KGM")


@pytest.mark.django_db
def test_new_row_with_unlisted_item_unit_asks_to_choose(client_logged_in, order, rod, fake):
    calls = len(fake.calls)
    response = client_logged_in.post(_url(order), _form_data(order, add=_add(rod, "")))
    assert response.status_code == 200
    assert response.context["form"].lines.forms[3].errors["unit"] == [UNLISTED_LINE_UNIT.format(unit="本")]
    order.refresh_from_db()
    assert order.revision_no == 1
    assert len(fake.calls) == calls

    response = _send_change(client_logged_in, order, _add(rod, "XRD"))
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.revision_no == 2
    assert _pair(order, 4) == ("本（棒）", "XRD")


@pytest.mark.django_db
def test_form_without_money_has_no_new_row_and_no_unit_field(order):
    form = PurchaseOrderChangeForm(instance=order, hide_money=True)
    assert len(form.lines.forms) == 3
    assert all("unit" not in row.fields for row in form.lines.forms)


@pytest.mark.django_db
def test_unlisted_unit_error_shows_under_unit_select(client_logged_in, order, rod):
    response = client_logged_in.post(_url(order), _form_data(order, add=_add(rod, "")))
    assert response.status_code == 200
    body = response.content.decode()
    cell = body[body.index('<select name="lines-3-unit"') :]
    cell = cell[: cell.index("</td>")]
    assert cell.count(UNLISTED_LINE_UNIT.format(unit="本")) == 1
