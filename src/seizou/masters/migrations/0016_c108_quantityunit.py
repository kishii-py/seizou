# 部品の単位を中小企業共通 EDI の数量単位の一覧から選べるようにする (決め UNIT-1 = a)。
#
# 一覧の表を作って 40 行を入れ、今の部品の単位の字を一覧の名前に移す: 字を畳んで (全角と半角・カタカナとひらがな・
# 大文字と小文字を同じにする) 一覧の名前と同じなら、単位の字を一覧の名前にそろえ (例「KG」→「kg」)、数量単位コードを入れる。
# 当たらない字は字もコードも変えず、部品マスターの絞り「単位を選び直す部品だけ」と部品の詳細の黄色の 1 行で人に選んでもらう
# (勝手に個にしない)。一覧の値は、この migration を書いた時の付表 4 の表の写し。
# 値を後で変えるときは、この migration を直さず、新しい migration で表を直す。

import unicodedata

from django.db import migrations, models

#: 中小企業共通 EDI の付表 4 の「数量単位コード」のシートの (コード, 名前)。並びは表のまま。
#: 先の 15 行が国際サブセット定義表、後の 25 行が＜参考＞の表。H87 の名前は表の「個<デフォルト>」から印を外した字。
#: 括弧は表のとおり全角。² と ³ は上付きの字。
UNITS = (
    ("H87", "個"), ("SET", "セット"), ("TNE", "t"), ("KGM", "kg"), ("GRM", "g"), ("MGM", "mg"),
    ("LTR", "l"), ("MLT", "ml"), ("KTM", "km"), ("MTR", "m"), ("CMT", "cm"), ("MMT", "mm"),
    ("MTK", "m²"), ("CMK", "cm²"), ("MTQ", "m³"),
    ("T3", "千個"), ("PG", "枚（物品）"), ("XST", "枚（紙）"), ("QB", "ページ（書物）"),
    ("XBO", "本（瓶）"), ("XTU", "本（チューブ）"), ("PI", "本（パイプ）"), ("XRD", "本（棒）"),
    ("XRO", "巻（ロール）"), ("XCL", "巻（コイル）"), ("RL", "リール"), ("XCN", "缶"),
    ("HEA", "人・頭（動物）"), ("Q3", "食（食事）"), ("XCS", "ケース"), ("XCT", "カートン"),
    ("KT", "キット"), ("XBX", "箱"), ("PX", "台（パレット）"), ("XPK", "包"), ("XBG", "袋（俵）"),
    ("XBK", "籠"), ("PR", "組"), ("DZN", "ダース"), ("GRO", "グロス"),
)

# カタカナ (ァ〜ヶ) をひらがなに写す表 (masters.services と同じ。migration は今の services を import しない)。
_KATAKANA_TO_HIRAGANA = {code: code - 0x60 for code in range(0x30A1, 0x30F7)}


def _fold(text):
    """照合のために字を畳む。全角と半角、カタカナとひらがな、大文字と小文字を同じにする (masters.services._fold と同じ)。"""
    text = unicodedata.normalize("NFKC", text)
    return text.translate(_KATAKANA_TO_HIRAGANA).casefold()


def seed_units(apps, schema_editor):
    """一覧の 40 行を入れる。何度呼んでも同じ 40 行になる (行を消す試験の後に、試験の頭で呼び直せるように)。"""
    QuantityUnit = apps.get_model("masters", "QuantityUnit")
    for order, (code, name) in enumerate(UNITS, start=1):
        QuantityUnit.objects.update_or_create(code=code, defaults={"name": name, "sort_order": order})


def fill_item_codes(apps, schema_editor):
    """単位の字のある部品の字を畳み、一覧の名前のどれかと全部が同じなら、字を一覧の名前に、コードを一覧のコードにする。

    当たらない字は何もしない (字もコードも変えない。勝手に個にしない)。
    """
    Item = apps.get_model("masters", "Item")
    names = {_fold(name): (name, code) for code, name in UNITS}
    for item in Item.objects.exclude(unit=""):
        hit = names.get(_fold((item.unit or "").strip()))
        if hit is None:
            continue
        item.unit, item.unit_code = hit
        item.save(update_fields=["unit", "unit_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0015_merge_c95_c98"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuantityUnit",
            fields=[
                ("code", models.CharField(max_length=10, primary_key=True, serialize=False, verbose_name="数量単位コード")),
                ("name", models.CharField(max_length=10, unique=True, verbose_name="名前")),
                ("sort_order", models.PositiveSmallIntegerField(verbose_name="並び順")),
            ],
            options={
                "verbose_name": "数量の単位",
                "verbose_name_plural": "数量の単位",
                "ordering": ["sort_order"],
            },
        ),
        migrations.AddField(
            model_name="item",
            name="unit_code",
            field=models.CharField(blank=True, editable=False, max_length=10, verbose_name="数量単位コード"),
        ),
        # 戻すときは何もしない (表は CreateModel を戻すと消え、コードの列は AddField を戻すと消える。部品の字は一覧の名前のまま残る)
        migrations.RunPython(seed_units, migrations.RunPython.noop),
        migrations.RunPython(fill_item_codes, migrations.RunPython.noop),
    ]
