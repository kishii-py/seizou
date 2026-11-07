# 請求書の明細が指す受注の明細 (決め PRICE-1d の edi。出荷に結び付かない一式の行も、受注の明細を指す)。1 本目 = 列を空を許して足し、行に写す。
#
# 今ある明細は全部が出荷の行。受注の明細は出荷の受注の明細を写す。単価の単位は部品の単位 (今ある明細は数量 × 単価で作った)。
# 数量・単価・金額は変えない (保存した取引の記録の値を書き換えない)。
# 行の書き換え (RunPython) と表の変更 (空を許さない形に直す・制約を足す) は 2 本目 (0005) に分ける。
# PostgreSQL では外部キーの検査が transaction の終わりまで待つ形なので、同じ migration で行を書き換えた後に表を変えると止まるため。1 つにまとめ直さない。

import django.db.models.deletion
from django.db import migrations, models


def copy_order_line(apps, schema_editor):
    """今ある明細の受注の明細に、その出荷の受注の明細を写す (受注の明細ごとに 1 回の update。1 行ずつ save しない)。"""
    InvoiceLine = apps.get_model("billing", "InvoiceLine")
    Shipment = apps.get_model("shipping", "Shipment")
    by_order_line = {}
    pairs = Shipment.objects.filter(pk__in=InvoiceLine.objects.values("shipment_id")).values_list("pk", "order_line_id")
    for shipment_id, order_line_id in pairs:
        by_order_line.setdefault(order_line_id, []).append(shipment_id)
    for order_line_id, shipment_ids in by_order_line.items():
        InvoiceLine.objects.filter(shipment_id__in=shipment_ids).update(order_line_id=order_line_id)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0003_wording_20261107'),
        ('orders', '0006_c99_orderline_price_basis'),
        ('shipping', '0003_c107_shipment_finishes_line'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoiceline',
            name='order_line',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='invoice_lines', to='orders.orderline', verbose_name='受注の明細'),
        ),
        migrations.RunPython(copy_order_line, migrations.RunPython.noop),
    ]
