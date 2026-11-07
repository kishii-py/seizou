# 請求書の明細の 2 つの形 (出荷の行と一式の行。決め PRICE-1d)。2 本目 = 表の変更だけ (行の書き換えは 1 本目の 0004)。
#
# 今ある明細は全部が出荷の行。受注の明細は出荷の受注の明細を写す (0004)。単価の単位は部品の単位 (今ある明細は数量 × 単価で作った)。
# 数量・単価・金額は変えない (保存した取引の記録の値を書き換えない)。
# 受注の明細を空を許さない形に直し、出荷を空を許す形 (空 = 一式の行) にし、単価の単位の写しと最後の出荷日の列と、2 つの形を守る制約を足す。

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0004_c107_invoiceline_order_line'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invoiceline',
            name='order_line',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoice_lines', to='orders.orderline', verbose_name='受注の明細'),
        ),
        migrations.AlterField(
            model_name='invoiceline',
            name='shipment',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='invoice_lines', to='shipping.shipment', verbose_name='出荷'),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='price_basis',
            field=models.CharField(choices=[('unit', '部品の単位'), ('lump', '式')], default='unit', max_length=10, verbose_name='単価の単位'),
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='last_shipped_at',
            field=models.DateField(blank=True, null=True, verbose_name='最後の出荷日'),
        ),
        migrations.AddConstraint(
            model_name='invoiceline',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('last_shipped_at__isnull', True), ('shipment__isnull', False)), models.Q(('last_shipped_at__isnull', False), ('price_basis', 'lump'), ('shipment__isnull', True)), _connector='OR'), name='billing_invoiceline_shipment_or_lump'),
        ),
        migrations.AddConstraint(
            model_name='invoiceline',
            constraint=models.UniqueConstraint(condition=models.Q(('shipment__isnull', True)), fields=('invoice', 'order_line'), name='billing_invoiceline_invoice_lump_unique'),
        ),
    ]
