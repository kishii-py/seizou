"""ダッシュボードから部署で開けない画面へ行く link を出さない (決め A-03。初見テスト R5 の U159。候補 C12 の 3〜5)。

3 = 数字のカードの行き先と、「生産管理ボードを見る」「受注の一覧を見る」を、部署で開けない人には出さない (カードは消さず、数は出す)。
4 = 「はじめての方へ」の枠から部署で開けない段を外し、段が残らなければ枠ごと出さない (決め C12-2 = a)。
5 = 現場の人の形のダッシュボードの link を全部開いて、403 が 0 個。
部署と編集の判定を on にした会社で、現場の人の形 (部署が製造) とフルコントロールの人を比べる。判定が log の会社は今のまま。
カードが押せるかは、カードの外側の tag でなく、カードの部分 ([data-tour="stats"]) に行き先の URL の href があるかで見る
(C30 がカードの外側を url の有無によらず div にし、中に行き先の link を置いたため。N254 の要望 2)。
Django の test client で HTML を見る (本物のブラウザは使わない)。名前は作り物だけ。
"""

import re
from datetime import timedelta
from decimal import Decimal
from html import unescape

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from seizou.accounts import services as accounts_services
from seizou.core import dashboard, views
from seizou.core.templatetags.seizou_tags import comma, yen
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.test_core_access import account
from tests.test_core_dashboard_views import ROW_ONE, row_of, section_of

#: 部署と編集の判定を on にした会社 (金額の判定は既定のまま)
ON = {"department": "on", "write": "on"}
LOG = {"department": "log", "write": "log"}
#: 現場の人の形では押せないカード (行き先の部品が部署の外) と、押せるカード (部品マスター)
CLOSED_CARDS = ("今月の受注", "出荷の残りがある明細", "受信した注文", "今月の支払の見込み", "今月の入金")
OPEN_CARDS = ("部品", "取引先")
LINK_WORDS = ("生産管理ボードを見る", "受注の一覧を見る")


def genba():
    """現場の人の形: 部署が製造 (指示書・実績・在庫・部品マスター)、編集の権限なし。"""
    department = accounts_services.create_department("作り物の製造の部署", ["workorders", "progress", "stock", "masters"], [], None)
    return account("seisaku", departments=[department])


def admin():
    """比べの形: フルコントロール。"""
    return account("kanrisha", full_control=True)


def make_orders(user):
    """受注と納期の行のある会社: 納期を過ぎた明細と、3 日後に納期の来る明細 (どちらも指示書と出荷が無い)。"""
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    today = timezone.localdate()
    for due in (today - timedelta(days=2), today + timedelta(days=3)):
        orders_services.create_order(
            customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": due}], user, order_date=today
        )


def home(client, person):
    client.force_login(get_user_model().objects.get(pk=person.pk))
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return response.content.decode()


def stats_part(body):
    """数字のカードの部分 (格子の開きから、数え方の小窓の template の手前まで)。"""
    start = body.index('data-tour="stats"')
    return body[start : body.index('id="stat-help-template"', start)]


def cards_by_label(part):
    """題 → カードの HTML (題の字から次のカードの題の手前まで)。"""
    marker = '<p class="text-xs font-medium text-base-content/70">'
    chunks = part.split(marker)[1:]
    return {chunk.split("</p>", 1)[0]: chunk for chunk in chunks}


def card_hrefs(stats):
    """題 → カードの行き先の href の字 (引用符まで含める。& は HTML の &amp;)。"""
    return {stat["label"]: f'href="{escape(stat["url"])}"' for stat in stats if stat.get("url")}


@pytest.mark.django_db
@pytest.mark.parametrize("with_orders", [False, True], ids=["empty", "orders"])
def test_genba_cards_and_links_have_no_closed_hrefs(client, settings, user, with_orders):
    settings.SEIZOU = {"access_control": dict(ON)}
    if with_orders:
        make_orders(user)
    stats = views.dashboard_stats()
    hrefs = card_hrefs(stats)
    body = home(client, genba())
    part = stats_part(body)
    shown = cards_by_label(part)

    for label in CLOSED_CARDS:
        assert hrefs[label] not in part, label
    for label in OPEN_CARDS:
        assert hrefs[label] in part, label
    # 出ているカードには数が出ている (link だけを外し、カードは消さない)
    values = {stat["label"]: stat for stat in stats}
    assert set(shown) >= {"部品", "取引先", "今月の受注", "出荷の残りがある明細", "受信した注文"}
    for label, card in shown.items():
        stat = values[label]
        number = yen(stat["value"], 0) if stat.get("money") else comma(stat["value"])
        assert f'<span class="text-2xl font-bold tabular-nums sm:text-3xl">{escape(number)}</span>' in card, label
    for words in LINK_WORDS:
        assert words not in body, words
    # 納期アラートと最近の受注の節に、一覧の行き先の href が無い (行の受注番号の link は受注の詳細へ移るので残る)
    alerts, recent = section_of(body, "due-alerts-title"), section_of(body, "recent-orders-title")
    for section in (alerts, recent):
        assert f'href="{reverse("orders:orderline_list")}"' not in section
        assert f'href="{reverse("orders:order_list")}"' not in section


@pytest.mark.django_db
def test_admin_keeps_card_and_section_links(client, settings, user):
    settings.SEIZOU = {"access_control": dict(ON)}
    make_orders(user)
    hrefs = card_hrefs(views.dashboard_stats())
    body = home(client, admin())
    part = stats_part(body)

    for label in (*CLOSED_CARDS, *OPEN_CARDS):
        assert hrefs[label] in part, label
    assert f'href="{reverse("orders:orderline_list")}">生産管理ボードを見る' in body
    assert f'href="{reverse("orders:order_list")}">受注の一覧を見る' in body


@pytest.mark.django_db
def test_log_company_keeps_links_for_genba(client, settings, user):
    settings.SEIZOU = {"access_control": dict(LOG)}
    make_orders(user)
    stats = views.dashboard_stats()
    hrefs = card_hrefs(stats)
    body = home(client, genba())
    part = stats_part(body)

    for label in cards_by_label(part):
        if label in hrefs:
            assert hrefs[label] in part, label
    for label in (*CLOSED_CARDS, *OPEN_CARDS):
        assert label in cards_by_label(part), label
    assert f'href="{reverse("orders:orderline_list")}">生産管理ボードを見る' in body
    assert f'href="{reverse("orders:order_list")}">受注の一覧を見る' in body


@pytest.mark.django_db
def test_dashboard_stats_names_the_card_targets():
    """行き先のあるカードには url を作った URL の名前 (url_name) が付き、行き先の無いカードには付かない。"""
    stats = views.dashboard_stats()
    by_label = {stat["label"]: stat for stat in stats}
    expected = {
        "部品": "masters:item_list",
        "取引先": "masters:partner_list",
        "今月の受注": "orders:order_list",
        "出荷の残りがある明細": "orders:orderline_list",
        "受信した注文": "connect:inbox_list",
        "今月の支払の見込み": "purchasing:receipt_list",
        "今月の入金": "billing:invoice_list",
    }
    for label, url_name in expected.items():
        assert by_label[label]["url_name"] == url_name, label
        assert by_label[label]["url"].split("?", 1)[0] == reverse(url_name), label
    assert "url_name" not in by_label["手入力せずに取り込んだ明細"]


# ---------------------------------------------------------------- 4 = 「はじめての方へ」の段 (決め C12-2 = a)

FRAME = re.compile(r'<section[^>]*aria-labelledby="flow-guide-title".*?</section>', re.S)
#: 受注の部品だけを開ける部署の人の枠に出てはいけない行き先 (部署の外の部品の画面)
OUTSIDE_ORDERS = ("/connect/", "/shipping/", "/billing/", "/purchasing/")


def clerk():
    """部署が受注だけの人 (tests/test_core_today_tasks の test_today_tasks_hides_rows_of_closed_departments と同じ作り方)。"""
    department = accounts_services.create_department("作り物の受注の部署", ["orders"], ["orders"], None)
    return account("juchu", departments=[department])


def jimu():
    """事務の人の形: 部署が受注と出荷 (請求と入金が無い)、伝票編集の権限あり (tests/test_core_department_buttons の jimu と同じ)。"""
    department = accounts_services.create_department(
        "作り物の受注と出荷の部署", ["orders", "intake", "connect", "shipping", "purchasing", "stock", "masters"], ["orders", "shipping"], None
    )
    return account("hanbai", departments=[department], permissions=["slip_edit"])


@pytest.mark.django_db
@pytest.mark.parametrize("with_orders", [False, True], ids=["empty", "orders"])
def test_genba_has_no_frame_and_alerts_take_the_row(client, settings, user, with_orders):
    settings.SEIZOU = {"access_control": dict(ON)}
    if with_orders:
        make_orders(user)
    person = genba()
    body = home(client, person)

    assert 'data-tour="flow-guide"' not in body
    assert 'id="flow-guide-title"' not in body
    assert row_of(body, "due-alerts-title").startswith(ROW_ONE)
    assert dashboard.today_tasks(get_user_model().objects.get(pk=person.pk)) is None


@pytest.mark.django_db
def test_orders_only_clerk_sees_only_the_orders_steps(client, settings):
    settings.SEIZOU = {"access_control": dict(ON)}
    person = clerk()
    frame = FRAME.search(home(client, person)).group(0)

    assert frame.count("はじめての方へ") == 1
    assert frame.count("<ol ") == 1
    assert "<h3 class=\"font-bold\">受注から入金まで</h3>" in frame
    assert "発注から検収まで" not in frame
    steps = re.findall(r'<a class="btn btn-sm btn-outline border-base-300 whitespace-nowrap" href="([^"]*)">([^<]*)</a>', frame)
    # 受注の段は受注の一覧 (受注の部品) へ移るので、受注だけの部署の人にも残る (ONB-01)。受信した注文 (connect) の段は無い
    assert steps == [(reverse("orders:order_list"), "受注"), (reverse("orders:orderline_list"), "指示書")]
    for head in OUTSIDE_ORDERS:
        assert f'href="{head}' not in frame, head

    guide = dashboard.today_tasks(get_user_model().objects.get(pk=person.pk))["guide"]
    assert [flow["key"] for flow in guide["flows"]] == ["sales"]
    assert [step["key"] for step in guide["flows"][0]["steps"]] == ["order", "workorder"]
    # 元の流れの値は書き換えない
    assert len(dashboard.flow_guide()["flows"]) == 2


@pytest.mark.django_db
def test_admin_and_jimu_frames_stay(client, settings, user):
    settings.SEIZOU = {"access_control": dict(ON)}
    person = admin()
    frame = FRAME.search(home(client, person)).group(0)
    # フルコントロールの人の枠は今のまま (2 つの流れの段を全部出す。test_core_today_tasks の test_home_shows_flow_picture_when_nothing_to_do と同じ形)。
    # 部品も取引先も無い会社なので、その上に「最初にすること」の段 (ONB-02) も出る
    assert frame.count("はじめての方へ") == 1
    for title in ("最初にすること", "受注から入金まで", "発注から検収まで"):
        assert frame.count(f'<ol class="mt-2 flex flex-wrap items-center gap-2" aria-label="{title}の段"') == 1, title
    assert dashboard.today_tasks(get_user_model().objects.get(pk=person.pk))["guide"] == dashboard.flow_guide()
    # 利用者を渡さない値も今のまま
    assert dashboard.today_tasks()["guide"] == dashboard.flow_guide()

    make_orders(user)
    person = jimu()
    frame = FRAME.search(home(client, person)).group(0)
    rows = dashboard.today_tasks(get_user_model().objects.get(pk=person.pk))["rows"]
    # 行のある事務の人の枠は「今日やること」の行のまま (行の外し方は N145 の形のまま)
    assert rows and [row["key"] for row in rows] == [row["key"] for row in dashboard.today_tasks()["rows"] if row["url_name"].split(":")[0] != "billing"]
    assert '<h2 id="flow-guide-title" class="text-lg font-bold">今日やること</h2>' in frame
    for row in rows:
        assert f'href="{escape(row["url"])}">{row["button"]}<' in frame, row["key"]


# ---------------------------------------------------------------- 5 = 現場の人のダッシュボードの link を全部開く

HREF = re.compile(r'<a\b[^>]*\bhref="(/[^"]*)"')


@pytest.mark.django_db
@pytest.mark.parametrize("with_orders", [False, True], ids=["empty", "orders"])
def test_genba_dashboard_links_open_without_403(client, settings, user, with_orders):
    """ダッシュボードの a[href] のうち「/」で始まる物 (ログアウトを除く) を全部 GET して、部署の 403 が 0 個 (R5 の U159 の数え方)。"""
    settings.SEIZOU = {"access_control": dict(ON)}
    if with_orders:
        make_orders(user)
    person = genba()
    body = home(client, person)
    links = sorted({unescape(href) for href in HREF.findall(body) if "logout" not in href})
    assert links

    refused = [link for link in links if client.get(link).status_code == 403]

    assert refused == []
    if with_orders:
        # 行の受注番号の link (受注の詳細。詳細は部署を見ない) は残り、403 にならない
        assert any(re.fullmatch(r"/orders/\d+/", link) for link in links)
