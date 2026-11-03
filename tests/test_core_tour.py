"""ガイドツアーの段の並び (core.tours)・段の JSON と markup (seizou_tour の tag と core/_tour.html)・JS の字の約束 (static/js/seizou-tour.js)。JS の動きは本物のブラウザで本体が見る。

Django の test client で HTML を見る。データは作り物だけ。
"""

import json
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

import seizou.core
import seizou.orders
from seizou.core.tours import TOURS, tour_for

CORE = Path(seizou.core.__file__).parent
ORDERS = Path(seizou.orders.__file__).parent
TOUR_JS = CORE / "static" / "js" / "seizou-tour.js"
ORDER_LIST = ORDERS / "templates" / "orders" / "order_list.html"
ORDERLINE_LIST = ORDERS / "templates" / "orders" / "orderline_list.html"

SELECTOR = re.compile(r'^(#[a-z][a-z0-9-]*|\[data-tour="[a-z][a-z0-9-]*"\]|section\[aria-labelledby="[a-z][a-z0-9-]*-title"\])$')

#: ダッシュボードの試験が「無いこと」を見る字 (段の字は部品の無い会社の画面にも入る)。協力企業の古い呼び名は test_wording が全部のファイルで見る
NOT_WRITTEN = (
    "はじめに", "品目", "注残の明細", "受信箱の未処理", "月次の推移", "納期アラート",
    "出荷 (直近 6 か月)", "生産 (直近 6 か月)", "trend-chart",
)

TOUR_JSON = re.compile(r'<script id="seizou-tour" type="application/json">(.*?)</script>', re.S)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


def page(client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


def test_tours_have_three_screens_with_fixed_keys():
    assert sorted(TOURS) == ["core:home", "orders:order_list", "orders:orderline_list"]
    for url_name, steps in TOURS.items():
        assert 4 <= len(steps) <= 6, url_name
        for step in steps:
            assert set(step) == {"selector", "title", "text"}, url_name
            assert all(step.values()), step
            assert SELECTOR.fullmatch(step["selector"]), step["selector"]
            for word in NOT_WRITTEN:
                assert word not in step["title"] and word not in step["text"], (url_name, word)
    assert tour_for("masters:item_list") is None
    assert tour_for("core:home") == [dict(step) for step in TOURS["core:home"]]


def test_tour_targets_exist_in_templates():
    data_tour_sources = "".join(
        path.read_text(encoding="utf-8")
        for path in (
            CORE / "templates" / "core" / "home.html",
            CORE / "templates" / "core" / "_topbar.html",
            ORDER_LIST,
            ORDERLINE_LIST,
        )
    )
    id_sources = (CORE / "templates" / "core" / "list.html").read_text(encoding="utf-8") + (
        ORDERS / "templates" / "orders" / "_board_filter.html"
    ).read_text(encoding="utf-8")
    home = (CORE / "templates" / "core" / "home.html").read_text(encoding="utf-8")
    for steps in TOURS.values():
        for step in steps:
            selector = step["selector"]
            if selector.startswith("#"):
                assert f'id="{selector[1:]}"' in id_sources, selector
            elif selector.startswith("[data-tour="):
                assert selector[1:-1] in data_tour_sources, selector
            else:
                labelled = re.search(r'aria-labelledby="[^"]+"', selector).group(0)
                assert labelled in home, selector


def test_home_page_has_tour_json_and_script(logged_in):
    html = page(logged_in, reverse("core:home"))
    found = TOUR_JSON.findall(html)
    assert len(found) == 1
    assert json.loads(found[0]) == tour_for("core:home")
    assert html.count("js/seizou-tour.js") == 1
    assert html.count('id="seizou-tour-root"') == 1
    assert '<div id="seizou-tour-root" hidden>' in html

    items = page(logged_in, reverse("masters:item_list"))
    assert 'id="seizou-tour"' not in items
    assert "js/seizou-tour.js" not in items
    assert 'id="seizou-tour-root"' not in items


def test_guide_modal_has_start_button_only_with_tour(logged_in):
    home = logged_in.get(reverse("core:screen_guide", args=["core_home"]), HTTP_HX_REQUEST="true").content.decode()
    assert "data-tour-start" in home
    assert "順に見る" in home
    items = logged_in.get(reverse("core:screen_guide", args=["masters_item_list"]), HTTP_HX_REQUEST="true").content.decode()
    assert "data-tour-start" not in items
    assert "順に見る" not in items


def test_order_templates_add_only_data_tour():
    for path in (ORDER_LIST, ORDERLINE_LIST):
        text = path.read_text(encoding="utf-8")
        assert text.count('data-tour="') == 2, path.name
        assert text.count('data-tour="') <= 3, path.name


def test_tour_js_rules():
    text = TOUR_JS.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 150
    for word in ("classList", "className", "innerHTML", "insertAdjacentHTML", "outerHTML", "fetch(", "XMLHttpRequest", "localStorage"):
        assert word not in text, word
    assert "csrf" not in text.lower()
    assert "Escape" in text
    assert "textContent" in text
    assert "window.seizouTour" in text
