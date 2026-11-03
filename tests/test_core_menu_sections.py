"""左のメニューの大項目と中項目 (seizou.core.context_processors.sections)。

決め 11-03 14:1x (北極星 7 節「11-03 の昼の決め」): 大項目ごとに開け閉めし、今の画面の大項目だけ開く。既定の並びは
ダッシュボード / 製造部 / 生産管理部 (中項目 受注管理・協力企業管理) / 出荷と請求 / マスター / 文書 (製造業なので製造部を
ダッシュボードのすぐ下に)。会社の設定 menu (答えの YAML の settings) があればその括りで分け、同じ画面を 2 つの括りに出せる。
"""

import re

import pytest
from django.urls import reverse

from seizou.core.context_processors import SECTIONS, core_menu, custom_sections, mark_active, menu, sections


def _labels(groups):
    return [group["label"] for group in groups]


def _group(groups, label):
    return next(group for group in groups if group["label"] == label)


def _entries(groups, label):
    return [item["label"] for group in groups if group["label"] == label for item in group["items"]]


def test_default_sections_follow_the_decided_order(db):
    assert [label for label, _subsections in SECTIONS] == ["ダッシュボード", "製造部", "生産管理部", "出荷と請求", "マスター", "文書"]
    groups = sections([*core_menu(), *menu()])

    assert _labels(groups) == ["ダッシュボード", "製造部", "生産管理部", "出荷と請求", "マスター", "文書"]
    # 製造部はダッシュボードのすぐ下 (製造業なので製造部が主役)。中項目の無い大項目は、見出しの無い中項目 1 つ
    made = _entries(groups, "製造部")
    assert made[0] == "指示書" and "進捗" in made and "在庫" in made
    assert [group["label"] for group in _group(groups, "製造部")["groups"]] == [""]
    # 受注管理と協力企業管理は生産管理部の中項目
    assert [(group["label"], [item["label"] for item in group["items"]]) for group in _group(groups, "生産管理部")["groups"]] == [
        ("受注管理", ["受注", "届いた注文"]),
        ("協力企業管理", ["発注", "受入"]),
    ]
    assert _entries(groups, "生産管理部") == ["受注", "届いた注文", "発注", "受入"]
    assert "出荷" in _entries(groups, "出荷と請求") and "請求書" in _entries(groups, "出荷と請求")
    # アカウント管理は左のメニューに出ない (設定の画面から開く)
    assert "アカウント管理" not in [item["label"] for group in groups for item in group["items"]]


def test_sections_carry_icon_collapsible_and_active(db):
    items = [*core_menu(), *menu()]
    mark_active(items, reverse("purchasing:receipt_list"))
    groups = sections(items)

    # ダッシュボードの括りは開け閉め無し (入口を直に出す)。ほかは開け閉めする
    assert [group["collapsible"] for group in groups] == [False, True, True, True, True, True]
    assert [group["icon"] for group in groups] == ["home", "factory", "clipboard", "truck", "box", "book"]
    # 今の画面 (受入) の大項目だけ active
    assert [group["label"] for group in groups if group["active"]] == ["生産管理部"]
    assert [item["label"] for item in _group(groups, "生産管理部")["items"] if item["active"]] == ["受入"]


def test_company_menu_setting_groups_screens(db, settings):
    settings.SEIZOU = {
        "menu": [
            {"label": "営業", "screens": ["orders:order_list", "connect:inbox_list"]},
            {"label": "現場", "apps": ["workorders", "progress"], "screens": ["orders:order_list"]},
        ]
    }
    groups = sections([*core_menu(), *menu()])

    assert _labels(groups)[:3] == ["ダッシュボード", "営業", "現場"]
    assert _entries(groups, "営業") == ["受注", "届いた注文"]
    # 同じ画面 (受注) を 2 つの括りに出せる。app で選んだ入口も並ぶ
    assert "受注" in _entries(groups, "現場") and "指示書" in _entries(groups, "現場")
    # 括りに無い入口は「その他」に残る
    assert "部品マスター" in _entries(groups, "その他")
    # 会社の括りは中項目を持たず、開け閉めし、絵は最初の入口の物
    sales = _group(groups, "営業")
    assert [group["label"] for group in sales["groups"]] == [""] and sales["collapsible"] and sales["icon"] == "clipboard"


def test_broken_menu_setting_is_ignored(db, settings):
    settings.SEIZOU = {"menu": [{"apps": ["orders"]}, "x", {"label": "  ", "apps": ["orders"]}]}

    assert custom_sections() == []
    assert _labels(sections([*core_menu(), *menu()]))[:2] == ["ダッシュボード", "製造部"]


def _nav(client, url):
    body = client.get(url).content.decode()
    return re.search(r'<nav class="flex-1 overflow-y-auto p-4" aria-label="メニュー">(.*?)</nav>', body, re.S).group(1)


def _summary_labels(html):
    return re.findall(r"<summary[^>]*>.*?<span[^>]*>([^<]+)</span>", html, re.S)


@pytest.mark.django_db
def test_sidebar_renders_sections_as_details_and_opens_only_the_current_one(client):
    from django.contrib.auth import get_user_model

    client.force_login(get_user_model().objects.create_user(username="tester", password="tests-only"))
    nav = _nav(client, reverse("purchasing:receipt_list"))

    assert _summary_labels(nav) == ["製造部", "生産管理部", "出荷と請求", "マスター", "文書"]
    # ダッシュボードの入口は details の外 (開け閉め無し)
    assert nav.index(f'href="{reverse("core:home")}"') < nav.index("<details")
    # 今の画面 (受入) の大項目だけ open。1 つ開くとほかが閉じる (details の name をそろえる)
    opened = re.findall(r"<details[^>]*\bopen\b[^>]*>\s*<summary[^>]*>.*?<span[^>]*>([^<]+)</span>", nav, re.S)
    assert opened == ["生産管理部"]
    assert nav.count('name="seizou-menu-section"') == 5
    # 中項目の見出しと、今の画面の印
    assert nav.index("受注管理") < nav.index("協力企業管理")
    assert 'aria-current="page"' in nav and "受注・出荷" not in nav

    # ダッシュボードでは、どの大項目も開いていない
    assert re.findall(r"<details[^>]*\bopen\b", _nav(client, "/")) == []
