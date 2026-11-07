"""添付ファイルを消したことを、付けた先の記録の履歴に残す (エンタープライズ化の調べ 第一弾の候補 22。依頼書 N367 の 3。本人の決め 11-08 00:37)。

- 履歴を持つ記録 (受注) に付けた添付を消すと、受注の履歴に「消した」の 1 行が増え、ファイル名・消した人・種類・大きさ・付けた人が残る。
- 乱数の保存の名前 (attachments/…) と、付けた先の種類と番号と形式は履歴に残さない。
- 付けた先の無い添付を消しても例外にしない。
- 金額が主な記録 (請求書など。attachments.services.MONEY_TARGETS) に付けた添付は、金額を見られない人 (money が on で閲覧の無い人) には
  一覧・開く・削除・移るだけの口・詳細の枠のどれにも出さない (404。候補 6 の (C)。本人の決め 11-08 00:13)。log の会社では開けて、
  seizou.access に 1 行残る。off の会社では誰でも開ける。
作り物の受注と利用者とファイルだけを使い、上げたファイルは tmp_path の media に置く
(fixture は tests/test_attachments_services.py と tests/test_attachments_access.py の media_root の形を写した)。
"""

import logging
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.attachments import services
from seizou.attachments.models import Attachment
from seizou.billing import services as billing_services
from seizou.core import history
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped

PDF_BYTES = b"%PDF-1.4\n% made for tests\n"
FILE_NAME = "kensa-seiseki.pdf"
INVOICE_FILE = "seikyu-meisai.pdf"
ORDER_FILE = "juchu-shashin.pdf"
PASSWORD = "kumo-tests-2611"
#: 金額の閲覧だけを効かせる (部署の判定と編集の権限は見ない)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
MONEY_LOG = {"department": "off", "write": "off", "money": "log"}
MONEY_OFF = {"department": "off", "write": "off", "money": "off"}


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, item):
    return orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 2)
    )


def _attach(target, person, name=FILE_NAME, kind="inspection"):
    return services.add_attachment(target, SimpleUploadedFile(name, PDF_BYTES), person, kind=kind)


# ---------------------------------------------------------------- 3: 消したことを履歴に残す


@pytest.mark.django_db
def test_remove_leaves_deleted_row_in_target_history(user, order, django_user_model):
    """(a) 受注の履歴に「消した」の 1 行が増え、行の字にファイル名、人に消した人、前の値に種類・大きさ・付けた人がある。"""
    attachment = _attach(order, user)
    remover = django_user_model.objects.create_user("keshita-hito", password="pass-for-tests-only")
    before = history.timeline(order, user).count

    services.remove_attachment(attachment, remover)

    timeline = history.timeline(order, user)
    assert timeline.count == before + 1
    row = timeline.rows[0]
    assert row["action"] == ChangeLog.ACTION_DELETED
    assert FILE_NAME in row["title"] and row["title"].endswith("を消した")
    assert row["who"] == "keshita-hito"
    values = {item["label"]: item["before"] for item in row["items"]}
    assert values["種類"] == "検査成績書"
    assert values["ファイル名"] == FILE_NAME
    assert values["大きさ (バイト)"] == str(len(PDF_BYTES))
    assert values["付けた人"] == user.get_username()


@pytest.mark.django_db
def test_deleted_row_keeps_no_stored_name_or_type(user, order):
    """(b) 前の値に、保存の名前 (attachments/…) と付けた先の種類と番号と形式が無い。"""
    attachment = _attach(order, user)
    stored = attachment.file.name
    assert stored.startswith("attachments/")

    services.remove_attachment(attachment, user)

    entry = ChangeLog.objects.get(model="attachments.attachment", action=ChangeLog.ACTION_DELETED)
    assert (entry.owner_model, entry.owner_pk) == ("orders.order", order.pk)
    fields = {item["field"] for item in entry.changes}
    assert not fields & {"file", "content_type", "object_id", "mime"}
    text = repr(entry.changes)
    assert "attachments/" not in text and stored.rsplit("/", 1)[-1] not in text
    assert "application/pdf" not in text


@pytest.mark.django_db
def test_remove_attachment_without_target_does_not_raise(user, customer):
    """(c) 付けた先の無い添付を消しても例外にならない (履歴の行は添付そのものを持ち主にして表に残る)。"""
    attachment = _attach(customer, user, name="torihikisaki.pdf", kind="other")
    Attachment.objects.filter(pk=attachment.pk).update(object_id=999999)
    orphan = Attachment.objects.get(pk=attachment.pk)

    assert services.remove_attachment(orphan, user) is None
    assert not Attachment.objects.filter(pk=attachment.pk).exists()
    entry = ChangeLog.objects.get(model="attachments.attachment", action=ChangeLog.ACTION_DELETED)
    assert (entry.owner_model, entry.owner_pk) == ("attachments.attachment", attachment.pk)


# ---------------------------------------------------------------- 4: 金額が主な記録の添付


@pytest.fixture
def invoice(user, customer, order):
    shipment = shipped(user, customer, order.lines.get(), "10")
    return billing_services.create_invoice(customer, [shipment], user, issued_on=date(2026, 11, 30))


@pytest.fixture
def files(user, order, invoice):
    """請求書に付けた添付と、受注に付けた添付。"""
    return {"invoice": _attach(invoice, user, name=INVOICE_FILE), "order": _attach(order, user, name=ORDER_FILE, kind="photo")}


def _account(login_id, **flags):
    accounts_services.issue_account(login_id, PASSWORD, [], [], flags, None)
    return get_user_model().objects.get(username=login_id)


def _client(person):
    client = Client()
    client.force_login(person)
    return client


def _download(client, attachment):
    response = client.get(reverse("attachments:attachment_download", args=[attachment.pk]))
    if response.status_code == 200:
        b"".join(response.streaming_content)  # 読み切って閉じる (tests/test_attachments_services.py の body_of と同じ訳)
    return response.status_code


def _panel(client, url):
    body = client.get(url).content.decode()
    start = body.index('<section id="attachments"')
    return body[start : body.index("</section>", start)]


@pytest.mark.django_db
def test_money_record_files_hidden_without_money(settings, files, invoice, order):
    """(e) 金額を見られない人には、請求書の添付は一覧に出ず、開く・削除・移るだけの口が 404、請求書の詳細の枠にも出ない。受注の添付は出る。"""
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client = _client(_account("genba"))
    on_invoice, on_order = files["invoice"], files["order"]

    listing = client.get(reverse("attachments:attachment_list")).content.decode()
    assert INVOICE_FILE not in listing and ORDER_FILE in listing
    assert _download(client, on_invoice) == 404
    assert client.get(reverse("attachments:attachment_delete", args=[on_invoice.pk])).status_code == 404
    assert client.get(reverse("attachments:attachment_detail", args=[on_invoice.pk])).status_code == 404
    assert INVOICE_FILE not in _panel(client, reverse("billing:invoice_detail", args=[invoice.pk]))
    assert _download(client, on_order) == 200
    assert ORDER_FILE in _panel(client, reverse("orders:order_detail", args=[order.pk]))


@pytest.mark.django_db
def test_money_record_files_shown_with_money(settings, files, invoice):
    """(f) 金額の閲覧のある人には、請求書の添付も一覧・開く・削除の画面・移るだけの口・詳細の枠に出る。"""
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client = _client(_account("keiri", see_money=True))
    on_invoice = files["invoice"]

    assert INVOICE_FILE in client.get(reverse("attachments:attachment_list")).content.decode()
    assert _download(client, on_invoice) == 200
    assert client.get(reverse("attachments:attachment_delete", args=[on_invoice.pk])).status_code == 200
    moved = client.get(reverse("attachments:attachment_detail", args=[on_invoice.pk]))
    assert moved.status_code == 302 and moved["Location"] == reverse("billing:invoice_detail", args=[invoice.pk])
    assert INVOICE_FILE in _panel(client, reverse("billing:invoice_detail", args=[invoice.pk]))


@pytest.mark.django_db
def test_money_log_opens_and_records(settings, caplog, files):
    """(g) money が log では、金額の閲覧の無い人も請求書の添付を開けて、seizou.access に「金額が主な記録の添付を開いた」の 1 行が出る。"""
    settings.SEIZOU = {"access_control": dict(MONEY_LOG)}
    client = _client(_account("genba"))
    caplog.set_level(logging.WARNING, logger="seizou.access")

    assert _download(client, files["invoice"]) == 200

    lines = [r.getMessage() for r in caplog.records if r.name == "seizou.access" and "金額が主な記録の添付を開いた" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].startswith("記録だけ (金額の閲覧): genba が ")


@pytest.mark.django_db
def test_money_off_opens_for_everyone(settings, files):
    """(h) money が off では、金額の閲覧の無い人も請求書の添付を開ける (今のまま)。"""
    settings.SEIZOU = {"access_control": dict(MONEY_OFF)}
    client = _client(_account("genba"))

    assert _download(client, files["invoice"]) == 200
    assert INVOICE_FILE in client.get(reverse("attachments:attachment_list")).content.decode()
