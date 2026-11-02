"""R16 の違反しない見本 (会社の fields の app の migration)。検査は中身を読まず、場所だけを見る。"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("masters", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="ItemExt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ext01", models.CharField(blank=True, max_length=100, verbose_name="材質")),
                (
                    "item",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ext",
                        to="masters.item",
                        verbose_name="品目",
                    ),
                ),
            ],
        ),
    ]
