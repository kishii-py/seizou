"""E-44 ログインの画面の会社のロゴ (148 回目。11-04 の回帰の守り)。

手順: A 社の管理者で設定の「会社の情報」の「編集」→ ロゴに作り物の PNG を上げて保存 → ログアウト → ログインの画面を開く → (後片付け) ロゴを「クリア」。
期待: ログインしていないログインの画面に、上げたロゴの絵が出る (絵が読める)。

この試験が見る所: 試験が作る 1×1 の作り物の PNG を `input[name=logo]` に入れて保存し、設定の画面へ戻ったことを見る。
利用者のメニューの「ログアウト」の後に `/login/` を開き、`img` の `src` が `/media/company/logo` で始まり `.png` で終わる絵
(置き場に前の回の `logo.png` が残っていると、保存の名前に印の字が付く = 古いファイルは消さない決まり) が読める (`naturalWidth` が 0 でない)。
終わりに (落ちても) もう一度ログインして「クリア」で外し、ログインの画面に絵が無いことを見る。
見ない所 (理由): ロゴの無い会社の頭文字の枠 (B 社。単体の試験 tests/test_core_media.py と tests/test_core_company_info.py が見る)、納品書と請求書の帳票のロゴ (E-13・E-29)、
ログインしていないときに会社の置き場のほかのファイルが開けないこと (単体の試験 tests/test_core_media.py が見る)。
"""

import base64
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from .conftest import login, shot, wait_image_loaded

# 1×1 の透明な PNG (作り物。試験が tmp_path に書く)。
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
LOGO_SRC = re.compile(r"/media/company/logo[^/]*\.png$")


def _on_login(page) -> bool:
    return urlparse(page.url).path.rstrip("/") == "/login"


def _logout(page) -> None:
    page.locator("summary[aria-label=利用者のメニュー]").click()
    page.locator("header details").get_by_role("button", name="ログアウト").click()
    page.wait_for_url(re.compile(r"/login/"))


def _clear_logo(page, company) -> None:
    page.goto(f"{company.base_url}/settings/company/")
    clear = page.locator("input[name=logo-clear]")
    if clear.count():
        clear.check()
        page.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()
        page.wait_for_url(re.compile(r"/settings/$"))


def test_e44_logo_on_login_page(page_a, company_a, tmp_path):
    logo = tmp_path / "e2e-logo.png"
    logo.write_bytes(PNG_1X1)
    login(page_a, company_a)
    try:
        page_a.goto(f"{company_a.base_url}/settings/")
        page_a.locator("#company-info-title").locator("xpath=..").get_by_role("link", name="編集").click()
        page_a.wait_for_url(re.compile(r"/settings/company/$"))
        page_a.set_input_files("input[name=logo]", str(logo))
        page_a.locator("form[data-enter-guard] button.btn-primary[type=submit]").first.click()
        page_a.wait_for_url(re.compile(r"/settings/$"))

        _logout(page_a)
        page_a.goto(company_a.login_url)
        if not _on_login(page_a):
            pytest.skip("立っていた runserver が開発機の自動ログイン (SEIZOU_DEV_AUTO_LOGIN=1) で動いていて、ログインの画面が開かない")
        image = page_a.locator("img[src*='/media/company/logo']").first
        expect(image).to_have_attribute("src", LOGO_SRC)
        wait_image_loaded(page_a, image)
        shot(page_a, "E-44", "a-login-logo")
    finally:
        if _on_login(page_a):
            login(page_a, company_a)
        _clear_logo(page_a, company_a)
    _logout(page_a)
    page_a.goto(company_a.login_url)
    expect(page_a.locator("img[src*='/media/company/']")).to_have_count(0)
