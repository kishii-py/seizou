"""E-32 CSV 取り込み (仕様書の 6 番の 3 節)。

手順: 部品マスターの一覧の「CSV 取り込み」→ 一覧の CSV をそのまま置く → 1 行だけ壊した CSV。
期待: 往復で差が無い。壊れた行があれば 1 件も入れずに行と欄と理由の表が出る (05 の 8 節。N20)。

この試験が見る所 (1 本目): A 社の部品マスターの一覧の「CSV 取り込み」を開き、一覧の CSV (`?format=csv`。ログインの cookie は文脈が持つ)
をそのまま選んで「保存」を押す。「CSV の誤り」が出ず、緑の知らせ (`.seizou-toast[role=status]`) が出る。
もう一度取った一覧の CSV が、取り込みの前と bytes で同じ (往復で差が無い)。種の部品も同じ値で「更新」される (値は変わらない)。
見る所 (2 本目): 一覧の CSV の見出しの下の 2 行目の「標準単価」の列を `abc` に変え、印の品番の新しい行を末尾に足して送る。
道が取り込みの画面のまま。「CSV の誤り」の表 (行・欄・理由) に、壊した行 (見出しを 1 行目に数えて「3 行目」) と、その列の見出しがある。
その後の一覧の CSV が送る前と bytes で同じで、一覧の絞り込み (`#list-q`) に印の品番を入れると 0 行 (1 件も入っていない)。
見ない所 (理由): 入れた件数・更新した件数 (成功の画面は件数を出さない。N20 の要望 1)、文字コードの選び方 (既定の UTF-8 のまま)、
30 件の CSV と壊した CSV の別の形 (06 の E-23 は E-32 と同じ画面。06 の整理は本体)。
"""

import csv
import io
import re
from urllib.parse import urlparse

from playwright.sync_api import expect

from .conftest import login, shot, unique_mark

PRICE_HEADER_PREFIX = "標準単価"


def _list_csv(context, company) -> bytes:
    response = context.request.get(f"{company.base_url}/masters/items/?format=csv")
    assert response.status == 200
    return response.body()


def _open_import(page, company) -> None:
    page.goto(f"{company.base_url}/masters/items/")
    page.get_by_role("link", name="CSV 取り込み").click()
    # 入口は開いた一覧の道を ?next= で渡す (取り込んだ後に一覧へ戻る。494 回目) ので、道の後ろに ? が続いてよい
    page.wait_for_url(re.compile(r"/masters/items/import/(\?|$)"))


def _send(page, path) -> None:
    page.set_input_files("input[name=file]", str(path))
    page.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()  # 主のボタンの字は画面ごとの動詞 (205 回目から) なので、字でなく主色の submit で押す


def test_e32_round_trip_no_diff(context, page_a, company_a, tmp_path):
    login(page_a, company_a)
    before = _list_csv(context, company_a)
    csv_path = tmp_path / "items.csv"
    csv_path.write_bytes(before)

    _open_import(page_a, company_a)
    _send(page_a, csv_path)
    expect(page_a.locator(".seizou-toast[role=status]")).to_be_visible()
    expect(page_a.get_by_text("CSV の誤り")).to_have_count(0)
    # 取り込んだ後は、開いていた部品マスターの一覧へ戻る (ダッシュボードへ移らない。494 回目)
    assert urlparse(page_a.url).path == "/masters/items/"
    shot(page_a, "E-32", "a-import")

    assert _list_csv(context, company_a) == before


def test_e32_broken_row_imports_nothing(context, page_a, company_a, tmp_path):
    login(page_a, company_a)
    before = _list_csv(context, company_a)
    rows = list(csv.reader(io.StringIO(before.decode("utf-8-sig"))))
    header = rows[0]
    price_index = next(i for i, name in enumerate(header) if name.startswith(PRICE_HEADER_PREFIX))
    price_header = header[price_index]
    assert len(rows) >= 3
    rows[2][price_index] = "abc"
    mark = unique_mark("e32")
    new_row = [""] * len(header)
    new_row[header.index("自社品番")] = mark
    new_row[header.index("品名")] = "E2E 試験の部品"
    rows.append(new_row)

    broken = tmp_path / "broken.csv"
    with broken.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)

    _open_import(page_a, company_a)
    _send(page_a, broken)
    errors = page_a.locator("h2", has_text="CSV の誤り")
    expect(errors).to_be_visible()
    assert urlparse(page_a.url).path == "/masters/items/import/"
    table_rows = page_a.locator("table tbody tr").filter(has_text="3 行目")
    expect(table_rows.first).to_be_visible()
    expect(table_rows.filter(has_text=price_header).first).to_be_visible()
    shot(page_a, "E-32", "a-errors")

    assert _list_csv(context, company_a) == before
    page_a.goto(f"{company_a.base_url}/masters/items/")
    page_a.fill("#list-q", mark)
    expect(page_a.locator("#rows tbody tr")).to_have_count(0)
