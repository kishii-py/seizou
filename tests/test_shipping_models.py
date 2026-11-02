"""shipping の表の形 (01 の 4 節。文字数の上限と日本語の名前と、受注の明細からの逆向きの名前)。"""

import re

from django.db import models

from seizou.orders.models import OrderLine
from seizou.shipping.models import DeliveryNote, Shipment

SHIPPING_MODELS = [DeliveryNote, Shipment]
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def _own_fields(model):
    return [field for field in model._meta.fields if not field.auto_created]


def test_models_have_japanese_verbose_names_and_limits():
    # 識別子 35・備考 500・コード 10 (納品書の状態)。
    allowed = {35, 500, 10}
    for model in SHIPPING_MODELS:
        assert JAPANESE.search(str(model._meta.verbose_name)), model.__name__
        for field in _own_fields(model):
            label = f"{model.__name__}.{field.name}"
            assert JAPANESE.search(str(field.verbose_name)), label
            if isinstance(field, models.CharField):
                assert field.max_length in allowed, label


def test_related_name_is_shipments():
    # orders.services.shipped_qty は、受注の明細の shipments で出荷を集計する。
    from_line = OrderLine._meta.get_field("shipments")
    assert from_line.related_model is Shipment
    assert from_line.field.name == "order_line"

    from_note = DeliveryNote._meta.get_field("shipments")
    assert from_note.related_model is Shipment
    assert from_note.field.name == "delivery_note"
