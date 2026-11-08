"""2 社の作り物のマニュアルの種 (本体が core/demo.py から呼ぶ。開発の記録 docs/nights/20261103/N35.md は demo.py を触らない)。

load(seeds_dir, user) が、種のフォルダの名前 (a_machining か b_plating) で会社を選んで入れる。
ほかの名前は何もしない。書くのは services の関数だけ (表に直接書かない)。標準出力に書かない。
2 回入れても重ならない: フォルダはルートの直下の同じ名前を使い回し、マニュアルは同じフォルダに同じ題があれば飛ばす。
種の中身はこの module の中の字 (全部作り物。会社名・人名・品番・取引先の名前を入れない)。画像は置かない。
"""

from __future__ import annotations

from pathlib import Path

from django.db import transaction

from . import services
from .models import Manual, ManualFolder

SETUP_V1 = """\
次の品物に切り替えるときの手順です。前の品物の最後の 1 個を測ってから始めます。

## 前の品物を片付ける

- 最後の 1 個の寸法を測り、記録に書く
- 切りくずを払い、チャックの周りを拭く

:::注意
主軸が止まってから扉を開けます。回っている間は手を入れません。
:::

## 刃物と爪を替える

- 次の品物の段取り表を見て、刃物の番号を確かめる
- 爪を替えたら、締め付けの力を段取り表の値に合わせる

:::危険
チャックの鍵を差したまま主軸を回しません。鍵は抜いて、決まった置き場に戻します。
:::

## プログラムを呼び出す

- 段取り表のプログラムの番号を呼び出す
- 工具の補正の値を段取り表と見比べる

## 1 個目を削って測る

| 項目 | 状態 |
|---|---|
| 外径 | {記録} |
| 長さ | {記録} |
| 面の粗さ | {要確認} |

:::こうする
1 個目は送りを落として削り、測ってから決まった送りに戻します。
:::

:::してはいけない
測る前に 2 個目を削り始めません。
:::

## 量産に入る

- [ ] 1 個目の測った値を記録に書いた
- [ ] 班長に 1 個目を見せた

---

経緯

- 2026-09-01 [変更] 締め付けの力 — 薄い品物で爪の跡が付いたので、段取り表の値を下げた。
- 2026-10-15 [トラブル] 補正の入れ違い — 刃物の番号を取り違えた。呼び出しの後に見比べる手順を足した。
"""

SETUP_V2 = SETUP_V1.replace(
    "- 工具の補正の値を段取り表と見比べる\n",
    "- 工具の補正の値を段取り表と見比べる\n- [ ] プログラムの番号を声に出して読んだ\n- [ ] 刃物の番号と補正の番号が合っている\n",
)

PACKING = """\
出荷の前に、品物を箱に詰めて送り状を貼るまでの手順です。

## 数と品物を確かめる

- 出荷の指示の数と、手元の数を数えて合わせる
- [ ] 数が指示と合っている
- [ ] 品物の向きと形が見本と同じ

## 箱に詰める

- 品物どうしが当たらないように、仕切りを入れる
- 隙間には緩衝材を詰める

:::ヒント
重い品物は箱の下に、軽い品物は上に置くと、運ぶ途中で潰れません。
:::

## 外の見た目を確かめる

| 項目 | 状態 |
|---|---|
| 箱の潰れ | {OK} |
| 濡れ | {OK} |
| 封のテープ | {要確認} |

## 送り状を貼る

- 送り状の宛先と数を、出荷の指示と見比べる
- [ ] 送り状を箱の上の面に貼った
"""

QUALITY = """\
めっきの仕上がりを判定するときの基準をまとめた文書です。

## 目的

めっきの仕上がりを、誰が見ても同じ判定にするための基準を決めます。

## 範囲

社内で行うめっきの全部の品物に当てます。外に頼んだ品物は、受け入れの検査で同じ基準を使います。

## 判定の基準

| 項目 | 見方 | 判定 |
|---|---|---|
| 膜の厚さ | 決まった 3 点を測る | {記録} |
| 色むら | 明るい所で 30 cm 離して見る | {OK} |
| ふくれ・はがれ | 曲げた所と角を見る | {NG} |
| 小さな点 | 拡大鏡で見る | {要確認} |

## 不具合のときの手順

:::危険
不具合の品物を良い品物の箱に戻しません。赤い札を付けて、決まった置き場に分けます。
:::

1. 不具合の品物に赤い札を付けて分ける
2. 同じ槽で処理した品物を全部止める
3. 班長に知らせ、記録に書く

---

経緯

- 2026-08-20 [変更] 膜の厚さの測る点 — 2 点から 3 点に増やした。
- 2026-10-02 [メモ] 拡大鏡 — 倍率を揃えるため、置き場の拡大鏡を 1 種類にした。
"""

#: 会社 (種のフォルダの名前) → フォルダ (名前・色) とマニュアルの並び
SEEDS = {
    "a_machining": (
        {
            "name": "段取り",
            "color": "blue",
            "manuals": (
                {
                    "title": "段取り替えの手順 (NC 旋盤)",
                    "format": "steps",
                    "body": SETUP_V1,
                    "next_body": SETUP_V2,
                    "note": "手順 3 に確かめる項目を足した",
                },
            ),
        },
        {
            "name": "出荷",
            "color": "green",
            "manuals": ({"title": "出荷前の梱包の手順", "format": "steps", "body": PACKING},),
        },
    ),
    "b_plating": (
        {
            "name": "品質",
            "color": "red",
            "manuals": ({"title": "品質の基準書 (めっき)", "format": "page", "body": QUALITY},),
        },
    ),
}


def root_folder(name: str, color: str, user) -> ManualFolder:
    """ルートの直下の同じ名前のフォルダ (無ければ services で作る)。"""
    found = ManualFolder.objects.filter(parent__isnull=True, name=name).order_by("pk").first()
    return found if found is not None else services.create_folder(None, name, color, user)


def load(seeds_dir, user) -> dict:
    """種のフォルダの名前の会社のマニュアルを入れる。{"入れた": n, "飛ばした": m} (マニュアルの本数)。"""
    folders = SEEDS.get(Path(seeds_dir).name)
    made = skipped = 0
    if folders is None:
        return {"入れた": made, "飛ばした": skipped}
    with transaction.atomic():
        for folder_seed in folders:
            folder = root_folder(folder_seed["name"], folder_seed["color"], user)
            for seed in folder_seed["manuals"]:
                if Manual.objects.filter(folder=folder, title=seed["title"]).exists():
                    skipped += 1
                    continue
                manual = services.create_manual(folder, seed["title"], seed["format"], user)
                manual = services.update_manual(manual, {"body": seed["body"]}, user)
                if "next_body" in seed:
                    services.save_as_version(manual, {"body": seed["next_body"]}, seed["note"], user)
                made += 1
    return {"入れた": made, "飛ばした": skipped}
