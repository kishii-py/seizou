"""ダッシュボードの「はじめての方へ」の枠の「最初にすること」(ONB-02 の会期中の小さい版。core/home.html の枝だけ)。

部品も取引先も 0 の会社にだけ、流れの段の上に「取引先を登録する → 部品を登録する → 受注を入れる」の枠だけのボタン (CSV 取り込みの link つき) を出す。
どちらかが 1 件でもあれば出さない。数が読めない (None) ときと数字のカードの並びが違うときも出さない。入口のボタンは書ける人にだけ (決め 11-04 12:5x)。
Django の test client で HTML を見る (本物のブラウザは使わない)。名前は作り物だけ。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import views
from seizou.masters import services as masters_services
from tests.test_core_access import account

FRAME = re.compile(r'<section[^>]*aria-labelledby="flow-guide-title".*?</section>', re.S)
TITLE = '<h3 id="first-steps-title" class="font-bold">最初にすること</h3>'
STEP = re.compile(r'<a class="btn btn-sm btn-outline border-base-300 whitespace-nowrap" href="([^"]*)">([^<]*)</a>')
CSV = re.compile(r'<a class="link link-primary whitespace-nowrap text-xs" href="([^"]*)">CSV 取り込み</a>')
ON = {"department": "on", "write": "on"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def frame_of(client) -> str:
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return FRAME.search(response.content.decode()).group(0)


def first_steps(frame: str) -> str:
    """「最初にすること」の部分 (題から流れの格子の手前まで)。無ければ空。"""
    if TITLE not in frame:
        return ""
    start = frame.index(TITLE)
    return frame[start : frame.index('<div class="mt-4 grid gap-6 2xl:grid-cols-2">', start)]


def make_partner(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


def make_item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.mark.django_db
def test_stats_start_with_items_then_partners():
    # 枠の条件は数字のカードの先頭の 2 枚を読む。並びが変わったらここで止まる (枠が黙って消えないため)
    stats = views.dashboard_stats()
    assert [stat["url_name"] for stat in stats[:2]] == ["masters:item_list", "masters:partner_list"]
    assert [stat["value"] for stat in stats[:2]] == [0, 0]


@pytest.mark.django_db
def test_empty_company_shows_first_steps_above_flows(logged_in):
    frame = frame_of(logged_in)
    assert frame.count("はじめての方へ") == 1
    part = first_steps(frame)
    assert part
    assert "取引先と部品を登録してから、受注を入れます。" in part
    assert STEP.findall(part) == [
        (reverse("masters:partner_create"), "取引先を登録する"),
        (reverse("masters:item_create"), "部品を登録する"),
        (reverse("orders:order_create"), "受注を入れる"),
    ]
    assert CSV.findall(part) == [reverse("masters:partner_import"), reverse("masters:item_import")]
    # 矢印は前の段の li に入る (最後の段には無い)
    assert part.count("<svg") == 2
    # 最初にすることは流れの段の上。主色のボタンと印は足さない
    assert frame.index(TITLE) < frame.index('aria-label="受注から入金までの段"')
    assert "btn-primary" not in frame
    assert "badge" not in frame
    for word in ("はじめに", "品目", "今日やること", "次にすること:"):
        assert word not in frame, word


@pytest.mark.django_db
@pytest.mark.parametrize("make", [make_partner, make_item], ids=["partner", "item"])
def test_first_steps_hidden_once_a_partner_or_item_exists(logged_in, user, make):
    make(user)
    frame = frame_of(logged_in)
    # 手を動かす物はまだ無いので「はじめての方へ」のまま。空の会社でなくなったので「最初にすること」は出ない
    assert frame.count("はじめての方へ") == 1
    assert first_steps(frame) == ""
    assert "最初にすること" not in frame
    assert "取引先を登録する" not in frame


@pytest.mark.django_db
def test_first_steps_hidden_when_counts_are_unreadable_or_reordered(logged_in, monkeypatch):
    real = views.dashboard_stats()
    unreadable = [{**stat, "value": None} if stat.get("url_name", "").startswith("masters:") else stat for stat in real]
    monkeypatch.setattr(views, "dashboard_stats", lambda: unreadable)
    assert "最初にすること" not in frame_of(logged_in)

    monkeypatch.setattr(views, "dashboard_stats", lambda: list(reversed(real)))
    assert "最初にすること" not in frame_of(logged_in)


@pytest.mark.django_db
def test_first_steps_follow_permissions_and_departments(client, settings):
    settings.SEIZOU = {"access_control": dict(ON)}

    def frame_for(person):
        client.force_login(get_user_model().objects.get(pk=person.pk))
        return frame_of(client)

    # 受注だけの部署で編集の権限の無い人: 1 つも出せないので題ごと出ない
    orders_only = accounts_services.create_department("作り物の受注の部署", ["orders"], ["orders"], None)
    assert first_steps(frame_for(account("juchu", departments=[orders_only]))) == ""

    # マスターと受注の部署で取引先だけ書ける人: 取引先の段だけ (矢印も無い)。
    # 枠は流れの段を 1 つも開けない人には出ない (core.dashboard.today_tasks が None) ので、部署に受注も入れる
    masters_and_orders = accounts_services.create_department("作り物のマスターの部署", ["masters", "orders"], [], None)
    part = first_steps(frame_for(account("torihiki", departments=[masters_and_orders], permissions=["partners"])))
    assert STEP.findall(part) == [(reverse("masters:partner_create"), "取引先を登録する")]
    assert CSV.findall(part) == [reverse("masters:partner_import")]
    assert "<svg" not in part
    assert "/orders/" not in part

    # マスターと伝票を書ける人とフルコントロールの人: 3 つ全部
    both = accounts_services.create_department("作り物の事務の部署", ["masters", "orders"], [], None)
    for person in (
        account("jimu", departments=[both], permissions=["master_edit", "slip_edit"]),
        account("kanrisha", full_control=True),
    ):
        part = first_steps(frame_for(person))
        assert [label for _url, label in STEP.findall(part)] == ["取引先を登録する", "部品を登録する", "受注を入れる"], person.username
