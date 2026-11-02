"""R16 の違反の見本 (会社の側に部品の app 名の migration がある)。検査は中身を読まず、場所だけを見る。"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("masters", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="item",
            name="material",
            field=models.CharField(max_length=100, blank=True),
        ),
    ]
