"""打ち間違えた出荷を消す道 (shipping.services.delete_shipment と delete_refusal。本人の決め 11-08 00:23・00:25。指示書の候補 12)。

消すと出荷の行が無くなり、受注の状態と在庫が出荷の前に戻り、変更の履歴に「消した」の 1 行が残る。
発行の納品書に入った出荷・請求書の外の入金が指す出荷・取引先につながる出荷 (受信した注文の出荷・送った出荷の知らせ・
届いた検収)・明細を出し終えた出荷は、日本語の ValueError で断り、何も変えない (字は delete_refusal の 1 か所)。
請求書の明細が指す出荷は消せる (明細は出荷番号と出荷日の写しを持って残る。依頼書 N403c)。
作り物の得意先・部品・受注だけを使う。相手への HTTP は偽物 (呼ばれたら落とす) にする。
"""

import re
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.attachments import services as attachments_services
from seizou.attachments.models import Attachment
from seizou.billing import services as billing_services
from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import access
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services
from seizou.shipping.models import Shipment
from seizou.stock import services as stock_services
from tests.test_core_access import account
from tests.test_core_row_summary_lists import HEADER, HTMX, MONEY_ONLY, eye_links

ORDER_DATE = date(2026, 11, 2)
COUNTED_ON = date(2026, 11, 3)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("出荷を消すときに相手への HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _order(customer, item, user, *, source="manual"):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
        source=source,
    )


@pytest.fixture
def line(customer, item, user):
    """手入力の受注 (数量 10) の明細。"""
    return _order(customer, item, user).lines.get()


def _ship(line, qty, user, **kwargs):
    return services.ship(line, Decimal(qty), SHIPPED_AT, user, **kwargs)


def _deleted_logs(pk):
    return ChangeLog.objects.filter(owner_model="shipping.shipment", owner_pk=pk, action=ChangeLog.ACTION_DELETED)


def _assert_refused(shipment, user, words):
    """消せない出荷: 断りの字は delete_refusal と同じで、出荷も受注の状態も履歴の行も変わらない。"""
    shipment.refresh_from_db()
    order = shipment.order_line.order
    order.refresh_from_db()
    status = order.status
    logs = ChangeLog.objects.count()
    refusal = services.delete_refusal(shipment)
    assert refusal.startswith("削除できません。")
    assert shipment.shipment_no in refusal
    assert words in refusal

    with pytest.raises(ValueError) as caught:
        services.delete_shipment(shipment, user)

    assert str(caught.value) == refusal
    assert Shipment.objects.filter(pk=shipment.pk).exists()
    order.refresh_from_db()
    assert order.status == status
    assert ChangeLog.objects.count() == logs
    return refusal


# ---------------------------------------------------------------- (a) 消すと出荷の前に戻る


@pytest.mark.django_db
def test_delete_returns_order_and_stock_to_before(user, item, line):
    stock_services.adjust(item, Decimal("50"), COUNTED_ON, user)
    first = _ship(line, "4", user)
    second = _ship(line, "3", user)
    assert stock_services.balance(item) == Decimal("43")
    first_pk, number = first.pk, first.shipment_no

    result = services.delete_shipment(first, user)

    assert result.pk is None
    assert result.saved_message == f"出荷 {number} を削除しました。"
    assert not Shipment.objects.filter(pk=first_pk).exists()
    order = line.order
    order.refresh_from_db()
    assert order.status == order.STATUS_PARTIAL  # ほかの出荷 (3) が残るので一部出荷
    assert stock_services.balance(item) == Decimal("47")
    logs = list(_deleted_logs(first_pk))
    assert len(logs) == 1
    assert number in logs[0].object_text

    services.delete_shipment(second, user)

    order.refresh_from_db()
    assert order.status == order.STATUS_OPEN  # 出荷が無くなったので受注に戻る
    assert stock_services.balance(item) == Decimal("50")
    assert orders_services.remaining_qty(line) == Decimal("10")


# ---------------------------------------------------------------- (b) 消せない出荷と、消せる取消の納品書の出荷


@pytest.mark.django_db
def test_issued_note_is_refused(user, customer, line):
    shipment = _ship(line, "4", user)
    note = services.create_delivery_note(customer, [shipment], user)

    refusal = _assert_refused(shipment, user, f"納品書 {note.note_no} に入っています")
    assert "金額を見られる人" in refusal


@pytest.mark.django_db
def test_cancelled_note_shipment_can_be_deleted(user, customer, line):
    shipment = _ship(line, "4", user)
    note = services.create_delivery_note(customer, [shipment], user)
    services.cancel_delivery_note(note, user)
    shipment.refresh_from_db()

    assert services.delete_refusal(shipment) == ""
    services.delete_shipment(shipment, user)

    assert not Shipment.objects.filter(pk=shipment.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("cancel_invoice", [False, True], ids=["issued", "cancelled"])
def test_invoice_line_shipment_can_be_deleted_and_line_stays(user, customer, line, cancel_invoice):
    """請求書の明細が指す出荷は、納品書を取り消せば消せる (発行の請求書も取消の請求書も)。請求書の明細は出荷番号と出荷日の写しを持って残る。

    前は「請求書を取り消しても消せない」(test_invoice_line_is_refused_even_after_cancel) だった。期待を変えた訳: 本人の決め 11-11 12:5x〜13:0x
    (請求済みの出荷の直しと削除は止めない) で、請求書の明細が出荷番号と出荷日を写して残し、請求書に入った出荷を消せるようにしたため (依頼書 N403c)。
    """
    shipment = _ship(line, "4", user)
    note = services.create_delivery_note(customer, [shipment], user)
    invoice = billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)
    # 発行の納品書に入っている間は、納品書の文で断る (請求書の文は無くなった)。
    _assert_refused(shipment, user, f"納品書 {note.note_no} に入っています")

    if cancel_invoice:
        billing_services.cancel_invoice(invoice, user)
    services.cancel_delivery_note(note, user)
    shipment.refresh_from_db()

    assert services.delete_refusal(shipment) == ""
    services.delete_shipment(shipment, user)

    assert not Shipment.objects.filter(pk=shipment.pk).exists()
    row = invoice.lines.get()
    assert row.shipment_id is None
    assert (row.shipment_no, row.shipped_at, row.qty) == (shipment.shipment_no, SHIPPED_AT, Decimal("4"))
    assert _deleted_logs(shipment.pk).count() == 1


@pytest.mark.django_db
def test_outside_payment_is_refused_even_after_cancel(user, customer, line):
    shipment = _ship(line, "4", user)
    note = services.create_delivery_note(customer, [shipment], user)
    payment = billing_services.record_outside_payment(shipment, SHIPPED_AT, Decimal("400"), user)
    billing_services.cancel_payment(payment, user)
    services.cancel_delivery_note(note, user)

    # 断りの字は依頼書 N403c で「請求書の外で受け取った入金の記録」だけを言う形にした (断る事は変えていない)。
    _assert_refused(shipment, user, "請求書の外で受け取った入金の記録が指しています")


@pytest.mark.django_db
def test_sent_notice_is_refused(user, line):
    shipment = _ship(line, "4", user)
    services.record_notice_response(shipment, "作り物の返事", user, sent=True)

    _assert_refused(shipment, user, "取引先につながる記録")


@pytest.mark.django_db
def test_acceptance_on_line_is_refused(user, customer, line):
    """手入力の受注の明細に検収を 1 行置く (入口は手入力のままなので、受信した注文の出荷の断りには当たらない)。"""
    shipment = _ship(line, "4", user)
    inbox = Inbox.objects.create(
        message_id=uuid.uuid4(), partner=customer, kind="order", payload={}, status="accepted", order=line.order
    )
    connect_services.record_acceptance(
        inbox, line, Decimal("4"), SHIPPED_AT, None, message_id=uuid.uuid4(), buyer_order_no="PO-0001", buyer_line_no=line.line_no
    )

    refusal = _assert_refused(shipment, user, "取引先につながる記録")
    assert "相手に会話で伝えてください" in refusal


@pytest.mark.django_db
def test_inbox_order_shipment_is_refused(user, customer, item):
    """受信した注文で作った受注の出荷 (種の読み込みと同じく知らせを送らない形で作るので、送った日時は空)。"""
    order = _order(customer, item, user, source="inbox")
    Inbox.objects.create(
        message_id=uuid.uuid4(), partner=customer, kind="order", payload={"order_no": "PO-0001"}, status="accepted", order=order
    )
    shipment = _ship(order.lines.get(), "4", user, send=False)
    assert shipment.notice_sent_at is None

    _assert_refused(shipment, user, "取引先につながる記録")


@pytest.mark.django_db
def test_finishing_shipment_is_refused(user, line):
    shipment = _ship(line, "10", user)
    assert shipment.finishes_line

    refusal = _assert_refused(shipment, user, "明細を出し終えた出荷です")
    assert "「編集」で直してください" in refusal


# ---------------------------------------------------------------- (c) 2 回目


@pytest.mark.django_db
def test_delete_twice_is_refused(user, line):
    shipment = _ship(line, "4", user)
    services.delete_shipment(shipment, user)

    with pytest.raises(ValueError, match="削除できません。この出荷は既に消してあります。"):
        services.delete_shipment(shipment, user)
    assert _deleted_logs(shipment.pk).count() == 1


# ---------------------------------------------------------------- (d) 添付 (今の動き = 付けた先の無い行が残る)


@pytest.mark.django_db
def test_attachment_row_stays_after_delete(settings, tmp_path, user, line):
    settings.MEDIA_ROOT = tmp_path / "media"
    shipment = _ship(line, "4", user)
    attachment = attachments_services.add_attachment(shipment, SimpleUploadedFile("作り物の写真.png", b"png"), user, kind="photo")
    shipment_pk = shipment.pk

    services.delete_shipment(shipment, user)

    attachment = Attachment.objects.get(pk=attachment.pk)
    assert attachment.object_id == shipment_pk
    assert attachment.target is None


# ---------------------------------------------------------------- (e) 権限の表


def test_delete_action_is_shipping_kind_and_not_money():
    assert access.ACTION_KINDS["shipping.delete_shipment"] == access.ACTION_KINDS["shipping.update_shipment"] == "shipping"
    assert "shipping.delete_shipment" not in access.MONEY_ACTIONS


# ---------------------------------------------------------------- (f)〜(i) 削除の確かめの画面と小窓

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 入力のカード (core/_form_card.html) の form の頭
FORM_CARD = '<form method="post" class="rounded-xl'


def _delete_url(shipment):
    return reverse("shipping:shipment_delete", args=[shipment.pk])


@pytest.mark.django_db
def test_delete_screen_and_modal_confirm(logged_in, line, user):
    shipment = _ship(line, "4", user)

    for response in (logged_in.get(_delete_url(shipment)), logged_in.get(_delete_url(shipment), **MODAL)):
        assert response.status_code == 200
        body = response.content.decode()
        assert "この出荷を消すと戻せません。受注の状態と在庫は出荷の前に戻ります。" in body
        assert shipment.shipment_no in body
        assert FORM_CARD in body and "btn btn-error" in body and "削除" in body
    assert "<html" not in body  # 小窓は部分だけ


@pytest.mark.django_db
def test_delete_post_goes_back_to_next_or_list(logged_in, line, user):
    first = _ship(line, "4", user)
    second = _ship(line, "3", user)
    listing = f"{reverse('shipping:shipment_list')}?q=T-100"

    response = logged_in.post(_delete_url(first), {"next": listing})

    assert response.status_code == 302
    assert response["Location"] == listing
    assert [str(m) for m in get_messages(response.wsgi_request)] == [f"出荷 {first.shipment_no} を削除しました。"]
    assert not Shipment.objects.filter(pk=first.pk).exists()

    response = logged_in.post(_delete_url(second), {})

    assert response.status_code == 302
    assert response["Location"] == reverse("shipping:shipment_list")
    assert not Shipment.objects.exists()


@pytest.mark.django_db
def test_refused_shipment_shows_reason_without_form(logged_in, customer, line, user):
    shipment = _ship(line, "4", user)
    services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    refusal = services.delete_refusal(shipment)

    for response in (logged_in.get(_delete_url(shipment)), logged_in.get(_delete_url(shipment), **MODAL)):
        body = response.content.decode()
        assert response.status_code == 200
        assert refusal in body
        assert FORM_CARD not in body and "btn btn-error" not in body
    page = logged_in.get(_delete_url(shipment)).content.decode()
    assert f'href="{reverse("shipping:shipment_detail", args=[shipment.pk])}"' in page and "出荷へ戻る" in page

    response = logged_in.post(_delete_url(shipment), {})

    assert response.status_code == 200
    assert refusal in response.content.decode()
    assert Shipment.objects.filter(pk=shipment.pk).exists()


def test_delete_url():
    assert reverse("shipping:shipment_delete", args=[1]) == "/shipping/1/delete/"


# ---------------------------------------------------------------- (j)(k) 出荷の一覧の行の ⋮ と右クリック

#: 行の操作の ⋮ (core/static/js/seizou-row-menu.js の markup の約束)
MENU = '<details class="dropdown dropdown-end row-menu">'
WRITE_ONLY = {"access_control": {"department": "off", "write": "on", "money": "off"}}


def _row(body, shipment):
    match = re.search(rf'<tr id="row-{shipment.pk}"[^>]*>.*?</tr>', body, re.S)
    assert match is not None, "出荷の行が無い"
    return match.group(0)


def _menu_links(row):
    """⋮ の中の (字 → (href, data-modal-form))。"""
    return {
        label: (href, modal)
        for href, modal, label in re.findall(r'<a[^>]*href="([^"]*)"[^>]*data-modal-form="([^"]*)"[^>]*>.*?(編集|削除)</a>', row, re.S)
    }


@pytest.mark.django_db
def test_shipment_list_rows_have_edit_and_delete_menu(logged_in, line, user):
    shipment = _ship(line, "4", user)
    url = reverse("shipping:shipment_list")
    eyes = [(f"/shipping/{shipment.pk}/", f"/shipping/{shipment.pk}/modal/")]

    for response in (logged_in.get(url), logged_in.get(url, {"q": ""}, **HTMX)):
        body = response.content.decode()
        row = _row(body, shipment)
        head = re.search(rf'<tr id="row-{shipment.pk}"[^>]*>', row).group(0)
        assert "data-row-menu" in head
        assert "closest('a, details')" in head
        assert MENU in row
        links = _menu_links(row)
        assert set(links) == {"編集", "削除"}
        for label, path in (("編集", f"/shipping/{shipment.pk}/edit/"), ("削除", f"/shipping/{shipment.pk}/delete/")):
            href, modal = links[label]
            assert href == modal
            assert href.startswith(f"{path}?next=/shipping/")
        # 見出し「要点」は 1 つのまま、目の印の link も今のまま
        assert body.count(HEADER) == 1
        assert eye_links(body) == eyes


@pytest.mark.django_db
def test_menu_follows_write_permission_not_money(settings, client, logged_in, line, user):
    shipment = _ship(line, "4", user)
    url = reverse("shipping:shipment_list")

    # 編集の判定が on: 出荷の種類の権限の無い人には出さない
    settings.SEIZOU = WRITE_ONLY
    row = _row(logged_in.get(url).content.decode(), shipment)
    assert MENU not in row and _menu_links(row) == {}
    clerk = account("denpyo", permissions=["slip_edit"])
    client.force_login(clerk)
    assert set(_menu_links(_row(client.get(url).content.decode(), shipment))) == {"編集", "削除"}

    # 金額の判定だけが on: 出荷は数の操作なので、金額の閲覧の無い人にも出す (決め 21:0x)
    settings.SEIZOU = MONEY_ONLY
    client.force_login(user)
    assert set(_menu_links(_row(client.get(url).content.decode(), shipment))) == {"編集", "削除"}


# ---------------------------------------------------------------- (l) 手引きの「打ち間違えたとき」


def test_operations_has_typo_section_between_trouble_and_practice():
    text = (Path(__file__).resolve().parent.parent / "docs" / "operations.md").read_text(encoding="utf-8")
    head = "## 打ち間違えたとき (出荷・受入・在庫)\n"

    assert text.count(head) == 1
    start = text.index(head)
    assert text.index("## 困ったとき\n") < start < text.index("## 練習モード")
    section = text[start : text.index("\n## ", start + 1)]
    for word in ("棚卸しの調整", "会話", "削除", "金額を見られる人", "受入は、保存した後に画面から直せません。"):
        assert word in section, word


# ---------------------------------------------------------------- 6 (任意) 出荷の編集の小窓の「この出荷を削除」


@pytest.mark.django_db
def test_edit_modal_links_to_delete_with_next(logged_in, line, user):
    shipment = _ship(line, "4", user)
    edit = reverse("shipping:shipment_update", args=[shipment.pk])
    delete = f"/shipping/{shipment.pk}/delete/?next=/shipping/"

    modal = logged_in.get(f"{edit}?next=/shipping/", **MODAL).content.decode()

    assert f'href="{delete}" data-modal-form="{delete}"' in modal
    assert "この出荷を削除" in modal
    assert "この出荷を削除" not in logged_in.get(edit).content.decode()  # 画面の全体には置かない (小窓だけ)
