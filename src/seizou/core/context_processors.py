"""テンプレートに会社の設定と左のメニューを渡す。

メニューは、存在する部品 (seizou.parts.PARTS) の urls.py にある MENU = [("受注", "orders:list"), ...] を順に並べた物。
左のメニューでは、入口を部品の app ごとに大項目 (SECTIONS) に分け、大項目ごとに開け閉めする (今の画面の大項目だけ開く)。
大項目の中に中項目の見出し (受注管理・協力企業管理) を置ける。入口が 1 つも無い大項目は出さない。
今開いている画面の入口には active の印を付ける。
"""

from __future__ import annotations

from importlib import import_module, util

from django.db import DatabaseError
from django.urls import NoReverseMatch, reverse
from django.utils.functional import SimpleLazyObject
from django.utils.http import urlencode

from seizou.core.templatetags.seizou_icons import menu_icon_name, section_icon_name
from seizou.parts import PARTS

#: 左のメニューの大項目 (見出し, 中項目の並び)。中項目は (見出し, その中項目に入る部品の app の名前)。見出しが空の中項目は、
#: 見出し無しで入口を並べる。並びはこの順。
#: 決め 11-03 14:1x: 製造業なので製造部をダッシュボードのすぐ下に置き、受注管理と協力企業管理は生産管理部の中項目にする。
SECTIONS = (
    ("ダッシュボード", (("", ("core",)),)),
    ("製造部", (("", ("workorders", "progress", "stock", "receiving")),)),
    ("生産管理部", (("受注管理", ("orders", "intake", "connect")), ("協力企業管理", ("purchasing",)))),
    ("出荷と請求", (("", ("shipping", "billing")),)),
    ("マスター", (("", ("masters", "accounts")),)),
    ("文書", (("", ("manuals", "attachments")),)),
)

#: どの大項目にも入らない部品の入口を置く大項目の見出し
OTHER_SECTION = "その他"

#: core 自身の入口 (部品の MENU と同じ形)
CORE_MENU = (("ダッシュボード", "core:home"), ("検索", "core:search"))

def _inbox_received_params() -> dict:
    """受信した注文の数の移る先の絞り (状態が「届いた」)。状態の値は model の定数から読む (字を直書きしない)。部品が無ければ空。"""
    from seizou.core import dashboard

    inbox = dashboard._model("connect", "Inbox")
    return {"f_status": inbox.STATUS_RECEIVED} if inbox is not None else {}


#: 左のメニューの入口の横に出す待っている数 (決め 11-05)。鍵は入口の URL の名前、値は (説明, 単位, 数える物, 移る先)。
#: 数える物は core.dashboard の queryset を返す関数の名前 (部品が無ければ None) で、入口ごとに 1 回ずつ .count() する ((B) の形)。
#: 受信した注文・指示書・受入・請求書は、開発の記録 docs/nights/20261105/N145.md の today_tasks が flow_guide で数える関数そのもの (今日やることと同じ数)。
#: today_tasks を呼ぶと 14 回 (段の 11 回と、相手に届いていない物の 3 回) 数えるので呼ばない。納期が過ぎた明細は today_tasks に無いので、due_alerts の遅れと同じ条件の関数。
#: 移る先は (URL の名前, 絞りの GET の辞書か、それを返す関数) で、数の印を押したときに開く画面 (入口の字を押したときは今までどおり
#: 絞り無しの入口。数を置く入口は変えず、押した後に移る先だけを変える)。決め 11-07 20:5x の 2 (数を押すとその数で絞った一覧が開く。
#: 開発の記録 docs/nights/20261105/N146.md の「移り先は変えない」を置き換える。nav-01)。生産管理ボードの数は盤のタブ「納期が過ぎた」と同じ組 (orders の presets の
#: f_open=1&f_overdue=1。数える関数 _overdue_lines と同じ条件)。受信した注文の数は状態「届いた」の絞り (今日やることの行と同じ)。
#: 指示書の数は明細の行を数えているので、指示書の一覧でなく生産管理ボードを、数える関数と同じ絞り「指示書の無い明細だけ」
#: (f_open=1&f_no_workorder=1。dashboard.without_workorder) で開く。合う絞りのまだ無い物 (受入の残・請求書に入っていない出荷) は、
#: 入口そのもの (絞り無し) に移る。移る先が入口と違う画面のとき、その画面を部署で開けない人 (左のメニューに並ばない) には、
#: 印の link も入口そのものにする (押すと断られる入口を作らない。attach_counts)。
MENU_COUNTS = {
    "connect:inbox_list": ("未処理の受信した注文", "件", "_received_inbox", ("connect:inbox_list", _inbox_received_params)),
    "orders:orderline_list": ("納期が過ぎた明細", "行", "_overdue_lines", ("orders:orderline_list", {"f_open": "1", "f_overdue": "1"})),
    "workorders:workorder_list": (
        "指示書の無い明細",
        "行",
        "_lines_without_workorder",
        ("orders:orderline_list", {"f_open": "1", "f_no_workorder": "1"}),
    ),
    "purchasing:receipt_list": ("受入の残っている出荷の知らせの明細", "行", "_noticed_lines_to_receive", ("purchasing:receipt_list", {})),
    "billing:invoice_list": ("請求書に入っていない出荷", "件", "_shipments_to_invoice", ("billing:invoice_list", {})),
}


def count_url(url_name: str) -> str | None:
    """数の印を押したときに開く URL (MENU_COUNTS の移る先に絞りの GET を付けた物)。移る先の URL が引けなければ None。"""
    target_name, params = MENU_COUNTS[url_name][3]
    target = url_or_none(target_name)
    if target is None:
        return None
    if callable(params):
        params = params()
    return f"{target}?{urlencode(params)}" if params else target


def url_or_none(url_name: str) -> str | None:
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return None


def entry(label: str, url_name: str, url: str, app: str) -> dict:
    return {"label": label, "url_name": url_name, "url": url, "app": app, "icon": menu_icon_name(url_name, app)}


def menu() -> list[dict]:
    """左のメニュー。URL が引ける入口だけを並べる (起動後に部品が増えた時などに 500 にしない)。

    1 つの入口は {"label", "url_name", "url", "app", "icon"} の辞書。app は部品の app の名前 (例 "masters")、
    icon は線の絵の名前 (seizou_icons)。
    """
    items: list[dict] = []
    for part in PARTS:
        name = f"{part}.urls"
        if util.find_spec(name) is None:
            continue
        module = import_module(name)
        app = part.rsplit(".", 1)[-1]
        for label, url_name in getattr(module, "MENU", ()):
            url = url_or_none(url_name)
            if url is None:
                continue
            items.append(entry(label, url_name, url, app))
    return items


def core_menu() -> list[dict]:
    """core 自身の入口 (ダッシュボード・検索)。"""
    items = []
    for label, url_name in CORE_MENU:
        url = url_or_none(url_name)
        if url is not None:
            items.append(entry(label, url_name, url, "core"))
    return items


def mark_active(items: list[dict], path: str) -> None:
    """今開いている画面の入口に active の印を付ける。

    URL の先頭が合う入口のうち、いちばん長い物を 1 つだけ選ぶ (/ は同じ道のときだけ)。
    """
    best = None
    for item in items:
        url = item["url"]
        hit = path == url if url == "/" else path.startswith(url)
        if hit and (best is None or len(url) > len(best["url"])):
            best = item
    for item in items:
        item["active"] = item is best


def custom_sections() -> list[dict]:
    """会社の設定 menu (左のメニューの括り)。1 つ = {"label", "apps", "screens"}。形の違う物は捨てる。空なら []。

    決め 11-03 (一覧の画面の設計の決め): 括りは会社ごとに答えの YAML で決め、同じ画面を 2 つの括りに出せる。
    会社の括りは大項目だけで、中項目は書けない (本体の選び 11-03 14:1x)。
    """
    from seizou.core import conf

    value = conf.setting("menu")
    if not isinstance(value, list):
        return []
    result = []
    for raw in value:
        if not isinstance(raw, dict) or not isinstance(raw.get("label"), str) or not raw["label"].strip():
            continue
        apps = [a for a in (raw.get("apps") or []) if isinstance(a, str)]
        screens = [u for u in (raw.get("screens") or []) if isinstance(u, str)]
        result.append({"label": raw["label"].strip(), "apps": apps, "screens": screens})
    return result


def section(label: str, parts: list[tuple[str, list[dict]]]) -> dict:
    """1 つの大項目。parts は (中項目の見出し, 入口の並び) の並び (入口の無い中項目は捨てる)。

    返す物 = {"label", "items", "groups", "show_label", "collapsible", "active", "icon"}。
    - items は大項目の入口の全部 (中項目の順)。groups は中項目の並び (1 つ = {"label", "items"}。見出しの無い中項目は label が空)。
    - show_label: 入口が 1 つで見出しと同じ字なら偽 (見出しを出さない)。
    - collapsible: 開け閉めする大項目。ダッシュボードの括りと show_label が偽の物は、開け閉め無しで入口を直に出す。
    - active: 今の画面の入口を持つ (左のメニューではその大項目だけ開いて出す)。
    - icon: 大項目の絵の名前 (seizou_icons)。
    """
    groups = [{"label": sublabel, "items": chosen} for sublabel, chosen in parts if chosen]
    items = [item for group in groups for item in group["items"]]
    only = items[0] if len(items) == 1 else None
    show_label = not (only is not None and only["label"] == label)
    return {
        "label": label,
        "items": items,
        "groups": groups,
        "show_label": show_label,
        "collapsible": show_label and label != SECTIONS[0][0],
        "active": any(item.get("active") for item in items),
        "icon": section_icon_name(label, items),
    }


def sections(items: list[dict]) -> list[dict]:
    """入口を大項目に分ける (1 つの形は section())。入口が無い大項目は出さない。大項目に入らない app は「その他」に置く。

    会社の設定 menu があれば、その括り (app か画面の URL の名前で選ぶ。同じ入口を 2 つの括りに出せる。中項目は無し) で分け、
    ダッシュボードの括り (core) はいつも先頭に置く。
    """
    custom = custom_sections()
    placed: set[int] = set()
    groups: list[dict] = []
    if custom:
        core_items = [item for item in items if item["app"] == "core"]
        placed.update(id(item) for item in core_items)
        groups.append(section(SECTIONS[0][0], [("", core_items)]))
        for raw in custom:
            chosen = [item for item in items if item["app"] in raw["apps"] or item["url_name"] in raw["screens"]]
            placed.update(id(item) for item in chosen)
            groups.append(section(raw["label"], [("", chosen)]))
    else:
        for label, subsections in SECTIONS:
            parts = []
            for sublabel, apps in subsections:
                chosen = [item for item in items if item["app"] in apps]
                placed.update(id(item) for item in chosen)
                parts.append((sublabel, chosen))
            groups.append(section(label, parts))
    groups.append(section(OTHER_SECTION, [("", [item for item in items if id(item) not in placed])]))
    return [group for group in groups if group["items"]]


def _hides_money(user) -> bool:
    """テンプレートが金額の枡を隠す印 (seizou.hide_money) の中身。テンプレートが読んだ時に 1 回だけ判定する。

    金額の閲覧の無い人は、on で真 (隠す)、log で偽 (見せて記録に 1 行)、off で偽。未ログインは偽。
    """
    from seizou.core import access

    return bool(getattr(user, "is_authenticated", False)) and not access.can_see_money(user)


def _menu_count(name: str) -> int | None:
    """core.dashboard の関数 name の queryset の件数 (1 回の問い合わせ)。部品が無い・DB が読めなければ None (偽の 0 を置かない)。"""
    from seizou.core import dashboard

    queryset = getattr(dashboard, name)()
    if queryset is None:
        return None
    try:
        return queryset.count()
    except DatabaseError:
        return None


def _header_color(user) -> str:
    """上の帯の色の名前 (人ごとの表示の設定。決め 11-05 21:2x。開発の記録 docs/nights/20261105/N167.md)。テンプレートが読んだ時に 1 回だけ読む。

    標準 (白)・未ログイン・表を読めないときは空の字 (土台の base.html は data-seizou-header を出さない)。名前は core.display.HEADER_COLORS の物だけ。
    """
    from seizou.core import display
    from seizou.core import services

    try:
        name = services.user_preference(user, display.HEADER_COLOR)
    except DatabaseError:
        return ""
    return name if name in display.HEADER_COLORS and name != display.HEADER_COLOR_DEFAULT else ""


def _bell_count(user) -> int:
    """上の帯の鈴の赤い数 (core.bell.unread_count)。テンプレートが読んだ時に 1 回だけ数える。未ログインは 0。"""
    from seizou.core import bell

    if not getattr(user, "is_authenticated", False):
        return 0
    return bell.unread_count(user)


def attach_counts(items: list[dict], user) -> None:
    """左のメニューの入口 (MENU_COUNTS にある物) に、待っている数 count と説明 count_label と単位 count_unit と、数を押したときに開く URL count_url を付ける。

    使う人: 現場と事務の全員 / いつ: 朝や手の空いた時に左のメニューを見る / 何のために: 待っている仕事の数から仕事へ入る。
    数は開発の記録 docs/nights/20261105/N145.md の「今日やること」と同じ元 (core.dashboard の数える関数)。count は SimpleLazyObject で、テンプレートが読んだ時
    (左のメニューを描くとき) だけ入口ごとに 1 回数える (htmx の行の取り直しでは数えない)。未ログインには付けない。
    数えられない時は None、0 は 0 のままで、どちらもテンプレートは印を出さない。items は visible_menu の後の入口 (開ける物だけ)。
    count_url は、移る先が入口と違う画面でその画面が items に無い (部署で開けない) ときは入口そのもの (問い合わせを足さずに見る)。
    """
    if not getattr(user, "is_authenticated", False):
        return
    counts: dict[str, int | None] = {}
    visible = {item["url_name"] for item in items}

    def count_of(url_name: str) -> int | None:
        if url_name not in counts:
            counts[url_name] = _menu_count(MENU_COUNTS[url_name][2])
        return counts[url_name]

    for item in items:
        url_name = item["url_name"]
        if url_name not in MENU_COUNTS:
            continue
        label, unit, _name, _target = MENU_COUNTS[url_name]
        item["count"] = SimpleLazyObject(lambda url_name=url_name: count_of(url_name))
        item["count_label"] = label
        item["count_unit"] = unit
        target_name = _target[0]
        reachable = target_name == url_name or target_name in visible
        item["count_url"] = (count_url(url_name) if reachable else None) or item["url"]


def seizou(request) -> dict:
    from seizou.core import access
    from seizou.core import conf
    from seizou.core import services
    from seizou.core import practice

    user = getattr(request, "user", None)

    try:
        company = services.company()
        company_name = company.name
        theme = company.theme or "light"
        brand_color = conf.brand_color()
        industry = conf.setting("industry") or ""
    except DatabaseError:
        company = None
        company_name = "seizou"
        theme = "light"
        brand_color = ""
        industry = ""
    part_items = menu()
    part_items = access.visible_menu(user, part_items)
    attach_counts(part_items, user)
    items = [*core_menu(), *part_items]
    mark_active(items, getattr(request, "path", "") or "")
    return {
        "seizou": {
            "company": company,
            "company_name": company_name,
            "theme": theme,
            "brand_color": brand_color,
            "industry": industry,
            "menu": part_items,
            "sections": sections(items),
            "search_url": url_or_none("core:search"),
            "manual_url": url_or_none("core:manual"),
            # 存在する部品の短い名前 (例 "masters")。テンプレートが {% if "attachments" in seizou.parts %} で、
            # 入っている部品の枠だけを include するために使う。
            "parts": part_names(),
            # 金額の閲覧が無い人 (on) に金額の枡を隠す印。テンプレートが読んだ時に判定し、log では記録に 1 行 (作者の決め)
            "hide_money": SimpleLazyObject(lambda: _hides_money(user)),
            # 上の帯の色 (人ごとの表示の設定。開発の記録 docs/nights/20261105/N167.md)。base.html の <html> の data-seizou-header。テンプレートが読んだ時に読む
            "header_color": SimpleLazyObject(lambda: _header_color(user)),
            # 上の帯の知らせの鈴の赤い数 (まだ見ていない知らせの数。core.bell)。core/_bell.html が読んだ時に数える (受け渡しの seizou-部品の6案-20261107/指示書.md の 4)
            "bell_count": SimpleLazyObject(lambda: _bell_count(user)),
            "practice": practice.is_practice(),  # 練習モード (seizou_practice start が立てたサーバー) の印。base.html が帯と題の「練習: 」を出す
            # 知らせの帯の字 (選べる設定 site_notice。空なら出さない)。base.html が練習モードの帯の下に出す (core/_site_notice_band.html)。
            # 表の上書きは conf の控えから読み、DB が読めないときも例外を投げない (会社の行の読みの try の外に置き、DB が止まっても帯を出す)
            "site_notice": conf.site_notice(),
        }
    }


def part_names() -> list[str]:
    """存在する部品 (seizou.parts.PARTS) の app の短い名前の並び (例 ["core", "masters", ...])。"""
    return [part.rsplit(".", 1)[-1] for part in PARTS]
