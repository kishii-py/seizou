"""左のメニューの括り (seizou.core.context_processors.sections)。

決め 11-03 (02 の 8 節の候補 3 と 4): 既定の括りは 受注管理 / 協力企業管理 / 製造 / 出荷と請求 / マスター / 文書。
会社の設定 menu (答えの YAML の settings) があればその括りで分け、同じ画面を 2 つの括りに出せる。
"""

import pytest

from seizou.core.context_processors import SECTIONS, core_menu, custom_sections, menu, sections


def _labels(groups):
    return [group["label"] for group in groups]


def _entries(groups, label):
    return [item["label"] for group in groups if group["label"] == label for item in group["items"]]


def test_default_sections_follow_the_decided_order(db):
    assert [label for label, _apps in SECTIONS] == ["ダッシュボード", "受注管理", "協力企業管理", "製造", "出荷と請求", "マスター", "文書"]
    groups = sections([*core_menu(), *menu()])

    assert _labels(groups)[:5] == ["ダッシュボード", "受注管理", "協力企業管理", "製造", "出荷と請求"]
    assert "受注" in _entries(groups, "受注管理") and "届いた注文" in _entries(groups, "受注管理")
    assert _entries(groups, "協力企業管理") == ["発注"]
    assert "出荷" in _entries(groups, "出荷と請求") and "請求書" in _entries(groups, "出荷と請求")
    # アカウント管理は左のメニューに出ない (設定の画面から開く)
    assert "アカウント管理" not in [item["label"] for group in groups for item in group["items"]]


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


def test_broken_menu_setting_is_ignored(db, settings):
    settings.SEIZOU = {"menu": [{"apps": ["orders"]}, "x", {"label": "  ", "apps": ["orders"]}]}

    assert custom_sections() == []
    assert _labels(sections([*core_menu(), *menu()]))[:2] == ["ダッシュボード", "受注管理"]


@pytest.mark.django_db
def test_home_renders_the_default_section_labels(client):
    from django.contrib.auth import get_user_model

    client.force_login(get_user_model().objects.create_user(username="tester", password="tests-only"))
    body = client.get("/").content.decode()

    assert "協力企業管理" in body and "出荷と請求" in body and "受注管理" in body
    assert "受注・出荷" not in body
