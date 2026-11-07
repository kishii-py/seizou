"""送った発注を変更として送る (中小企業共通 EDI の注文変更の方式 2。C96 その 4)。

相手の受け口への HTTP は requests.post を偽の物に差し替え、送った本文を控える (外へは出ない)。鍵はテストのたびに作り、環境変数に置く。
取引先と品目は作り物。日は固定の値を渡す。取適法の確かめ (purchasing.checks) は黄の知らせで、「このまま保存」(confirmed=1) で通す。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Outbox
from seizou.core import edi_codes
from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.purchasing import services
from seizou.purchasing.forms import PurchaseOrderChangeForm, ReceiptForm
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine
from tests.test_connect_send import FakePost, FakeResponse
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
DAY = date(2026, 11, 6)


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
def fake(monkeypatch):
    post = FakePost()
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def order(subcontractor, items, user, fake):
    """送った (相手が受け取った) 明細 3 行の発注。明細 1 = 数 300・明細 2 = 単価 1180・明細 3。"""
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
    """変更の form の送る値。changes = {行番号: {欄: 値}}。DELETE は {"DELETE": "on"}。add = 足す明細の辞書の並び。"""
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


def _codes(po):
    return [(row.line_no, row.line_status_code, row.line_status_reason_code) for row in po.lines.order_by("line_no")]


def _last_body(fake):
    return json.loads(fake.calls[-1]["data"].decode("utf-8"))


def _notice(line, qty, revision_no=1):
    return connect_services.record_shipping_notice(
        line, Decimal(qty), DAY, None, message_id=uuid.uuid4(), shipment_no=f"SH-{uuid.uuid4().hex[:6]}", order_revision_no=revision_no
    )


@pytest.mark.django_db
def test_change_warns_then_sends_revision(client_logged_in, order, items, fake):
    changes = {1: {"qty": "200"}, 2: {"unit_price": "1150"}, 3: {"DELETE": "on"}}
    add = [{"item": items[3].pk, "qty": "5", "unit": "", "unit_price": "400", "due_date": "2026-11-28", "note": ""}]

    response = client_logged_in.post(_url(order), _form_data(order, changes, add=add))
    body = response.content.decode()
    assert response.status_code == 200
    assert body.count('data-notice="check"') == 2
    assert "明細 2 の単価を ¥1,180 / 個 から ¥1,150 / 個 に下げます。" in body
    assert "取適法の対象の取引では、発注した後の減額は、相手が合意しても違反になるおそれがあります。" in body
    assert "明細 1 の数を 300 から 200 に減らします。" in body
    assert "明細 3 を取り消します。" in body
    assert "相手が負った費用を負担しないと違反になるおそれがあります。" in body
    assert "このまま保存" in body
    order.refresh_from_db()
    assert order.revision_no == 1

    calls = len(fake.calls)
    response = client_logged_in.post(_url(order), _form_data(order, changes, add=add, confirmed=True))
    assert response.status_code == 302
    order.refresh_from_db()
    assert (order.revision_no, order.purpose_code, order.status) == (2, "4", PurchaseOrder.STATUS_SENT)
    assert _codes(order) == [(1, "3", "3002"), (2, "3", "3006"), (3, "2", ""), (4, "1", "")]
    assert len(fake.calls) == calls + 1
    sent = _last_body(fake)
    assert (sent["order_revision_no"], sent["purpose_code"]) == (2, "4")
    assert [line["line_status_code"] for line in sent["lines"]] == ["3", "3", "2", "1"]

    # 次に明細 1 の納期だけを遅らせる: 取り消してある明細 3 は確かめに出ず、確かめが無いので 1 回で送る
    response = client_logged_in.post(_url(order), _form_data(order, {1: {"due_date": "2026-11-30"}}))
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.revision_no == 3
    assert _codes(order) == [(1, "3", "3001"), (2, "39", ""), (3, "2", ""), (4, "39", "")]


@pytest.mark.django_db
def test_raising_price_qty_or_later_due_has_no_warning(client_logged_in, order):
    changes = {1: {"qty": "400", "due_date": "2026-11-30"}, 2: {"unit_price": "1200"}}
    response = client_logged_in.post(_url(order), _form_data(order, changes))
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.revision_no == 2
    assert _codes(order)[:2] == [(1, "3", "3003"), (2, "3", "3006")]


@pytest.mark.django_db
def test_no_change_is_not_sent(client_logged_in, order, fake):
    calls = len(fake.calls)
    response = client_logged_in.post(_url(order), _form_data(order), follow=True)
    assert "変えた所が無いので、変更を送っていません。" in response.content.decode()
    order.refresh_from_db()
    assert order.revision_no == 1
    assert len(fake.calls) == calls


@pytest.mark.django_db
def test_same_content_is_resent_after_refusal(client_logged_in, order, fake):
    client_logged_in.post(_url(order), _form_data(order, {1: {"qty": "200"}, 3: {"DELETE": "on"}}, confirmed=True))
    order.refresh_from_db()
    assert order.revision_no == 2
    Outbox.objects.filter(purchase_order=order, revision_no=2).update(reply_type="RE", reply_note="作り物の断り")

    response = client_logged_in.post(_url(order), _form_data(order))
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.revision_no == 3
    assert _codes(order) == [(1, "3", "3002"), (2, "39", ""), (3, "2", "")]
    sent = _last_body(fake)
    assert sent["order_revision_no"] == 3
    assert [line["line_status_code"] for line in sent["lines"]] == ["3", "39", "2"]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({1: {"unit_price": "90"}}, "保存できません。明細 1 は相手が出荷済みなので、単価は変えられません。"),
        ({1: {"qty": "5"}}, "保存できません。明細 1 の数を、相手が出荷済みの数 (10) より少なくはできません。"),
    ],
)
@pytest.mark.django_db
def test_shipped_line_limits_are_refused(order, user, changes, message):
    first = order.lines.get(line_no=1)
    _notice(first, "10")
    lines = [{"line": row, "qty": row.qty, "unit_price": row.unit_price, "due_date": row.due_date, "note": row.note} for row in order.lines.order_by("line_no")]
    for key, value in changes[1].items():
        lines[0][key] = Decimal(value)
    with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
        services.change_purchase_order(order, {"note": ""}, lines, user)


@pytest.mark.django_db
def test_shipped_line_cannot_be_dropped(order, user):
    _notice(order.lines.get(line_no=1), "10")
    lines = [{"line": row, "qty": row.qty} for row in order.lines.order_by("line_no") if row.line_no != 1]
    with pytest.raises(ValueError, match="明細 1 は相手が出荷済みなので取り消せません。数を出荷済みの数に直してください。"):
        services.change_purchase_order(order, {"note": ""}, lines, user)


@pytest.mark.django_db
def test_shipped_line_form_hides_price_and_delete(order, client_logged_in):
    _notice(order.lines.get(line_no=1), "10")
    body = client_logged_in.get(_url(order)).content.decode()
    assert "出荷済み 10" in body
    assert 'name="lines-0-unit_price"' not in body
    assert 'name="lines-0-DELETE"' not in body
    assert 'name="lines-1-unit_price"' in body
    assert "第 2 版の変更を送る" in body


@pytest.mark.django_db
def test_fully_shipped_order_cannot_change(order, user, client_logged_in):
    for row in order.lines.all():
        _notice(row, str(row.qty))
    assert services.can_change_order(order) is False
    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[order.pk])).content.decode()
    assert _url(order) not in body
    lines = [{"line": row, "qty": row.qty} for row in order.lines.all()]
    with pytest.raises(ValueError, match="^保存できません。相手が全部の明細を出荷し終えているので、変更は送れません"):
        services.change_purchase_order(order, {"note": ""}, lines, user)


@pytest.mark.parametrize("status", [PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_REJECTED, PurchaseOrder.STATUS_CANCELLED])
@pytest.mark.django_db
def test_change_screen_is_404_outside_sent_states(order, client_logged_in, status):
    PurchaseOrder.objects.filter(pk=order.pk).update(status=status)
    assert client_logged_in.get(_url(order)).status_code == 404
    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[order.pk])).content.decode()
    assert _url(order) not in body


@pytest.mark.django_db
def test_detail_shows_change_button_for_sent_order(order, client_logged_in):
    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[order.pk])).content.decode()
    assert f'href="{_url(order)}"' in body


@pytest.mark.django_db
def test_form_without_money_has_no_price_and_no_new_line(order):
    form = PurchaseOrderChangeForm(instance=order, hide_money=True)
    assert all("unit_price" not in row.fields for row in form.lines.forms)
    assert len(form.lines.forms) == 3


@pytest.mark.django_db
def test_undelivered_revision_shows_alert_and_resend(order, client_logged_in, monkeypatch):
    monkeypatch.setattr("requests.post", FakePost(reply=FakeResponse(503)))
    client_logged_in.post(_url(order), _form_data(order, {1: {"qty": "400"}}))
    order.refresh_from_db()
    assert order.revision_no == 2
    detail = reverse("purchasing:purchaseorder_detail", args=[order.pk])
    body = client_logged_in.get(detail).content.decode()
    assert "第 2 版の変更は相手に届いていません。" in body
    assert reverse("purchasing:purchaseorder_send", args=[order.pk]) in body

    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    first = Outbox.objects.filter(purchase_order=order, revision_no=2).order_by("pk").first()
    response = client_logged_in.post(reverse("purchasing:purchaseorder_send", args=[order.pk]), follow=True)
    assert "相手に第 2 版の変更を送りました。" in response.content.decode()
    assert _last_body(fake)["id"] == str(first.message_id)
    body = client_logged_in.get(detail).content.decode()
    assert "第 2 版の変更は相手に届いていません。" not in body
    assert reverse("purchasing:purchaseorder_send", args=[order.pk]) not in body


@pytest.mark.django_db
def test_refused_revision_shows_reason_and_keeps_changes(order, client_logged_in):
    client_logged_in.post(_url(order), _form_data(order, {1: {"qty": "400"}}))
    outbox = Outbox.objects.filter(purchase_order=order, revision_no=2).order_by("pk").first()
    connect_services._record_revision_reply(outbox, uuid.uuid4(), edi_codes.REPLY_REJECTED, "作り物の断りの理由")

    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[order.pk])).content.decode()
    assert "相手は第 2 版の変更を断りました。理由: 作り物の断りの理由。相手の受注は前の版の中身のままです。" in body
    assert "第 2 版 (変更)・相手が断った" in body
    order.refresh_from_db()
    assert order.revision_no == 2
    assert order.lines.get(line_no=1).qty == Decimal("400")


@pytest.mark.django_db
def test_detail_chips_live_token_and_old_revision_marks(order, client_logged_in):
    client_logged_in.post(_url(order), _form_data(order, {1: {"qty": "400"}}))
    order.refresh_from_db()
    first = order.lines.get(line_no=1)
    _notice(first, "10", revision_no=1)

    body = client_logged_in.get(reverse("purchasing:purchaseorder_detail", args=[order.pk])).content.decode()
    chips = body.count("data-head-chip")
    assert chips == 3
    assert "一番早い納期" in body and "第 2 版 (変更)・相手の答え待ち" in body
    assert f'data-live="po-{order.pk}:sent:' in body and ":r2\"" in body
    assert "第 1 版の注文への知らせ (今は第 2 版)" in body
    assert '<span class="badge badge-soft badge-info">変更 (数量変更)</span>' in body

    form = ReceiptForm(initial={"line": str(first.pk)})
    assert form.fields["line"].help_text == "第 1 版の注文への出荷の知らせです (今は第 2 版)。"


@pytest.mark.django_db
def test_change_screen_usecase_has_no_law_paragraph():
    screens = {screen.url_name: screen for screen in all_screens()}
    usecase = screens["purchasing:purchaseorder_change"].usecase
    assert "使う人: 生産管理" in usecase
    assert "取適法" not in usecase


@pytest.mark.django_db
def test_line_and_item_keys_are_checked(order, items, user):
    row = order.lines.get(line_no=1)
    with pytest.raises(ValueError, match="^保存できません。明細の行に line と item の両方があるか"):
        services.change_purchase_order(order, {"note": ""}, [{"line": row, "item": items[0], "qty": 1, "unit_price": 1}], user)
    with pytest.raises(ValueError, match="^保存できません。明細が無くなります。"):
        services.change_purchase_order(order, {"note": ""}, [], user)
    assert PurchaseOrderLine.objects.filter(purchase_order=order).count() == 3
