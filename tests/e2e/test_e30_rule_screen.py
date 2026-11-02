"""E-30 決まりの検査の画面 (仕様書の 6 番の 3 節)。

手順: 会社の置き場に土台を継がない画面のファイルを置いてサーバーを立て直す → どの画面でも全面の知らせ → 「この文を写す」→ 消して立て直す。
期待: 「決まりに沿っていません」の 1 枚だけが出て、番号と文と直し方とファイルと行がある。消すと元に戻る (決め 11-01 夕方。11-02 の夜の N16)。

この試験が見る所 (A 社だけ。B 社の置き場は触らない): ログインして `/orders/` が開くことを見た後 (違反の間はログインの画面も全面の画面になるので、
ログインは置く前に済ませる)、置き場に `views.py` (土台を継がない class 1 つ = R15) を置いて runserver を立て直す。`/orders/` と `/masters/items/` は
どちらも状態が 500・題が「決まりに沿っていません | <A 社の名前>」・見出し「決まりに沿っていません」で、本文に `R15`・`views.py`・「直し方:」があり、
左のメニュー (`aside`) が無い。置き場の `.seizou/violations.md` に `R15` と `views.py` がある (読むだけ)。「この文を写す」を押すと、
字が「写しました」に変わるか、写す文の箱が見える (headless の Chromium は clipboard を許さないことがあるので、どちらでも通す)。
ファイルを消して立て直すと `/orders/` が開き、`violations.md` が無い。
絵: 全面の画面にはファイルの絶対の道 (利用者の名前を含む) が出るので、撮る前に道の字 (`p.font-mono`) の置き場の頭を `~/seizou-sites/<id>/` の形に置き換える。
写す文の箱には絶対の道が出るので、押した後は撮らない。
見ない所 (理由): clipboard の中身 (許さない環境がある)、違反が 20 件を超えるときの「ほか n 件」、htmx への `HX-Redirect`、直す命令 (Claude Code の命令でブラウザの試験の外)。
`finally` の立て直しが失敗すると、後の E-31・E-32 も落ちる (名前の順で E-30 は先に回る)。
"""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import login, restart_company, shot

VIOLATION_SOURCE = '''"""E2E の試験が置いて消す違反の見本"""

from django.views.generic import TemplateView


class E2eViolationView(TemplateView):
    """E2E の試験が置いて消す違反の見本"""
'''
MIDDLEWARE = "seizou.core.middleware.RuleNoticeMiddleware"
HEADING = "決まりに沿っていません"

SCRUB = """([site, home, shown]) => {
  document.querySelectorAll('p.font-mono').forEach((p) => {
    p.textContent = p.textContent.split(site).join(shown).split(home).join('~');
  });
}"""


def _open_orders(page, company):
    response = page.goto(f"{company.base_url}/orders/")
    return response


def test_e30_violation_screen_then_back(page_a, company_a):
    login(page_a, company_a)
    response = _open_orders(page_a, company_a)
    assert response.status == 200
    expect(page_a.locator("h1")).to_contain_text("受注")

    settings_text = (company_a.site / "settings.py").read_text(encoding="utf-8")
    if MIDDLEWARE not in settings_text:
        pytest.skip("置き場の settings.py に RuleNoticeMiddleware が無い (本体が置き場を今の main から作り直す)")
    violating = company_a.site / "views.py"
    if violating.exists():
        pytest.fail("置き場に views.py が既にある。この試験はほかのファイルを書き換えない")
    report = company_a.site / ".seizou" / "violations.md"

    try:
        violating.write_text(VIOLATION_SOURCE, encoding="utf-8")
        restart_company(company_a)
        for path in ("/orders/", "/masters/items/"):
            response = page_a.goto(f"{company_a.base_url}{path}")
            assert response.status == 500
            assert page_a.title() == f"{HEADING} | {company_a.name}"
            expect(page_a.locator("h1")).to_have_text(HEADING)
            body = page_a.locator("body")
            for word in ("R15", "views.py", "直し方:"):
                expect(body).to_contain_text(word)
            expect(page_a.locator("aside")).to_have_count(0)
        assert report.is_file()
        written = report.read_text(encoding="utf-8")
        assert "R15" in written and "views.py" in written

        page_a.evaluate(SCRUB, [str(company_a.site), str(Path.home()), f"~/seizou-sites/{company_a.id}"])
        shown = " ".join(page_a.locator("p.font-mono").all_inner_texts())
        assert str(Path.home()) not in shown
        shot(page_a, "E-30", "a-violation")

        page_a.click("#rule-copy")
        page_a.wait_for_function(
            "() => document.getElementById('rule-copy-label').textContent === '写しました'"
            " || !document.getElementById('rule-copy-text').hidden"
        )
    finally:
        violating.unlink(missing_ok=True)
        restart_company(company_a)

    response = _open_orders(page_a, company_a)
    assert response.status == 200
    expect(page_a.locator("h1")).to_contain_text("受注")
    assert not report.exists()
    shot(page_a, "E-30", "a-back")
