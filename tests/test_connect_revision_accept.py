"""受ける側が変更と取消の版を受け、受け入れると受注が変わること (中小企業共通 EDI の注文変更の方式 2。C96 その 3)。

1 つの DB に注文を送る会社 (A = 協力企業 S001 へ発注する) と受ける会社 (B = 得意先 C001 から受注する) の両方の役を置く
(tests/test_connect_reply.py の形)。相手への HTTP は requests.post を偽の物に差し替え、受けた本文を受け口に渡す。
送る側の変更の入口 (変更の画面) ができるまで、発注の版と明細のコードは表に直に書いてから send_order で送る。
取引先・品目・会社は作り物。日は固定の値を渡す。
"""

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services
from seizou.connect.forms import AcceptForm
from seizou.connect.models import Inbox, Outbox
from seizou.core import edi_codes
from seizou.core.exceptions import RuleViolation
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine
from seizou.shipping import services as shipping_services
from tests.test_connect_reply import ORDER_ENV, REPLY_ENV, _connect, _into_receive
from tests.test_connect_revision_protocol import _signed_receive
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret

DAY = date(2026, 11, 6)
PARTS = ("T-100", "T-200", "T-300", "T-400")


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def order_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ORDER_ENV, value)
    return value


@pytest.fixture
def reply_secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(REPLY_ENV, value)
    return value


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の発注元", "role": "customer"}, user)


@pytest.fixture
def items(user):
    """A の品目 (相手の品番 T-…) と B の品目 (自社の品番 B-…) の組。"""
    buyer = [masters_services.create_item({"part_no": part, "name": f"作り物の {part}", "unit": "個"}, user) for part in PARTS]
    own = [
        masters_services.create_item({"part_no": f"B-{part[2:]}", "name": f"作り物の受ける側の {part}", "unit": "個"}, user)
        for part in PARTS
    ]
    return buyer, own


@pytest.fixture
def connections(user, subcontractor, customer, order_secret, reply_secret):
    _connect(user, subcontractor, customer)


@pytest.fixture
def fake(monkeypatch, connections):
    post = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", post)
    return post


def _send_first(subcontractor, items, user):
    """A が明細 1〜3 (数 300・単価 1000・納期 11-20) の発注を送る。返す物は発注。"""
    buyer, _own = items
    return purchasing_services.create_purchase_order(
        subcontractor,
        [
            {"item": item, "qty": Decimal("300"), "unit_price": Decimal("1000"), "due_date": date(2026, 11, 20)}
            for item in buyer[:3]
        ],
        user,
        order_date=date(2026, 11, 2),
    )


def _mapping(items, count=4):
    buyer, own = items
    return {buyer[n].part_no: own[n] for n in range(count)}


def _accept_first(items, user):
    base = Inbox.objects.get(revises=None)
    order = services.accept(base, _mapping(items, 3), user)
    return Inbox.objects.get(pk=base.pk), order


def _revise(po, items, *, revision_no=2, purpose=edi_codes.PURPOSE_CHANGE):
    """A の発注を表に直に第 n 版の変更にする: 明細 1 の数 300 → 200 (3・3002)、明細 2 は 39、明細 3 は 2、明細 4 を 1 で足す。"""
    buyer, _own = items
    PurchaseOrder.objects.filter(pk=po.pk).update(revision_no=revision_no, purpose_code=purpose)
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=1).update(
        qty=Decimal("200"), amount=Decimal("200000"), line_status_code="3", line_status_reason_code="3002"
    )
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=2).update(line_status_code="39", line_status_reason_code="")
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=3).update(line_status_code="2", line_status_reason_code="")
    if not PurchaseOrderLine.objects.filter(purchase_order=po, line_no=4).exists():
        PurchaseOrderLine.objects.create(
            purchase_order=po, line_no=4, item=buyer[3], qty=Decimal("50"), unit="個", unit_price=Decimal("400"),
            amount=Decimal("20000"), due_date=date(2026, 11, 25), line_status_code="1",
        )
    return PurchaseOrder.objects.get(pk=po.pk)


def _cancel(po, revision_no=2):
    PurchaseOrder.objects.filter(pk=po.pk).update(
        revision_no=revision_no, purpose_code=edi_codes.PURPOSE_CANCEL, status=PurchaseOrder.STATUS_CANCELLED
    )
    PurchaseOrderLine.objects.filter(purchase_order=po).update(line_status_code="2", line_status_reason_code="")
    return PurchaseOrder.objects.get(pk=po.pk)


def _send(po, user):
    return services.send_order(PurchaseOrder.objects.get(pk=po.pk), user)


def _bodies(fake, kind):
    return [json.loads(call["data"].decode("utf-8")) for call in fake.calls if json.loads(call["data"].decode("utf-8"))["kind"] == kind]


def _rows(order):
    return {row.line_no: row for row in order.lines.all()}


@pytest.mark.django_db
def test_change_is_received_shown_and_accepted(fake, user, subcontractor, items, client_logged_in):
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    rows = _rows(order)
    # B が受注の明細 2 の単価を自分で直し、明細 5 を手で足しておく
    _buyer, own = items
    orders_services.update_order(
        order,
        {},
        [
            {"order_line": rows[1]},
            {"order_line": rows[2], "unit_price": Decimal("1180")},
            {"order_line": rows[3]},
            {"item": own[0], "qty": Decimal("7"), "unit_price": Decimal("10"), "line_no": 5},
        ],
        user,
    )

    outbox = _send(_revise(po, items), user)
    assert outbox.response == "受け取った"
    revision = Inbox.objects.get(revises=base)
    assert (revision.revision_no, revision.purpose_code, revision.status) == (2, "4", Inbox.STATUS_RECEIVED)
    assert revision.order is None
    # 人が受け入れるまで受注は変わらない (R10)
    assert _rows(order)[1].qty == Decimal("300")

    body = client_logged_in.get(reverse("connect:inbox_detail", args=[revision.pk])).content.decode()
    assert "注文履歴番号" in body and "第 2 版 (変更)" in body
    assert "明細状態" in body and "明細行変更" in body and "(数量変更)" in body
    assert "今: ¥1,180" in body
    assert "受注に無い (受け入れると足す)" in body
    assert "受け入れると受注から消す" in body
    assert "変える受注" in body and order.order_no in body
    assert reverse("connect:inbox_accept", args=[revision.pk]) in body
    # 断る画面にも同じ列と「今」の値が出る
    reject_body = client_logged_in.get(reverse("connect:inbox_reject", args=[revision.pk])).content.decode()
    assert "今: ¥1,180" in reject_body and "明細状態" in reject_body
    # 一覧の行: 同じ注文番号の知らせは出ず、版の札が出る
    list_body = client_logged_in.get(reverse("connect:inbox_list")).content.decode()
    assert "同じ注文番号の注文が既に届いています" not in list_body
    assert "第 2 版の変更 (受信した注文" in list_body
    assert "第 2 版の変更が届いています" in list_body

    # 受け入れの欄は、受注に無い行番号の明細の品番だけ
    form = AcceptForm(instance=revision)
    assert [part for _name, part in form.part_fields] == ["T-400"]
    assert form.submit_label == "変更を受け入れる"

    changed = services.accept(revision, {"T-400": own[3]}, user)
    assert changed.pk == order.pk
    rows = _rows(order)
    assert sorted(rows) == [1, 2, 4, 5]
    assert rows[1].qty == Decimal("200")
    assert rows[2].unit_price == Decimal("1000")
    assert (rows[4].item, rows[4].qty, rows[4].customer_part_no) == (own[3], Decimal("50"), "T-400")
    assert (rows[5].qty, rows[5].unit_price) == (Decimal("7"), Decimal("10"))
    base.refresh_from_db()
    assert base.current_revision_no() == 2
    revision.refresh_from_db()
    assert revision.status == Inbox.STATUS_ACCEPTED and revision.order is None

    reply = _bodies(fake, "order_reply")[-1]
    assert (reply["order_revision_no"], reply["reply_type"], reply["supplier_order_no"]) == (2, "AP", order.order_no)
    assert Outbox.objects.filter(revision_no=2).order_by("pk").first().reply_type == "AP"
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED

    shipping_services.ship(rows[1], Decimal("20"), DAY, user)
    notice = _bodies(fake, "shipping_notice")[-1]
    assert notice["order_revision_no"] == 2


@pytest.mark.django_db
def test_cancel_revision_cancels_order(fake, user, subcontractor, items):
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    _send(_cancel(po), user)
    revision = Inbox.objects.get(revises=base)
    assert AcceptForm(instance=revision).submit_label == "取消を受け入れる"
    services.accept(revision, {}, user)
    order.refresh_from_db()
    assert order.status == order.STATUS_CANCELLED
    assert "得意先の注文の取消 (注文履歴番号 2)" in order.note


@pytest.mark.django_db
def test_pending_cancel_revision_badge_says_cancel(fake, user, subcontractor, items, client_logged_in):
    """取消の版が届いたまま受け入れていない時、第 1 版の行の札は「取消が届いています」と出す (直す前は取消でも「変更が届いています」だった)。"""
    po = _send_first(subcontractor, items, user)
    base, _order = _accept_first(items, user)
    _send(_cancel(po), user)
    # 一覧 (注釈で読む道)
    list_body = client_logged_in.get(reverse("connect:inbox_list")).content.decode()
    assert "第 2 版の取消が届いています" in list_body
    assert "第 2 版の変更が届いています" not in list_body
    # 詳細 (注釈が無いので 1 回引く道)
    detail_body = client_logged_in.get(reverse("connect:inbox_detail", args=[base.pk])).content.decode()
    assert "第 2 版の取消が届いています" in detail_body
    assert "第 2 版の変更が届いています" not in detail_body


def _refused(outbox, text):
    assert outbox.response.startswith("断られた: 注文の変更を受けられません。")
    assert text in outbox.response
    assert not Inbox.objects.exclude(revises=None).exists()


@pytest.mark.django_db
def test_change_to_rejected_first_revision_is_refused(fake, user, subcontractor, items):
    po = _send_first(subcontractor, items, user)
    services.reject(Inbox.objects.get(), "作り物の断り", user)
    _refused(_send(_revise(po, items), user), "第 1 版の注文は断ってあるので、変える受注がありません。")


@pytest.mark.django_db
def test_change_to_shipped_order_is_refused(fake, user, subcontractor, items):
    po = _send_first(subcontractor, items, user)
    _base, order = _accept_first(items, user)
    for row in order.lines.all():
        shipping_services.ship(row, row.qty, DAY, user)
    _refused(_send(_revise(po, items), user), "は出荷済なので変えられません (送った注文を直せるのは、受注を出荷し終えるまで)。")


@pytest.mark.django_db
def test_cancel_to_partly_shipped_order_is_refused(fake, user, subcontractor, items):
    po = _send_first(subcontractor, items, user)
    _base, order = _accept_first(items, user)
    shipping_services.ship(_rows(order)[1], Decimal("10"), DAY, user)
    _refused(_send(_cancel(po), user), "は一部を出荷済みなので、取消を受けられません。")


@pytest.mark.parametrize(
    "line_no, change, message",
    [
        # 受け口の断りの文は相手へ返り、送り合った記録の結果の列に残るので、単価の値を入れない (Claude Security の走査の候補 11-07 の 39)
        (1, {"unit_price": Decimal("900"), "amount": Decimal("180000")}, "明細 1 は出荷済みの物があるので単価は変えられません (注文の単価が受注の単価と違います)。"),
        (1, {"qty": Decimal("5"), "amount": Decimal("5000")}, "明細 1 の数 (5) が出荷済みの数 (10) より少なくなります。"),
        (3, None, "明細 3 は出荷済みの物があるので取り消せません。"),
    ],
)
@pytest.mark.django_db
def test_change_against_shipped_line_is_refused(fake, user, subcontractor, items, line_no, change, message):
    po = _send_first(subcontractor, items, user)
    _base, order = _accept_first(items, user)
    shipping_services.ship(_rows(order)[line_no], Decimal("10"), DAY, user)
    po = _revise(po, items)
    # 明細 1 は数を出荷済みより多いまま (200) にしておき、見る物だけを変える
    if change is not None:
        PurchaseOrderLine.objects.filter(purchase_order=po, line_no=line_no).update(**change)
    _refused(_send(po, user), message)


@pytest.mark.django_db
def test_change_removing_line_with_acceptance_is_refused(fake, user, subcontractor, items):
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    services.record_acceptance(
        base, _rows(order)[3], Decimal("5"), DAY, None, message_id=uuid.uuid4(), buyer_order_no=po.po_no, buyer_line_no=3
    )
    outbox = _send(_revise(po, items), user)
    _refused(outbox, "明細 3 は、受注の明細に指示書か入荷の引当か検収があるので、受注から消せません。")
    assert "消した明細に出荷があります" not in outbox.response


@pytest.mark.django_db
def test_change_with_other_part_on_same_line_no_is_refused(fake, user, subcontractor, items):
    po = _send_first(subcontractor, items, user)
    _base, order = _accept_first(items, user)
    _buyer, own = items
    rows = _rows(order)
    # B が手で行番号 4 の明細を足してある (相手の品番は空)
    orders_services.update_order(
        order,
        {},
        [{"order_line": rows[n]} for n in (1, 2, 3)] + [{"item": own[0], "qty": Decimal("1"), "unit_price": Decimal("1")}],
        user,
    )
    _refused(_send(_revise(po, items), user), "明細 4 の品番が、受注の明細 4 (相手の品番 —) と違います。")


@pytest.mark.django_db
def test_old_revision_is_refused_and_resend_is_not(fake, user, subcontractor, items, order_secret):
    po = _send_first(subcontractor, items, user)
    base, _order = _accept_first(items, user)
    outbox = _send(_revise(po, items), user)
    revision = Inbox.objects.get(revises=base)
    services.accept(revision, _mapping(items), user)

    # 受け入れた版 2 と同じ番号の送り直しは、断らずに同じ行を返す
    again = _send(po, user)
    assert again.message_id == outbox.message_id
    assert again.response == "受け取った"
    assert Inbox.objects.exclude(revises=None).count() == 1

    # 別の番号の版 2 (受け入れてある版以下) は断る
    data = json.loads(json.dumps(_bodies(fake, "order")[-1]))
    data["id"] = str(uuid.uuid4())
    refused = _signed_receive(data, order_secret)
    assert refused == f"注文の変更を受けられません。受注 {base.order.order_no} は第 2 版まで受け入れてあります。"


@pytest.mark.django_db
def test_rejected_change_then_same_content_as_next_revision(fake, user, subcontractor, items):
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    _send(_revise(po, items), user)
    services.reject(Inbox.objects.get(revises=base), "作り物の断り", user)
    assert _rows(order)[1].qty == Decimal("300")
    outbox = Outbox.objects.filter(revision_no=2).order_by("pk").first()
    assert (outbox.reply_type, outbox.reply_note) == ("RE", "作り物の断り")
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_ACCEPTED

    # 同じ中身・同じ明細のコードで版 3 を送ると、受け入れて受注が版 3 の値になる
    _send(_revise(po, items, revision_no=3), user)
    third = Inbox.objects.get(revises=base, revision_no=3)
    services.accept(third, _mapping(items), user)
    rows = _rows(order)
    assert sorted(rows) == [1, 2, 4]
    assert rows[1].qty == Decimal("200")
    assert base.current_revision_no() == 3


@pytest.mark.django_db
def test_change_waits_for_first_revision(fake, user, subcontractor, items, client_logged_in):
    po = _send_first(subcontractor, items, user)
    base = Inbox.objects.get()
    outbox = _send(_revise(po, items), user)
    assert outbox.response == "受け取った"
    revision = Inbox.objects.get(revises=base)
    body = client_logged_in.get(reverse("connect:inbox_detail", args=[revision.pk])).content.decode()
    assert f"この変更は受け入れられません。先に第 1 版の注文 (受信した注文 {base.pk}) を受け入れてください。" in body
    assert reverse("connect:inbox_accept", args=[revision.pk]) not in body
    assert reverse("connect:inbox_reject", args=[revision.pk]) in body
    with pytest.raises(ValueError, match=rf"^受け入れられません。先に第 1 版の注文 \(受信した注文 {base.pk}\) を受け入れてください。"):
        services.accept(revision, _mapping(items), user)

    services.accept(base, _mapping(items, 3), user)
    order = services.accept(revision, _mapping(items), user)
    assert _rows(order)[1].qty == Decimal("200")
    with pytest.raises(RuleViolation):
        services.accept(revision, _mapping(items), user)


@pytest.mark.django_db
def test_order_detail_marks_acceptance_to_old_revision(fake, user, subcontractor, items, client_logged_in):
    po = _send_first(subcontractor, items, user)
    base, order = _accept_first(items, user)
    services.record_acceptance(
        base, _rows(order)[1], Decimal("5"), DAY, None, message_id=uuid.uuid4(), buyer_order_no=po.po_no, buyer_line_no=1
    )
    body = client_logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert "版の注文への検収" not in body
    _send(_revise(po, items), user)
    services.accept(Inbox.objects.get(revises=base), _mapping(items), user)
    body = client_logged_in.get(reverse("orders:order_detail", args=[order.pk])).content.decode()
    assert "第 1 版の注文への検収 (今は第 2 版)" in body


@pytest.mark.django_db
def test_update_order_adds_line_with_given_number(user, customer, items):
    _buyer, own = items
    order = orders_services.create_order(
        customer, [{"item": own[0], "qty": Decimal("1"), "unit_price": Decimal("1")}], user, order_date=DAY
    )
    (first,) = order.lines.all()
    orders_services.update_order(
        order,
        {},
        [
            {"order_line": first},
            {"item": own[1], "qty": Decimal("2"), "unit_price": Decimal("1"), "line_no": 7},
            {"item": own[2], "qty": Decimal("3"), "unit_price": Decimal("1")},
        ],
        user,
    )
    assert sorted(_rows(order)) == [1, 7, 8]


def _price_change_waiting(subcontractor, items, user):
    """明細 1 の単価を 1000 → 900 にした第 2 版を出荷の前に受けて待たせ、その後に明細 1 を 10 出荷する。返す物は第 2 版の行。"""
    po = _send_first(subcontractor, items, user)
    _base, order = _accept_first(items, user)
    po = _revise(po, items)
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=1).update(unit_price=Decimal("900"), amount=Decimal("180000"))
    _send(po, user)
    revision = Inbox.objects.get(revision_no=2)
    shipping_services.ship(_rows(order)[1], Decimal("10"), DAY, user)
    return revision


@pytest.mark.django_db
def test_price_refusal_keeps_no_price_in_exchange_and_reply(fake, user, subcontractor, items):
    """受け口が単価の違う変更を断ったとき、送り合った記録の結果の列と、相手の返事の欄に単価の値が残らない (候補 11-07 の 39)。"""
    from seizou.connect.models import Exchange

    po = _send_first(subcontractor, items, user)
    _base, order = _accept_first(items, user)
    shipping_services.ship(_rows(order)[1], Decimal("10"), DAY, user)
    po = _revise(po, items)
    PurchaseOrderLine.objects.filter(purchase_order=po, line_no=1).update(unit_price=Decimal("900"), amount=Decimal("180000"))
    outbox = _send(po, user)

    assert "単価は変えられません" in outbox.response
    assert "¥" not in outbox.response
    results = list(Exchange.objects.values_list("result", flat=True))
    assert any("単価は変えられません" in text for text in results)
    assert not any("¥" in text for text in results)


@pytest.mark.django_db
def test_revision_banner_hides_prices_from_people_without_money(settings, client, fake, user, subcontractor, items):
    """受信した注文の詳細の黄色の 1 行は、金額の閲覧のある人には両方の単価を、無い人 (on) には単価の値の無い字を出す (候補 11-07 の 39)。"""
    from seizou.accounts import services as accounts_services

    revision = _price_change_waiting(subcontractor, items, user)
    url = reverse("connect:inbox_detail", args=[revision.pk])

    client.force_login(user)
    shown = client.get(url).content.decode()
    assert "(注文の単価 ¥900・受注の単価 ¥1,000)" in shown

    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}
    accounts_services.issue_account("genba", "kumo-tests-2611", [], [], {}, None)
    client.force_login(get_user_model().objects.get(username="genba"))
    hidden = client.get(url).content.decode()
    assert "単価は変えられません (注文の単価が受注の単価と違います)。" in hidden
    assert "注文の単価 ¥" not in hidden and "受注の単価 ¥" not in hidden
