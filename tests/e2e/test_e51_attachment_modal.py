"""E-51 受注の詳細から小窓で添付ファイルを付ける (N157。決め 11-05 11:4x)。

手順: A 社で受注を 1 件入れる (受注の詳細へ移る) → 「添付ファイル」の枠の「添付ファイルを付ける」→ ファイルを選ばずに保存 →
作り物の PDF を選び、備考を入れて保存。
期待: 詳細の上に入力の小窓が開き、ファイルの無い保存は小窓のままファイルの欄の下に赤の字が出る。ファイルを選ぶとドラッグの枠の下に
名前が出て、保存すると小窓が閉じ、右上に知らせが出て、画面を移らずに枠の行と数の印だけが変わる。

この試験が見る所: 枠の `a[data-modal-form]` を押すと `#seizou-modal` に `open` が付き、`#seizou-modal-box` の中に
multipart の印 (`hx-encoding`) を持つ `form[hx-post]` とドラッグの枠 (`[data-file-drop]`) がある。何も選ばずに主色の submit を押すと
`open` は付いたまま `#id_file_error` が出る。`set_input_files` で選ぶと `[data-file-drop-name]` にファイルの名前が出る
(ドラッグの枠の JS が小窓の中でも一覧を描く)。保存すると `open` が外れ、`.seizou-toast[role=status]` が出て、`#attachments` に
ファイルの名前が出て、`#attachments-heading .badge` が 0 から 1 になる。画面の印 (`window.__e51`) と URL が残る (読み直していない)。
見ない所 (理由): ドラッグそのもの (ブラウザの外からのファイルの持ち込みは Playwright で作りにくい。手の確かめで見る)、
部品と指示書の詳細 (同じ枠。tests/test_attachments_modal.py が字で見る)、削除 (画面の全体の確かめのまま)、B 社 (同じテンプレート)。
受注を 1 件と添付ファイルを 1 つ増やす (E-41 と同じく A 社の DB に残る)。
"""

from playwright.sync_api import expect

from .conftest import login, new_order, seed_name, shot, unique_mark

CUSTOMER_CODE = "C001"
PART_NO = "WK-1001"
PDF_BYTES = b"%PDF-1.4\n% e2e\n"


def test_e51_attach_file_in_detail_modal(page_a, company_a, tmp_path):
    login(page_a, company_a)
    mark = unique_mark("e51")
    new_order(
        page_a,
        company_a,
        customer=seed_name(company_a, "partners", CUSTOMER_CODE),
        part_no=PART_NO,
        qty=10,
        unit_price=480,
        mark=mark,
    )
    detail = page_a.url
    page_a.evaluate("window.__e51 = 1")
    badge = page_a.locator("#attachments-heading .badge")
    expect(badge).to_have_text("0")

    page_a.locator("#attachments a[data-modal-form]").click()

    box = page_a.locator("#seizou-modal-box")
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    expect(box.locator('form[hx-post][hx-encoding="multipart/form-data"]')).to_have_count(1)
    expect(box.locator("[data-file-drop]")).to_be_visible()
    shot(page_a, "E-51", "a-modal")

    # ボタンの字で探さず、小窓の form の主色の submit で押す (E-41 と同じ形)。locator は押すたびに引き直す (誤りで小窓の中身が描き直される)。
    submit = box.locator("form[data-enter-guard] button.btn-primary[type=submit]").first
    submit.click()
    expect(page_a.locator("#seizou-modal[open]")).to_have_count(1)
    expect(box.locator("#id_file_error")).to_be_visible()

    path = tmp_path / f"{mark}.pdf"
    path.write_bytes(PDF_BYTES)
    # ファイルの欄は CSS で画面の外に置いてある (見えない欄でも set_input_files は入る。押して開かない)。
    box.locator("input[name=file]").set_input_files(str(path))
    expect(box.locator("[data-file-drop-name]")).to_have_text(f"{mark}.pdf")
    box.locator("textarea[name=note]").fill(mark)
    submit.click()

    expect(page_a.locator("#seizou-modal[open]")).to_have_count(0)
    expect(page_a.locator(".seizou-toast[role=status]").first).to_be_visible()
    expect(page_a.locator("#attachments")).to_contain_text(f"{mark}.pdf")
    expect(badge).to_have_text("1")
    assert page_a.evaluate("window.__e51") == 1, "詳細を読み直した (小窓の保存は画面を移らない)"
    assert page_a.url == detail
    shot(page_a, "E-51", "a-after")
