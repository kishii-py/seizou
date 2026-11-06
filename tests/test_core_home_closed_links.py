"""ダッシュボードから部署で開けない画面へ行く link を出さない (決め A-03。初見テスト R5 の U159。候補 C12 の 3)。

3 = 数字のカードの行き先と、「生産管理ボードを見る」「受注の一覧を見る」を、部署で開けない人には出さない (カードは消さず、数は出す)。
部署と編集の判定を on にした会社で、現場の人の形 (部署が製造) とフルコントロールの人を比べる。判定が log の会社は今のまま。
カードが押せるかは、カードの外側の tag でなく、カードの部分 ([data-tour="stats"]) に行き先の URL の href があるかで見る
(C30 がカードの外側を url の有無によらず div にし、中に行き先の link を置いたため。N254 の要望 2)。
Django の test client で HTML を見る (本物のブラウザは使わない)。名前は作り物だけ。
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from seizou.accounts import services as accounts_services
from seizou.core import views
from seizou.core.templatetags.seizou_tags import comma
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.test_core_access import account
from tests.test_core_dashboard_views import section_of

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
        number = f"{stat['value']:,.0f}" if stat.get("money") else str(comma(stat["value"]))
        assert number in card, label
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
