"""添付ファイルを消したことを、付けた先の記録の履歴に残す (エンタープライズ化の調べ 第一弾の候補 22。依頼書 N367 の 3。本人の決め 11-08 00:37)。

- 履歴を持つ記録 (受注) に付けた添付を消すと、受注の履歴に「消した」の 1 行が増え、ファイル名・消した人・種類・大きさ・付けた人が残る。
- 乱数の保存の名前 (attachments/…) と、付けた先の種類と番号と形式は履歴に残さない。
- 付けた先の無い添付を消しても例外にしない。
作り物の受注と利用者とファイルだけを使い、上げたファイルは tmp_path の media に置く
(fixture は tests/test_attachments_services.py と tests/test_attachments_access.py の media_root の形を写した)。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from seizou.attachments import services
from seizou.attachments.models import Attachment
from seizou.core import history
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services

PDF_BYTES = b"%PDF-1.4\n% made for tests\n"
FILE_NAME = "kensa-seiseki.pdf"


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
