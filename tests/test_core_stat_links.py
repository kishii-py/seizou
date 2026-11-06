"""ダッシュボードの数字のカードの行き先の link と (i) の小窓と phone の並びと、「出荷の残りがある明細」に添える受注の件数
(初見ユーザーテスト R5 の V38・V181・r5-a-03・a-flagged-25・V78。候補 C30 の 1・2・3・5)。

見る所 (1 = 行き先): url のある 7 枚は外側が div で、onclick がそのカードの url へ移し (中の link とボタンの押下は除く)、
点線の下に「<画面の名前>を見る」の link がちょうど 1 つある・8 枚目と url の無いカードには link も onclick も無い・
link の中にボタンが入れ子にならず、link の字に help の 1 文が入らない・画面の名前が引けない url は「一覧を見る」。
見る所 (2 = (i) の小窓): 格子に吹き出し (tooltip・data-tip) が無い・(i) のボタンの名前は「<題>の数え方」で、help の 1 文はどの名前にも入らない・
頁に小窓の中身の template が 1 つあり、script が template を写して字を textContent で入れる (class を触らず HTML を差し込まない)。
見る所 (5 = phone の並び): 格子が phone から 2 列 (xl の枝は今のまま)・カードの余白が p-4 (sm から p-6)・大きい数が text-2xl (sm から text-3xl)。
見る所 (3 = 受注の件数): 数の下の 1 行「受注 n 件」の n が、受注の一覧の既定 (タブ「出荷の残りがある」と同じ条件) と、今日やることの
出荷の行の件数と同じ・大きい数 (明細の行) は今のまま生産管理ボードの件数と同じ・受注の件数を数えられないときは
1 行ごと出さずカードは出す・今日やることの出荷の段が同じ関数で数える。Django の test client で HTML を見る。名前は作り物だけ。
"""

import re
from decimal import Decimal
from html import escape, unescape

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from seizou.core import dashboard, tours, views
from seizou.masters import services as masters_services
from seizou.orders import services
from seizou.shipping import services as shipping_services

LINES_LABEL = "出荷の残りがある明細"
TITLE = re.compile(r'<p class="text-xs font-medium text-base-content/70">([^<]*)</p>')
DETAIL = '<p class="mt-1 text-sm text-base-content/70">'
# カードの外側の div の開き (url のあるカードは cursor-pointer が付く)
CARD_OPEN = re.compile(r'<div class="flex (?:cursor-pointer )?flex-col rounded-xl[^"]*"')
LINK = re.compile(r'<a [^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.S)
# 7 枚の行き先 (URL の名前) と link の字 (候補 C30 の 1 の表)
LINKS = {
    "部品": ("masters:item_list", "部品マスターを見る"),
    "取引先": ("masters:partner_list", "取引先を見る"),
    "今月の受注": ("orders:order_list", "受注を見る"),
    "出荷の残りがある明細": ("orders:orderline_list", "生産管理ボードを見る"),
    "受信した注文": ("connect:inbox_list", "受信した注文を見る"),
    "今月の支払の見込み": ("purchasing:receipt_list", "受入を見る"),
    "今月の入金": ("billing:invoice_list", "請求書を見る"),
}
HANDS_FREE_LABEL = "手入力せずに取り込んだ明細"
TEMPLATE_OPEN = '<template id="stat-help-template">'


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def make_order(user, customer, item, qtys):
    """数量の並び qtys の明細を持つ、今日の受注を作る (tests/test_core_dashboard_cards.py の make_order と同じ作り)。"""
    return services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100"), "due_date": None} for qty in qtys],
        user,
        order_date=timezone.localdate(),
    )


def lines_stat():
    return next(stat for stat in views.dashboard_stats() if stat["label"] == LINES_LABEL)


def card_html(client, label):
    """ダッシュボードの数字のカードの枠 (data-tour="stats") の中で、題が label のカードの HTML (題の <p> から次のカードの題の手前まで)。"""
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    part = grid_part(response.content.decode())
    found = list(TITLE.finditer(part))
    for index, match in enumerate(found):
        if unescape(match.group(1)) == label:
            return part[match.start() : found[index + 1].start() if index + 1 < len(found) else len(part)]
    raise AssertionError(f"カードが無い: {label}")


@pytest.mark.django_db
def test_lines_card_shows_open_order_count(logged_in, user, customer, shaft):
    today = timezone.localdate()
    make_order(user, customer, shaft, ["1", "2"])  # 未出荷 (明細 2 行)
    partial = make_order(user, customer, shaft, ["3", "4"])
    shipping_services.ship(partial.lines.get(line_no=1), Decimal("3"), today, user)  # 一部出荷 (残りは 2 行目だけ)
    shipped = make_order(user, customer, shaft, ["5"])
    shipping_services.ship(shipped.lines.get(), Decimal("5"), today, user)  # 全部を出荷
    cancelled = make_order(user, customer, shaft, ["6"])
    services.cancel_order(cancelled, user)
    partial.refresh_from_db()
    shipped.refresh_from_db()
    assert (partial.status, shipped.status) == (partial.STATUS_PARTIAL, shipped.STATUS_SHIPPED)

    stat = lines_stat()

    # 大きい数は明細の行 (未出荷の 2 行と一部出荷の 2 行目) で、押した先の生産管理ボードの件数と同じ。添える件数は受注 2 件
    assert stat["value"] == 3
    assert stat["unit"] == "行"
    assert stat["detail"] == "受注 2 件"
    assert logged_in.get(stat["url"]).context["paginator"].count == stat["value"]
    # 受注の一覧の既定 (タブ「出荷の残りがある」と同じ条件) と、今日やることの出荷の行と同じ数
    assert logged_in.get(reverse("orders:order_list")).context["paginator"].count == 2
    shipment_rows = [row for row in dashboard.today_tasks()["rows"] if row["key"] == "shipment"]
    assert [row["count"] for row in shipment_rows] == [2]
    # 画面では数の下の 1 行に出る
    assert DETAIL + "受注 2 件</p>" in card_html(logged_in, LINES_LABEL)


class _UnreadableOrders:
    """count で DB の誤りを投げる作り物 (DB が読めないときの形)。"""

    def count(self):
        raise DatabaseError("作り物の読めない DB")


@pytest.mark.parametrize("orders", [None, _UnreadableOrders()], ids=["no-orders", "db-error"])
@pytest.mark.django_db
def test_lines_card_without_order_count(logged_in, user, customer, shaft, monkeypatch, orders):
    make_order(user, customer, shaft, ["1"])
    monkeypatch.setattr(dashboard, "orders_to_ship", lambda: orders)

    stat = lines_stat()

    # 受注の件数を数えられないときは 1 行ごと出さない (偽の 0 を置かない)。カードと大きい数は出る
    assert "detail" not in stat
    assert stat["value"] == 1
    card = card_html(logged_in, LINES_LABEL)
    assert DETAIL not in card
    assert "件</p>" not in card


def test_today_tasks_count_shipment_with_same_function():
    # カードの受注の件数と今日やることの出荷の行は、同じ 1 つの関数で数える
    assert dashboard._FLOW_COUNTS["shipment"] is dashboard.orders_to_ship


# --- 行き先の link (候補 C30 の 1。R5 の V38・V181) ---


def grid_part(body):
    """数字のカードの格子 (data-tour="stats") から、格子の後ろの (i) の小窓の中身 (template) の手前までの HTML。

    template が無ければ今日やることの枠の手前まで。最後のカード (8 枚目) の切れ端に template と script を入れないため。
    """
    start = body.index('data-tour="stats"')
    end = body.find(TEMPLATE_OPEN, start)
    if end == -1:
        end = body.find('data-tour="flow-guide"', start)
    return body[start : end if end != -1 else len(body)]


def outer_cards(body):
    """カードごとの HTML (外側の div の開きから次のカードの外側の手前まで)。題の字 → HTML。"""
    part = grid_part(body)
    found = list(CARD_OPEN.finditer(part))
    result = {}
    for index, match in enumerate(found):
        card = part[match.start() : found[index + 1].start() if index + 1 < len(found) else len(part)]
        result[unescape(TITLE.search(card).group(1))] = card
    return result


def link_text(html):
    return re.sub(r"<[^>]+>", "", html).strip()


@pytest.mark.django_db
def test_cards_with_url_have_visible_destination_link(logged_in, settings):
    settings.SEIZOU = {}
    urls = {stat["label"]: stat["url"] for stat in views.dashboard_stats()}

    body = logged_in.get(reverse("core:home")).content.decode()
    by_label = outer_cards(body)

    # 試験の settings は全部の部品が入っているので 8 枚。url のあるのは 8 枚目の外の 7 枚
    assert list(by_label) == [*LINKS, HANDS_FREE_LABEL]
    for label, (url_name, words) in LINKS.items():
        card, url = by_label[label], urls[label]
        assert url.split("?", 1)[0] == reverse(url_name), label
        # 外側は div で、押すとそのカードの url へ移る (中の link と (i) のボタンの押下は除く)
        assert card.startswith("<div "), label
        opening = card[: card.index(">") + 1]
        assert "cursor-pointer" in opening and "hover:border-primary" in opening, label
        onclick = "onclick=\"if(!event.target.closest('a,button')){window.location='" + escape(url) + "'}\""
        assert onclick in opening, label
        # 行き先の link がちょうど 1 つで、字は「<画面の名前>を見る」(help の 1 文を含まない)
        ((href, inner),) = LINK.findall(card)
        assert href == escape(url), label
        assert link_text(inner) == words, label
        assert stat_help(label) not in inner, label
    # 「今月の受注」の url は絞りの GET つき (HTML では & が &amp;)
    assert "&amp;f_ordered_from=" in by_label["今月の受注"]
    # 8 枚目は行き先が無いので、link も onclick も無い
    hands_free = by_label[HANDS_FREE_LABEL]
    assert "<a" not in hands_free
    assert "onclick" not in hands_free


def stat_help(label):
    return next(stat["help"] for stat in views.dashboard_stats() if stat["label"] == label)


@pytest.mark.django_db
def test_no_button_inside_card_links(logged_in, settings):
    settings.SEIZOU = {}

    part = grid_part(logged_in.get(reverse("core:home")).content.decode())

    links = re.findall(r"<a\b.*?</a>", part, re.S)
    assert len(links) == len(LINKS)
    for link in links:
        assert "<button" not in link


@pytest.mark.django_db
def test_card_without_url_has_no_link_and_unknown_screen_reads_list(logged_in, monkeypatch):
    closed = {"label": "作り物の開けない数", "value": 3, "unit": "件", "note": "作り物の注", "icon": "box", "url": None}
    home_card = {**closed, "label": "作り物の行き先つきの数", "url": reverse("core:home")}
    unknown = {**closed, "label": "作り物の名前の無い行き先の数", "url": "/no-such-screen/"}
    monkeypatch.setattr(views, "dashboard_stats", lambda: [closed, home_card, unknown])

    by_label = outer_cards(logged_in.get(reverse("core:home")).content.decode())

    # 部署で開けないカード (url が None) には link も onclick も無い
    assert "<a" not in by_label["作り物の開けない数"]
    assert "onclick" not in by_label["作り物の開けない数"]
    assert "cursor-pointer" not in by_label["作り物の開けない数"]
    # 画面の名前は url から引く (ダッシュボード)。引けない url は「一覧を見る」
    ((href, inner),) = LINK.findall(by_label["作り物の行き先つきの数"])
    assert (href, link_text(inner)) == (reverse("core:home"), "ダッシュボードを見る")
    ((href, inner),) = LINK.findall(by_label["作り物の名前の無い行き先の数"])
    assert (href, link_text(inner)) == ("/no-such-screen/", "一覧を見る")


# --- (i) の小窓 (候補 C30 の 2 = C30-1 の a。R5 の r5-a-03・V181) ---


@pytest.mark.django_db
def test_info_button_opens_help_in_modal(logged_in, settings):
    settings.SEIZOU = {}
    helps = {stat["label"]: stat["help"] for stat in views.dashboard_stats()}

    body = logged_in.get(reverse("core:home")).content.decode()
    part = grid_part(body)
    by_label = outer_cards(body)

    # 吹き出しは無い (窓の端と左のメニューの下で切れ、閉じていても頁を横に広げたため)
    assert "tooltip" not in part and "data-tip" not in part
    assert list(by_label) == list(helps)
    names = [unescape(value) for value in re.findall(r'aria-label="([^"]*)"', part)]
    links = [link_text(inner) for _, inner in LINK.findall(part)]
    for label, help_text in helps.items():
        # 題の <p> のすぐ後ろに (i) のボタンが 1 つ。名前は「<題>の数え方」、小窓に出す字は help の 1 文。onclick は無い
        title = f'<p class="text-xs font-medium text-base-content/70">{escape(label)}</p>'
        (button,) = re.findall(re.escape(title) + r'\s*(<button type="button"[^>]*>)', by_label[label])
        assert unescape(re.search(r'aria-label="([^"]*)"', button).group(1)) == f"{label}の数え方", label
        assert unescape(re.search(r'data-stat-help="([^"]*)"', button).group(1)) == help_text, label
        assert "onclick" not in button, label
        # 読み上げの名前 (aria-label) と link の字に、help の 1 文が入らない (説明が 2 回聞こえない)
        assert not [name for name in names if help_text in name], label
        assert not [words for words in links if help_text in words], label


@pytest.mark.django_db
def test_page_has_one_help_template_and_safe_script(logged_in, settings):
    settings.SEIZOU = {}

    body = logged_in.get(reverse("core:home")).content.decode()

    assert body.count(TEMPLATE_OPEN) == 1
    start = body.index(TEMPLATE_OPEN)
    template = body[start : body.index("</template>", start)]
    # 中身は core/_rule_help.html と同じ作り (題と右上の閉じるボタン・本文・下の閉じるの行)。題は切らずに折り返す
    assert '<h3 class="min-w-0 text-lg font-bold" data-stat-help-title></h3>' in template
    assert "<p data-stat-help-text></p>" in template
    assert 'aria-label="閉じる" autofocus' in template
    assert '<div class="modal-action mt-6">' in template
    # template の後ろの script が写して字を入れる。class を触らず、HTML を差し込まない (tests/test_core_form_modal.py と同じ字)
    after = body.index("</template>", start)
    script = body[body.index("<script>", after) : body.index("</script>", after)]
    for word in ("[data-stat-help]", "stat-help-template", "seizou-modal-box", "cloneNode", "textContent", "replaceChildren", "showModal"):
        assert word in script, word
    for word in (".innerHTML", "insertAdjacentHTML", "outerHTML", ".classList", ".className"):
        assert word not in script, word


# --- phone の並び (候補 C30 の 5 = C30-3 の a。R5 の a-flagged-25) ---


@pytest.mark.parametrize("money", ["off", "on"], ids=["money-shown", "money-hidden"])
@pytest.mark.django_db
def test_cards_are_two_columns_on_phone(logged_in, settings, money):
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": money}}

    body = logged_in.get(reverse("core:home")).content.decode()

    # phone (640px 未満) から 2 列。xl 以上の枝は今のまま (8 枚で 4 列)
    grid = re.search(r'<div data-tour="stats" class="([^"]*)"', body).group(1).split()
    assert "grid-cols-2" in grid and "sm:grid-cols-2" not in grid
    assert "xl:grid-cols-4" in grid
    by_label = outer_cards(body)
    assert len(by_label) == 8
    for label, card in by_label.items():
        # カードの中の余白は phone で p-4、sm から p-6
        outer = re.match(r'<div class="([^"]*)"', card).group(1).split()
        assert "p-4" in outer and "sm:p-6" in outer and "p-6" not in outer, label
        # 大きい数 (数・金額・金額を見られない人の「—」のどの枝も) は phone で text-2xl、sm から text-3xl
        number = re.search(r'<p class="mt-2"><span class="([^"]*)"', card).group(1).split()
        assert "text-2xl" in number and "sm:text-3xl" in number and "text-3xl" not in number, label
    if money == "on":
        assert "—" in by_label["今月の支払の見込み"] and "—" in by_label["今月の入金"]


def test_tour_step_still_points_to_stats():
    # ガイドツアーの「今日の数字」の段は今までどおり数字のカードの格子を指し、字も変わらない
    step = tours.TOURS["core:home"][0]
    assert step["selector"] == '[data-tour="stats"]'
    assert step["text"] == "部品・取引先・今月の受注などの数です。カードを押すと、その一覧が開きます。"
