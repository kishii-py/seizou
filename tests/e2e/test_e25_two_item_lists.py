"""E-25 2 社の一覧の左右 (仕様書の 6 番の 3 節)。

手順: A 社と B 社の部品マスター (品目) の一覧を左右に並べる (11-02 に受注の一覧から変えた)。
期待: 列の名前と数 (答えた項目が列に出る)、会社名、色が違う。

この試験が見る所: 2 社にログインし、部品マスターの一覧 (`/masters/items/`) の見出しの列に、それぞれの会社の答えの YAML で
「一覧に出す」とした項目の見出しが出ること。左のメニューの会社の名前が 2 社で違うこと。
見ない所 (理由): 色 (CSS の値は決まりの検査と CSS の検査が見る)、列の数の絶対値 (土台の列は部品の側が変える)。
"""

import yaml
from playwright.sync_api import expect

from .conftest import COMPANIES_DIR, login, shot


def _list_labels(company) -> list[str]:
    answers = yaml.safe_load((COMPANIES_DIR / f"{company.id}.yaml").read_text(encoding="utf-8"))
    return [field["label"] for field in answers["item_fields"] if field.get("in_list")]


def test_e25_item_lists_differ(page_a, page_b, company_a, company_b):
    login(page_a, company_a)
    login(page_b, company_b)
    headers = {}
    for key, page, company in (("a", page_a, company_a), ("b", page_b, company_b)):
        page.goto(f"{company.base_url}/masters/items/")
        expect(page.locator("#rows tbody tr").first).to_be_visible()
        headers[key] = [text.strip() for text in page.locator("#rows thead th").all_inner_texts()]
        for label in _list_labels(company):
            assert label in headers[key], f"{company.id} の列に「{label}」が無い"
        expect(page.locator("aside").first).to_contain_text(company.name)
    assert headers["a"] != headers["b"]
    shot(page_a, "E-25", "a-items")
    shot(page_b, "E-25", "b-items")
