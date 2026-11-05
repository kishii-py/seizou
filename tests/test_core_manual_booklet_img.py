"""使い方の冊子の絵の大きさ (依頼書 N171): 縦に長い画面の絵を、1 ページに収まる高さに縮める style の字。

WeasyPrint は回さない (冊子の HTML の字だけを見る)。試験ごとに settings.BASE_DIR を一時の置き場に替え、その下の manual/ に作り物の断片と絵を書く。
"""

import re

import pytest

from seizou.core import manual, reports

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def folder(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    (tmp_path / "manual" / "images").mkdir(parents=True)
    return tmp_path / "manual"


def booklet_html() -> str:
    return reports.render_html(manual.BOOKLET_TEMPLATE, manual.booklet_context())


def test_chapter_img_has_max_height(db, folder):
    rules = re.findall(r"^\.booklet-chapter img \{([^}]*)\}$", booklet_html(), re.M)
    assert len(rules) == 1
    for word in ("max-width: 100%;", "height: auto;", "max-height: 200mm;", "object-fit: contain;"):
        assert word in rules[0], word


def test_shot_is_inside_chapter(db, folder):
    (folder / "masters_item_list.html").write_text("<section><h2>部品マスター</h2><p>作り物の説明の字</p></section>", encoding="utf-8")
    (folder / "images" / "masters_item_list.png").write_bytes(PNG)
    box = re.search(r'<div id="masters_item_list" class="booklet-chapter">(.*?)</div>', booklet_html(), re.S)
    assert box, "部品マスターの章が無い"
    assert '<figure class="seizou-manual-shot"><img src="images/masters_item_list.png"' in box.group(1)
