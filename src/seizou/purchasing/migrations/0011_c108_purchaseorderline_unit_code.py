# 発注の明細に数量単位コードの列を足し、今の明細の単位の字が数量単位の一覧の名前に当たればコードを入れる (決め UNIT-1 = a)。
#
# 発注の明細は取引の記録なので、今の単位の字は変えず、新しい列に値を入れるだけにする。
# 訳: 電子帳簿保存法の電子取引のデータの保存では、保存した取引のデータを直したら、その履歴が残る仕組みが求められる
# (中小企業共通 EDI ガイドブック 3.2.2「注文回答の扱いについて」) ので、履歴の外で記録の字を直さない。

import unicodedata

from django.db import migrations, models

# カタカナ (ァ〜ヶ) をひらがなに写す表 (masters.services と同じ。migration は今の services を import しない)。
_KATAKANA_TO_HIRAGANA = {code: code - 0x60 for code in range(0x30A1, 0x30F7)}


def _fold(text):
    """照合のために字を畳む。全角と半角、カタカナとひらがな、大文字と小文字を同じにする (masters.services._fold と同じ)。"""
    text = unicodedata.normalize("NFKC", text)
    return text.translate(_KATAKANA_TO_HIRAGANA).casefold()


def fill_line_codes(apps, schema_editor):
    """単位の字のある明細の字を畳み、数量単位の一覧の名前のどれかと全部が同じなら、コードを入れる。当たらない字はコードを空のまま。

    電子帳簿保存法の電子取引のデータの保存のため、発注の明細の今の単位の字は直さず、新しい列にだけ値を入れる。
    """
    QuantityUnit = apps.get_model("masters", "QuantityUnit")
    PurchaseOrderLine = apps.get_model("purchasing", "PurchaseOrderLine")
    codes = {_fold(name): code for code, name in QuantityUnit.objects.values_list("code", "name")}
    for line in PurchaseOrderLine.objects.exclude(unit=""):
        code = codes.get(_fold((line.unit or "").strip()))
        if code:
            line.unit_code = code
            line.save(update_fields=["unit_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("purchasing", "0010_merge_c96_c100"),
        # 数量の単位の一覧の表と 40 行 (この migration が読む)
        ("masters", "0016_c108_quantityunit"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorderline",
            name="unit_code",
            field=models.CharField(blank=True, editable=False, max_length=10, verbose_name="数量単位コード"),
        ),
        # 戻すときは何もしない (コードの列は AddField を戻すと消える。明細の字は変えていない)
        migrations.RunPython(fill_line_codes, migrations.RunPython.noop),
    ]
