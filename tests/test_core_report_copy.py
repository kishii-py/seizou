"""帳票の写し (core.models.ReportCopy・core.services.keep_report_copy・report_copies) の試験。

- keep_report_copy は、作った人と作った人の名前を変更の履歴 (ChangeLog) と同じ形で書く (人の無い処理は by の字。人が None でも書ける)。
- report_copies(obj) は、その記録の写しだけを古い順に返す (同じ番号のほかの表の行を混ぜない)。
- 写しには QR の字と金額を隠す印を渡さない (納品書の写しに QR が無く、単価と金額がある)。
- 2 社の作り物の種を読み込むと、種の納品書と請求書と発注の数だけ写しがある (種は services を通るので写しができる)。
今日に依る所は無い (日付は全部渡す)。取引先・部品・会社の値は作り物だけ。HTTP は呼ばない (呼んだら落とす)。
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.urls import reverse

from seizou.billing.models import Invoice
from seizou.core import demo
from seizou.core import fields as company_fields
from seizou.core import services as core_services
from seizou.core.models import ChangeLog, ReportCopy
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing.models import PurchaseOrder
from seizou.shipping import services as shipping_services
from seizou.shipping.models import DeliveryNote
from seizou.shipping.reports import DELIVERY_NOTE_TEMPLATE, delivery_note_context

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
ORDER_DATE = date(2026, 10, 1)
SHIPPED_AT = date(2026, 10, 10)
PARTNER_BY = "作り物の得意先 C001 の seizou"


@pytest.fixture
def note(user):
    """数量 50・単価 1650 の出荷 1 つの納品書。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先 C001", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-341", "name": "作り物の軸", "unit": "個"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("50"), "unit_price": Decimal("1650")}], user, order_date=ORDER_DATE
    )
    shipment = shipping_services.ship(order.lines.get(), Decimal("50"), SHIPPED_AT, user, send=False)
    return shipping_services.create_delivery_note(customer, [shipment], user, issued_on=SHIPPED_AT)


def _keep(obj, user, **kwargs):
    with transaction.atomic():
        return core_services.keep_report_copy(
            obj, user, title="納品書", template=DELIVERY_NOTE_TEMPLATE, context=delivery_note_context(obj), **kwargs
        )


@pytest.mark.django_db
def test_actor_is_written_like_the_change_log(user, note):
    [copy] = core_services.report_copies(note)
    log = ChangeLog.objects.filter(model="shipping.deliverynote", object_pk=note.pk).get()
    assert (copy.actor, copy.actor_label) == (log.actor, log.actor_label) == (user, str(user))

    by_partner = _keep(note, None, by=PARTNER_BY)
    assert (by_partner.actor, by_partner.actor_label) == (None, PARTNER_BY)

    nobody = _keep(note, None)
    assert (nobody.actor, nobody.actor_label) == (None, "")

    anonymous = _keep(note, AnonymousUser(), by=PARTNER_BY)
    assert (anonymous.actor, anonymous.actor_label) == (None, PARTNER_BY)

    assert str(nobody) == f"{nobody.created_at:%Y-%m-%d %H:%M} {note.note_no} 納品書"


@pytest.mark.django_db
def test_report_copies_returns_only_this_record_oldest_first(user, note):
    second = _keep(note, user)
    # 同じ番号 (pk) のほかの表の行 (請求書) を混ぜない。保存していない物で、表と番号だけを持たせる
    other = Invoice(pk=note.pk, invoice_no="IV-作り物")
    with transaction.atomic():
        core_services.keep_report_copy(other, user, title="請求書", template=DELIVERY_NOTE_TEMPLATE, context=delivery_note_context(note))

    copies = list(core_services.report_copies(note))

    assert [row.pk for row in copies] == [copies[0].pk, second.pk]
    assert copies[0].pk < second.pk
    assert {row.model for row in copies} == {"shipping.deliverynote"}
    assert [row.object_text for row in core_services.report_copies(other)] == ["IV-作り物"]


@pytest.mark.django_db
def test_copy_has_no_qr_and_always_shows_money(client, user, note):
    [copy] = core_services.report_copies(note)

    assert "report-qr-small" not in copy.html
    assert "<svg" not in copy.html
    assert "¥1,650 / 個" in copy.html
    assert "¥82,500" in copy.html

    # 印刷用の画面だけが QR を足す (写しとの違い)
    client.force_login(user)
    body = client.get(reverse("shipping:deliverynote_print", args=[note.pk])).content.decode()
    assert "report-qr-small" in body


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


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_seeds_keep_a_copy_per_issued_report(company_id, no_registration, media, no_http):
    demo.load(COMPANIES_DIR / company_id)

    counts = {
        "shipping.deliverynote": DeliveryNote.objects.count(),
        "billing.invoice": Invoice.objects.count(),
        "purchasing.purchaseorder": PurchaseOrder.objects.count(),
    }
    assert counts["shipping.deliverynote"] and counts["billing.invoice"]  # 発注の種は A 社だけ
    assert {model: ReportCopy.objects.filter(model=model).count() for model in counts} == counts
    assert ReportCopy.objects.count() == sum(counts.values())
