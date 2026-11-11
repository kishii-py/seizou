# 発注の明細に、人が付け外しする「完了」の印の列を足す (見比べのまとめの M03。本人の決め 11-11 08:4x・09:3x)。
# 今ある明細は全部、印の無い (完了でない) 明細として入る。印が偽なら受入の残の数え方は今と同じ (RULES.md R25)。

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0012_c111_self_invoice"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorderline",
            name="completed",
            field=models.BooleanField(default=False, editable=False, verbose_name="完了"),
        ),
    ]
