"""人ごとの表示の設定の鍵と値の表 (決め 11-05 21:2x。開発の記録 docs/nights/20261105/N167.md)。

表示の設定は core の表 UserPreference に「人・鍵・値」の 1 行で持つ (書くのは core.services.set_user_preference だけ)。
今の鍵は上の帯 (ヘッダー) の色 (HEADER_COLOR) の 1 つだけ。ほかの表示の設定を足すときも、表を変えずに同じ表へ鍵を足す。
色の値 (CSS) は scripts/css/input.css の固定の決まり ([data-seizou-header="<名前>"] .seizou-header) にあり、ここは名前と見出しだけ。
"""

#: 上の帯の色の鍵 (UserPreference.key)
HEADER_COLOR = "header_color"

#: 標準の名前 (今の白い帯。保存すると行を消す = 既定は行なし)
HEADER_COLOR_DEFAULT = "white"

#: 上の帯の色の名前 → 見出し (表示の設定の小窓の見本の並びの順)
HEADER_COLORS = {
    "white": "白 (標準)",
    "blue": "青",
    "green": "緑",
    "orange": "橙",
    "violet": "紫",
    "black": "黒",
    "sunset": "桃から橙",
    "sea": "緑から青",
}
