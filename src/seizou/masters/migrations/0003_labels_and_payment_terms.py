# 初見テストと E2E の所見で、画面の字 (verbose_name) をそろえ、支払の条件を 2 列に分ける。
#
# - 品目 → 部品、標準単価 → 標準単価 (円)、区分 → 取引先の種類、
#   小計の計算方法 → 金額の端数の扱い、消費税の計算方法 → 消費税の端数の扱い。
# - 支払日 (2 文字の字) を 支払の月 (当月・翌月・翌々月) と 支払日 (1〜31 の数。31 は月末) に分ける。
#   字の列を数の列に直に変えると、PostgreSQL は「末」で止まり、SQLite は字のまま残すので、
#   数の列を仮の名前で足して値を移し、元の列を外してから名前を戻す。

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

MONTH_END_DAY = 31


def split_payment_day(apps, schema_editor):
    """元の字が数字だけなら 1〜31 の数に (外れは空)、「末」を含めば 31、ほかは空。「翌」で始まれば支払の月を翌月に。"""
    Partner = apps.get_model("masters", "Partner")
    for partner in Partner.objects.all():
        text = (partner.payment_day or "").strip()
        day = None
        if text.isdigit():
            number = int(text)
            day = number if 1 <= number <= MONTH_END_DAY else None
        elif "末" in text:
            day = MONTH_END_DAY
        partner.payment_day_number = day
        partner.payment_month = "next" if text.startswith("翌") else ""
        partner.save(update_fields=["payment_day_number", "payment_month"])


def join_payment_day(apps, schema_editor):
    """戻すとき: 31 は「末」、ほかは数の字。翌月なら頭に「翌」を付ける (2 文字に収まる時だけ)。"""
    Partner = apps.get_model("masters", "Partner")
    for partner in Partner.objects.all():
        day = partner.payment_day_number
        text = "" if day is None else ("末" if day == MONTH_END_DAY else str(day))
        if partner.payment_month == "next" and len(text) == 1:
            text = "翌" + text
        partner.payment_day = text
        partner.save(update_fields=["payment_day"])


class Migration(migrations.Migration):
    dependencies = [
        ("masters", "0002_partner_role_label"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="item",
            options={"ordering": ["part_no"], "verbose_name": "部品", "verbose_name_plural": "部品"},
        ),
        migrations.AlterField(
            model_name="item",
            name="standard_price",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=14, null=True, verbose_name="標準単価 (円)"
            ),
        ),
        migrations.AlterField(
            model_name="itemprocess",
            name="item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="item_processes",
                to="masters.item",
                verbose_name="部品",
            ),
        ),
        migrations.AlterField(
            model_name="partner",
            name="role",
            field=models.CharField(
                choices=[("customer", "得意先"), ("subcontractor", "外注先"), ("both", "両方")],
                max_length=15,
                verbose_name="取引先の種類",
            ),
        ),
        migrations.AlterField(
            model_name="partner",
            name="rounding",
            field=models.CharField(
                blank=True,
                choices=[("round", "四捨五入"), ("ceil", "切上"), ("floor", "切捨")],
                max_length=10,
                verbose_name="金額の端数の扱い",
            ),
        ),
        migrations.AlterField(
            model_name="partner",
            name="tax_mode",
            field=models.CharField(
                blank=True,
                choices=[("total", "トータル"), ("line_round", "各行四捨五入"), ("line_floor", "各行切捨")],
                max_length=10,
                verbose_name="消費税の端数の扱い",
            ),
        ),
        # --- 支払の条件を 2 列に分ける ---
        migrations.AddField(
            model_name="partner",
            name="payment_month",
            field=models.CharField(
                blank=True,
                choices=[("current", "当月"), ("next", "翌月"), ("after_next", "翌々月")],
                default="",
                max_length=10,
                verbose_name="支払の月",
            ),
        ),
        migrations.AddField(
            model_name="partner",
            name="payment_day_number",
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="支払日"),
        ),
        migrations.RunPython(split_payment_day, join_payment_day),
        migrations.RemoveField(
            model_name="partner",
            name="payment_day",
        ),
        migrations.RenameField(
            model_name="partner",
            old_name="payment_day_number",
            new_name="payment_day",
        ),
        migrations.AlterField(
            model_name="partner",
            name="payment_day",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(31),
                ],
                verbose_name="支払日",
            ),
        ),
    ]
