"""2 社の出荷の知らせと検収の種 (connect.demo_seed。core の demo.load の PART_SEEDS が呼ぶ) と、
受信した注文を受け口を通さずに入れる関数 (connect.services.record_inbox)。

例の種 (examples/companies/<id>/) を demo.load で入れ、2 回目は飛ばすこと、A 社の発注の明細に出荷の知らせが付き
発注が「相手が受け入れた」に進むこと、B 社に受信した注文の写しと受注と検収が入ること、種の指す物が他の種にあること
(2 社の種の突き合わせ) を見る。HTTP は呼ばない (呼んだら落とす)。取引先は作り物。
"""

import csv
import datetime
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from django.contrib.auth import get_user_model
from django.utils import timezone

from seizou.connect import demo_seed, services
from seizou.connect.models import Acceptance, Inbox, Outbox, ShippingNotice
from seizou.core import demo
from seizou.core import fields as company_fields
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.orders.models import Order
from seizou.purchasing.models import PurchaseOrder, Receipt
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
#: 種の出荷の知らせ・受信した注文の写し・検収の件数 (A 社 = 出荷の知らせ 3、B 社 = 受信した注文 1・検収 2)
EXPECTED = {"a_machining": 3, "b_plating": 3}
KIND = "出荷の知らせと検収"


@pytest.fixture
def no_registration(monkeypatch):
    """試験用の settings には会社ごとの項目の登録が無い。ほかの試験の登録が残っていても空にする。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("種の読み込みで HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.mark.django_db
def test_record_inbox_makes_row_once(user):
    customer = masters_services.create_partner({"code": "C901", "name": "作り物の発注元", "role": "customer"}, user)

    inbox = services.record_inbox(customer, {"order_no": "X-1", "lines": []}, user)

    assert isinstance(inbox, Inbox)
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.kind == Inbox.KIND_ORDER
    assert inbox.partner == customer
    assert inbox.payload == {"order_no": "X-1", "lines": []}
    assert isinstance(inbox.message_id, uuid.UUID)

    again = services.record_inbox(customer, {"order_no": "X-2", "lines": []}, user, message_id=inbox.message_id)

    assert again.pk == inbox.pk
    assert again.payload == {"order_no": "X-1", "lines": []}  # 2 回目は上書きしない
    assert Inbox.objects.count() == 1

    with pytest.raises(ValueError, match="辞書"):
        services.record_inbox(customer, "X-3", user)
    assert Inbox.objects.count() == 1


@pytest.mark.django_db
def test_record_inbox_takes_received_at(user):
    customer = masters_services.create_partner({"code": "C901", "name": "作り物の発注元", "role": "customer"}, user)
    received_at = timezone.make_aware(datetime.datetime(2026, 10, 28, 10, 15))

    inbox = services.record_inbox(customer, {"order_no": "X-1", "lines": []}, user, received_at=received_at)

    assert Inbox.objects.get(pk=inbox.pk).received_at == received_at

    # 同じ番号の 2 回目は 1 回目の行をそのまま返す (届いた日時も書き直さない)
    later = timezone.make_aware(datetime.datetime(2026, 10, 29, 9, 0))
    again = services.record_inbox(
        customer, {"order_no": "X-1", "lines": []}, user, message_id=inbox.message_id, received_at=later
    )
    assert again.pk == inbox.pk
    assert Inbox.objects.get(pk=inbox.pk).received_at == received_at

    # timezone の無い日時は断る
    with pytest.raises(ValueError, match="timezone 付きの日時"):
        services.record_inbox(customer, {"order_no": "X-2", "lines": []}, user, received_at=datetime.datetime(2026, 10, 28, 10, 15))
    assert Inbox.objects.count() == 1

    # 渡さなければ今の日時
    before = timezone.now()
    plain = services.record_inbox(customer, {"order_no": "X-3", "lines": []}, user)
    assert abs(Inbox.objects.get(pk=plain.pk).received_at - before) < datetime.timedelta(minutes=1)


def _edi_counts():
    return (
        Inbox.objects.count(),
        Acceptance.objects.count(),
        ShippingNotice.objects.count(),
        Order.objects.count(),
    )


def _check_a_machining():
    notices = {
        (n.line.line_no, n.qty, n.shipped_at, n.shipment_no, n.supplier_order_no)
        for n in ShippingNotice.objects.select_related("line")
    }
    assert notices == {
        (1, Decimal("200"), datetime.date(2026, 10, 29), "SH-000022", "SO-000021"),
        (2, Decimal("40"), datetime.date(2026, 11, 1), "SH-000023", "SO-000021"),
        (2, Decimal("60"), datetime.date(2026, 11, 3), "SH-000024", "SO-000021"),
    }
    order = PurchaseOrder.objects.get()
    assert order.status == PurchaseOrder.STATUS_PARTIAL  # 受入の後に注文回答の写しが入るので一部納品
    assert order.replied_at is not None
    assert order.reply_message_id is None  # 相手の注文回答の番号は無い (写し)
    assert Receipt.objects.count() == 2
    assert all(receipt.acceptance_sent_at is None for receipt in Receipt.objects.all())  # 検収を送っていない
    assert Inbox.objects.count() == 0
    assert Acceptance.objects.count() == 0


def _check_b_plating():
    inbox = Inbox.objects.get()
    assert inbox.status == Inbox.STATUS_ACCEPTED
    assert inbox.partner.code == "C001"
    assert inbox.summary() == "WK-1001 ×200 ほか 1 行"
    assert timezone.localtime(inbox.received_at).strftime("%Y-%m-%d %H:%M") == "2026-10-28 10:15"  # 種に書いた届いた日時
    assert inbox.reply_sent_at is None  # 注文回答は送っていない (種の受け入れは send=False)
    assert inbox.reply_response == services.MESSAGE_REPLY_SEED
    assert inbox.reply_message_id is None
    order = inbox.order
    assert order.order_no == "SO-000021"
    assert order.source == Order.SOURCE_INBOX
    assert order.customer_order_no == "PO-000001"
    lines = list(order.lines.select_related("item").order_by("line_no"))
    assert [line.customer_part_no for line in lines] == ["WK-1001", "WK-1004"]
    assert [line.item.part_no for line in lines] == ["SB-2001", "SB-2028"]
    assert [line.unit_price for line in lines] == [Decimal("45"), Decimal("60")]
    acceptances = {
        (a.order_line.line_no, a.qty, a.accepted_on, a.buyer_order_no, a.buyer_line_no)
        for a in Acceptance.objects.select_related("order_line")
    }
    assert acceptances == {
        (1, Decimal("200"), datetime.date(2026, 10, 30), "PO-000001", 1),
        (2, Decimal("40"), datetime.date(2026, 11, 2), "PO-000001", 2),
    }
    assert Acceptance.objects.filter(inbox=inbox).count() == 2
    assert orders_services.acceptance_summary(lines[1])["total"] == Decimal("40")
    assert Order.objects.count() == 21
    # 受信した注文の写しの受注の出荷 (出荷の種の末尾 3 件)。番号は A 社の種の相手の出荷番号と同じ
    shipments = list(Shipment.objects.filter(order_line__order=order).select_related("order_line").order_by("shipment_no"))
    assert [(s.shipment_no, s.order_line.line_no, s.qty, s.shipped_at) for s in shipments] == [
        ("SH-000022", 1, Decimal("200"), datetime.date(2026, 10, 29)),
        ("SH-000023", 2, Decimal("40"), datetime.date(2026, 11, 1)),
        ("SH-000024", 2, Decimal("60"), datetime.date(2026, 11, 3)),
    ]
    # 出荷の知らせは送っていない (種の出荷は send=False。番号も付けない)
    assert all(s.notice_message_id is None and s.notice_sent_at is None for s in shipments)
    assert all(s.notice_response == shipping_services.MESSAGE_NOTICE_SEED for s in shipments)
    # 3 件とも同じ 1 枚の発行の納品書に入っている
    assert len({s.delivery_note_id for s in shipments}) == 1
    assert shipments[0].delivery_note.status == "issued"
    assert order.status == Order.STATUS_SHIPPED
    assert ShippingNotice.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_examples_load_edi_seeds_twice(company_id, no_registration, media, no_http, capsys):
    made = EXPECTED[company_id]

    summary = demo.load(COMPANIES_DIR / company_id)

    assert summary[KIND] == {"入れた": made, "飛ばした": 0}
    assert f"{KIND}: 入れた {made} 件・飛ばした 0 件" in capsys.readouterr().out
    assert len(summary["注意"]) == 1  # 例の種では「注意」の文を増やさない
    assert Outbox.objects.count() == 0  # 相手に送っていない
    if company_id == "a_machining":
        _check_a_machining()
        replied_at = PurchaseOrder.objects.get().replied_at
        # 注文回答の写しの日時は、種を読んだ時刻でなく、発注日 (10-28) の翌日の 9:00 (精査 A9 の 3 節の 2)
        assert timezone.localtime(replied_at) == timezone.make_aware(datetime.datetime(2026, 10, 29, 9, 0))
    else:
        _check_b_plating()
    counts = _edi_counts()

    again = demo.load(COMPANIES_DIR / company_id)

    assert again[KIND] == {"入れた": 0, "飛ばした": made}
    assert _edi_counts() == counts
    assert Outbox.objects.count() == 0
    if company_id == "a_machining":
        assert PurchaseOrder.objects.get().replied_at == replied_at  # 2 回目は状態を進め直さない


@pytest.mark.django_db
def test_seed_without_files_is_silent(tmp_path):
    assert demo_seed.load_after_orders(tmp_path, None) == {"入れた": 0, "飛ばした": 0}
    assert demo_seed.load(tmp_path, None) == {"入れた": 0, "飛ばした": 0}

    # 受信した注文の写しは取引先の種を指し、出荷の知らせは受入の種の発注を指すので、それらの無い置き場では
    # ValueError で止まる (黙って飛ばさない)。受信した注文の写しは受注の後の口 (load_after_orders) が読む
    cases = (
        ("b_plating", "acceptances.yaml", demo_seed.load_after_orders),
        ("a_machining", "shipping_notices.yaml", demo_seed.load),
    )
    for company_id, name, load_seed in cases:
        folder = tmp_path / company_id
        folder.mkdir()
        shutil.copy(COMPANIES_DIR / company_id / name, folder / name)
        with pytest.raises(ValueError, match=f"{name} の 1 件目"):
            load_seed(folder, None)
    # 部品の種の段の load は受信した注文の写しを読まない
    assert demo_seed.load(tmp_path / "b_plating", None) == {"入れた": 0, "飛ばした": 0}
    assert Inbox.objects.count() == 0
    assert ShippingNotice.objects.count() == 0


def _yaml(path, root):
    return yaml.safe_load(path.read_text(encoding="utf-8"))[root]


def _csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _check_a_references(folder):
    assert not (folder / "acceptances.yaml").exists()
    receipts = {(order["取引先コード"], order["備考"]): order for order in _yaml(folder / "receipts.yaml", "発注")}
    for entry in _yaml(folder / "shipping_notices.yaml", "発注"):
        order = receipts[(entry["取引先コード"], entry["備考"])]
        notices = entry["出荷の知らせ"]
        assert all(1 <= notice["行番号"] <= len(order["明細"]) for notice in notices)
        shipment_nos = [notice["相手の出荷番号"] for notice in notices]
        assert len(shipment_nos) == len(set(shipment_nos))
        by_line_qty = {(notice["行番号"], notice["数量"]): notice for notice in notices}
        for receipt in order["受入"]:
            notice = by_line_qty[(receipt["行番号"], receipt["数量"])]  # 受入のある知らせ
            assert notice["出荷日"] < receipt["受入日"]  # 受入の前の日


def _check_b_references(folder):
    assert not (folder / "shipping_notices.yaml").exists()
    a_folder = COMPANIES_DIR / "a_machining"
    a_name = yaml.safe_load((COMPANIES_DIR / "a_machining.yaml").read_text(encoding="utf-8"))["name"]
    partner_names = {row["取引先コード"]: row["名前"] for row in _csv(folder / "partners.csv")}
    partner_items = {(row["取引先コード"], row["取引先の品番"]) for row in _csv(folder / "partner_items.csv")}
    customer_order_nos = {order["得意先の注文番号"] for order in _yaml(folder / "orders.yaml", "受注")}
    a_order = _yaml(a_folder / "receipts.yaml", "発注")[0]
    for entry in _yaml(folder / "acceptances.yaml", "受信した注文"):
        code = entry["取引先コード"]
        assert partner_names[code] == a_name
        assert all((code, line["相手の品番"]) in partner_items for line in entry["明細"])
        assert entry["相手の発注番号"] not in customer_order_nos  # 受注の種と重ならない
        # 2 社の種の突き合わせ: 受信した注文は A 社の発注と同じ中身、検収は A 社の受入と同じ日と数
        assert entry["発注日"] == a_order["発注日"]
        assert entry["届いた日時"].date() == entry["発注日"]  # 届いた日時は発注日と同じ日
        assert [(line["相手の品番"], line["数量"], line["単価"], line["納期"]) for line in entry["明細"]] == [
            (line["自社品番"], line["数量"], line["単価"], line["納期"]) for line in a_order["明細"]
        ]
        assert [(a["行番号"], a["検収日"], a["数量"]) for a in entry["検収"]] == [
            (r["行番号"], r["受入日"], r["数量"]) for r in a_order["受入"]
        ]
    # 2 社の種の出荷の突き合わせ: 受信した注文の写しの受注の出荷は、A 社の出荷の知らせと同じ行と日と数で、
    # 新しい置き場の出荷番号 (出荷の種の並びの順の連番) が A 社の相手の出荷番号と同じ
    inbox_keys = {(entry["取引先コード"], entry["相手の発注番号"]) for entry in _yaml(folder / "acceptances.yaml", "受信した注文")}
    seeded = [
        (position, s)
        for position, s in enumerate(_yaml(folder / "shipments.yaml", "出荷"), start=1)
        if (s["得意先の取引先コード"], s["得意先の注文番号"]) in inbox_keys
    ]
    a_notices = _yaml(a_folder / "shipping_notices.yaml", "発注")[0]["出荷の知らせ"]
    assert [(s["行番号"], s["出荷日"], s["数量"]) for _position, s in seeded] == [
        (n["行番号"], n["出荷日"], n["数量"]) for n in a_notices
    ]
    assert [f"SH-{position:06d}" for position, _s in seeded] == [n["相手の出荷番号"] for n in a_notices]
    # 納品書の種に、その出荷だけを指す 1 件がある
    refs = sorted((s["得意先の注文番号"], s["行番号"], str(s["出荷日"])) for _position, s in seeded)
    notes = [
        sorted((r["得意先の注文番号"], r["行番号"], str(r["出荷日"])) for r in note["出荷"])
        for note in _yaml(folder / "delivery_notes.yaml", "納品書")
    ]
    assert notes.count(refs) == 1


@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_seed_references_exist(company_id):
    folder = COMPANIES_DIR / company_id
    if company_id == "a_machining":
        _check_a_references(folder)
    else:
        _check_b_references(folder)


@pytest.mark.django_db
def test_b_plating_seed_accepted_at_is_seed_time(no_registration, media, no_http):
    demo.load(COMPANIES_DIR / "b_plating")  # seizou new --demo が呼ぶ物

    inbox = Inbox.objects.get()
    assert timezone.localtime(inbox.accepted_at).strftime("%Y-%m-%d %H:%M") == "2026-10-28 10:40"  # 種に書いた承認した日時
    assert datetime.timedelta(0) <= inbox.accepted_at - inbox.received_at < datetime.timedelta(days=1)
    accepted_at = inbox.accepted_at

    demo.load(COMPANIES_DIR / "b_plating")

    assert Inbox.objects.get().accepted_at == accepted_at  # 2 回目は受信した注文を飛ばし、書き直さない
    # 種の約束: 届いた日時 <= 承認した日時 < 届いた日時 + 1 日
    for entry in _yaml(COMPANIES_DIR / "b_plating" / "acceptances.yaml", "受信した注文"):
        assert entry["届いた日時"] <= entry["承認した日時"] < entry["届いた日時"] + datetime.timedelta(days=1)
    # timezone の無い日時は、受け入れ済みの行でも状態を見る前に断る
    with pytest.raises(ValueError, match="承認した日時は timezone 付きの日時"):
        services.accept(inbox, {}, None, send=False, accepted_at=datetime.datetime(2026, 10, 28, 10, 40))
