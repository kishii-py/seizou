"""ガイドツアーの段の並び (固定の字)。画面の URL の名前 → 段 (selector・title・text) の並び。上の帯の seizou_tour の tag が今の画面の段を JSON で出し、static/js/seizou-tour.js が順に示す。selector は今ある id か data-tour="<名前>" の属性か、今ある aria-labelledby の節。要素の無い段は JS が飛ばす。文を画面から直す口は無い。

段の字は、部品の無い会社のダッシュボードにも JSON で入る。ダッシュボードの試験が「無いこと」を見る字を書かない。
ダッシュボードの 2 段目 (数字のカードの次の「今日やること」の枠) の題だけは、枠の題と同じ字を core.dashboard の TODAY_TITLE から読む (題を変えるときは 1 か所)。
"""

from __future__ import annotations

from seizou.core.dashboard import TODAY_TITLE

#: 上の帯の「案内」のボタンの段 (受注の一覧と注文残の一覧で同じ字)
_GUIDE_STEP = {
    "selector": '[data-tour="guide"]',
    "title": "案内",
    "text": "困ったら、ここからこの画面の役目と前後の画面を見られます。",
}

TOURS: dict[str, tuple[dict, ...]] = {
    "core:home": (
        {
            "selector": '[data-tour="stats"]',
            "title": "今日の数字",
            "text": "部品・取引先・今月の受注などの数です。カードを押すと、その一覧が開きます。",
        },
        {
            "selector": '[data-tour="flow-guide"]',
            "title": TODAY_TITLE,
            "text": "手を動かす物のある段が、数と移る先の釦で並びます。釦を押すと、その物に絞った一覧が開きます。",
        },
        {
            "selector": 'section[aria-labelledby="shipping-title"]',
            "title": "出荷と生産",
            "text": "月ごとの出荷と生産の図です。表に月ごとの件数と数量が並びます。",
        },
        {
            "selector": 'section[aria-labelledby="due-alerts-title"]',
            "title": "納期の遅れ",
            "text": "納期を過ぎた明細と、近く納期が来る明細です。行を押すと受注の詳細が開きます。",
        },
        {
            "selector": '[data-tour="guide"]',
            "title": "案内",
            "text": "どの画面でも、ここを押すとその画面でできることと前後の画面が開きます。",
        },
    ),
    "orders:order_list": (
        {
            "selector": "#list-q",
            "title": "探す",
            "text": "受注番号や得意先の字を打つと、表が絞り込まれます。",
        },
        {
            "selector": "#rows",
            "title": "受注の表",
            "text": "行を押すと受注の詳細が開きます。既定は出荷の残っている受注だけです。",
        },
        {
            "selector": '[data-tour="orderline-link"]',
            "title": "生産管理ボード",
            "text": "明細ごとの残と、指示書や出荷の判断が並ぶ画面へ移ります。",
        },
        _GUIDE_STEP,
    ),
    "orders:orderline_list": (
        {
            "selector": "#list-q",
            "title": "絞り込む",
            "text": "キーワードの下の欄で、出荷の残りがある物だけ・納期の期間・納期が過ぎた物だけに絞り込めます。",
        },
        {
            "selector": '[data-tour="board"]',
            "title": "生産管理ボードの表",
            "text": "明細ごとの残と判断の印が並びます。行の「⋮」から指示書を出したり出荷したりできます。",
        },
        {
            "selector": '[data-tour="order-link"]',
            "title": "受注の一覧",
            "text": "受注ごとの画面に戻ります。",
        },
        _GUIDE_STEP,
    ),
}


def tour_for(url_name: str) -> list[dict] | None:
    """url_name の画面のツアーの段 (写しの並び)。ツアーの無い画面は None。"""
    steps = TOURS.get(url_name or "")
    if steps is None:
        return None
    return [dict(step) for step in steps]
