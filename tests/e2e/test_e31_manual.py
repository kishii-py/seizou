"""E-31 使い方 (画面ごとの説明書。仕様書の 6 番の 3 節)。

手順: 上の帯の「使い方」→ 目次と本文 → 印刷用。
期待: 画面の目録の順に並び、断片が無い画面には案内が出る。印刷用は 1 枚 (N18)。

この試験が見る所: A 社にログインし、上の帯の「使い方」(`aria-label` が「使い方」の link) を押すと道が `/manual/` になる。
目次 (`nav[aria-label=目次]` の link) が 1 件以上あり、目次の link 先 (`#<anchor>`) の並びと、本文の節 (同じ id の要素) の
並びが同じ。目次の「 (まだ)」の数と、本文の「この画面の説明はまだありません。」の数が同じ (断片が無い画面には案内が出る)。
置き場に `manual/` のフォルダが無いときは「この会社の説明書はまだありません。」が出て、有るときは出ない。
「印刷用を開く」は新しいタブで開き、道が `/manual/` で `print=1` を持ち、左のメニュー (`aside`) が無く、
目次の link 先の並びが本文の画面と同じ。
見ない所 (理由): 目録そのものとの突き合わせ (置き場の命令の出力を読む形になり大きい。目次と本文の並びが同じことで見た)、
断片の中身 (置き場によって違う)、紙の枚数 (左のメニューの無い 1 つの HTML であることだけを見た)、
外の置き場 (`SEIZOU_E2E_BASE_URL_…`) での「この会社の説明書はまだありません。」の数 (置き場の `manual/` の有無を読めない。絵で見る)。
"""

import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect

from .conftest import login, shot

STILL_NONE = " (まだ)"
FRAGMENT_NONE = "この画面の説明書はまだありません。"
MANUAL_NONE = "この会社の説明書はまだありません。"


def _toc_anchors(page) -> list[str]:
    hrefs = page.locator("nav[aria-label=目次] a").evaluate_all("links => links.map(a => a.getAttribute('href'))")
    return [href.removeprefix("#") for href in hrefs]


def _body_ids_in_order(page, anchors: list[str]) -> list[str]:
    """文書の中で anchors に当たる id の並び (文書の先頭から)。"""
    wanted = set(anchors)
    ids = page.locator("[id]").evaluate_all("els => els.map(e => e.id)")
    return [identifier for identifier in ids if identifier in wanted]


def test_e31_toc_body_and_print(context, page_a, company_a):
    login(page_a, company_a)
    page_a.locator("header a[aria-label=使い方]").click()
    page_a.wait_for_url(re.compile(r"/manual/$"))

    toc = page_a.locator("nav[aria-label=目次] a")
    assert toc.count() >= 1
    anchors = _toc_anchors(page_a)
    assert len(set(anchors)) == len(anchors)
    assert _body_ids_in_order(page_a, anchors) == anchors

    texts = [text.strip() for text in toc.all_inner_texts()]
    not_yet = [text for text in texts if text.endswith(STILL_NONE.strip())]
    expect(page_a.get_by_text(FRAGMENT_NONE)).to_have_count(len(not_yet))

    if not company_a.external:  # 外の置き場の manual/ の有無は読めない (案内が出るかは絵で見る)
        folder_exists = (company_a.site / "manual").is_dir()
        expect(page_a.get_by_text(MANUAL_NONE)).to_have_count(0 if folder_exists else 1)
    shot(page_a, "E-31", "a-manual")

    with context.expect_page() as opened:
        page_a.get_by_role("link", name="印刷用を開く").click()
    printed = opened.value
    printed.wait_for_load_state("load")
    url = urlparse(printed.url)
    assert url.path == "/manual/"
    assert parse_qs(url.query).get("print") == ["1"]
    expect(printed.locator("aside")).to_have_count(0)
    assert _toc_anchors(printed) == anchors
    assert _body_ids_in_order(printed, anchors) == anchors
    shot(printed, "E-31", "a-print")
