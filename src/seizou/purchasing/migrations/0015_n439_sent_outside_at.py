# 発注の表に「発注書を送った日時」の列を足す (依頼書 N439。本人の決め 11-12 14:3x・18:2x)。
# 今ある発注は全部、列が空 (= 発注書を送ったと記録していない) のまま入る。状態の値は変えない。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0014_m01_subcontract_step'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='sent_outside_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='発注書を送った日時'),
        ),
    ]
