"""使い方の業務の流れの章 (依頼書 N169): 冊子・使い方の画面・印刷の 1 枚に、FLOWS から機械で描く。

断片は使わない。試験ごとに settings.BASE_DIR を一時の置き場に替える (manual/ は空)。部品を外した会社は、目録 (all_screens) から部品の画面を外して見る。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import manual, reports
from seizou.core.screens import all_screens
from seizou.orders import timeline as orders_timeline
from seizou.purchasing import timeline as purchasing_timeline

FLOW_TITLES = ["注文を受けてから入金まで", "発注から受入まで", "取引先とつなぐ"]
FLOWS_BOX = re.compile(r'<div id="flows"[^>]*>(.*?)</div>', re.S)
#: 使い方の画面 ({}) と印刷の 1 枚 (print=1) の GET
OUTLETS = [{}, {"print": "1"}]


@pytest.fixture
def client_logged_in(client, db):
    client.force_login(get_user_model().objects.create_user(username="tester", password="tests-only"))
    return client


@pytest.fixture
def folder(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    (tmp_path / "manual").mkdir()
    return tmp_path / "manual"


def without(monkeypatch, *apps):
    """目録から部品 (app) の画面を外す (部品の入っていない会社と同じ目録)。"""
    screens = [screen for screen in all_screens() if screen.app not in apps]
    monkeypatch.setattr(manual, "all_screens", lambda: screens)


def booklet_html() -> str:
    return reports.render_html(manual.BOOKLET_TEMPLATE, manual.booklet_context())


def flows_box(html: str) -> str:
    box = FLOWS_BOX.search(html)
    assert box, "業務の流れの箱が無い"
    return box.group(1)


def test_steps_are_screens_and_follow_stages():
    names = {screen.url_name for screen in all_screens()}
    assert [flow["title"] for flow in manual.FLOWS] == FLOW_TITLES
    for flow, stages in zip(manual.FLOWS, (orders_timeline.STAGES, purchasing_timeline.STAGES, purchasing_timeline.STAGES)):
        assert all(step["url_name"] in names for step in flow["steps"]), flow["title"]
        reached = [step["stage"] for step in flow["steps"] if step["stage"]]
        assert set(reached) <= set(stages), flow["title"]
        assert [stages.index(stage) for stage in reached] == sorted(stages.index(stage) for stage in reached), flow["title"]
        assert not flow["steps"][0].get("remote"), flow["title"]


def test_booklet_has_flows_before_first_chapter(folder, db):
    html = booklet_html()
    first = manual.fragment_name(all_screens()[0].url_name)
    assert html.count("<h2>業務の流れ</h2>") == 1
    flows_at = html.index('<div id="flows" class="booklet-flows">')
    assert html.index('<nav class="booklet-toc"') < flows_at < html.index(f'<div id="{first}" class="booklet-chapter')
    assert [title for title in FLOW_TITLES if f"<h3>{title}</h3>" in flows_box(html)] == FLOW_TITLES
    assert 'href="#flows"' not in html


@pytest.mark.parametrize("params", OUTLETS)
def test_page_and_print_show_flows_before_screens(client_logged_in, folder, params):
    response = client_logged_in.get(reverse("core:manual"), params)
    assert response.status_code == 200
    html = response.content.decode()
    first = manual.fragment_name(all_screens()[0].url_name)
    assert html.count("<h2>業務の流れ</h2>") == 1
    assert html.index('<div id="flows"') < html.index(f'<div id="{first}"')
    assert [title for title in FLOW_TITLES if f"<h3>{title}</h3>" in flows_box(html)] == FLOW_TITLES
    assert 'href="#flows"' not in html


def test_step_links_land_on_ids_of_same_page(client_logged_in, folder):
    own = sum(1 for flow in manual.FLOWS for step in flow["steps"] if not step.get("remote"))
    pages = [booklet_html(), *(client_logged_in.get(reverse("core:manual"), params).content.decode() for params in OUTLETS)]
    for html in pages:
        anchors = re.findall(r'href="#([^"]+)"', flows_box(html))
        assert len(anchors) == own
        for anchor in anchors:
            assert f'id="{anchor}"' in html, anchor
    assert '相手の会社の「注文を受け入れる」' in flows_box(pages[0])


def test_steps_drop_without_parts(client_logged_in, folder, monkeypatch):
    without(monkeypatch, "billing")
    sales = manual.manual_flows()[0]
    assert sales["title"] == FLOW_TITLES[0]
    assert "billing_invoice_create" not in [step["anchor"] for step in sales["steps"]]
    assert [step["stage"] for step in sales["steps"]][-1] == "納品"
    without(monkeypatch, "connect")
    flows = manual.manual_flows()
    assert [flow["title"] for flow in flows] == FLOW_TITLES[:2]
    assert not any(step["remote"] or step["anchor"].startswith("connect_") for flow in flows for step in flow["steps"])
    without(monkeypatch, "orders", "purchasing", "connect")
    assert manual.manual_flows() == []
    for params in OUTLETS:
        assert "<h2>業務の流れ</h2>" not in client_logged_in.get(reverse("core:manual"), params).content.decode()
    assert "<h2>業務の流れ</h2>" not in booklet_html()


def test_booklet_flows_stay_out_of_bookmarks_and_counter(folder, db):
    html = booklet_html()
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert ".booklet-flows h2, .booklet-flows h3 { bookmark-level: none; }" in style
    assert ".booklet-flows a::after { content: \" (\" target-counter(attr(href), page) \" ページ)\"; }" in style
    assert "booklet-chapter" not in html[html.index('<div id="flows"') : html.index("</div>", html.index('<div id="flows"'))]


@pytest.mark.pdf
def test_booklet_pdf_bookmarks_unchanged(folder, db):
    document = reports._render_document(manual.BOOKLET_TEMPLATE, manual.booklet_context())
    label, _, children, _ = document.make_bookmark_tree()[0]
    assert label == "使い方"
    assert [child[0] for child in children[:2]] == ["この冊子について", "目次"]
    assert children[1][2] == []
    assert len(children) == 2 + len(all_screens())
