"""E-58 フィードバックの画面の上のタブ 3 つで、送る画面と一覧と「みんなの Issue」を切り替える (依頼書 N321。決め 11-07 09:1x〜09:2x)。

手順: A 社で /feedback/new/ を開く → 帯の「送った一言」を押す → 帯の「みんなの Issue」を押す。
期待: 送る画面では帯があり「送る」が選ばれている。「送った一言」で /feedback/ に移り、表 (#rows) が出て「送った一言」が選ばれる。
「みんなの Issue」で /feedback/?tab=issues に移り、Issue を探す節 (#issues) が出て、表と絞り込みが無く、「みんなの Issue」が選ばれる。

この試験が見る所: `#feedback-tabs` の数・3 つのタブの `aria-selected`・URL・`#rows` と `#list-card` と `#issues` の数と見え方。
見ない所 (理由): 「探す」を押す形 (GitHub を読むので押さない。探した後もタブが保たれることは tests/test_feedback_views.py が
requests を差し替えて見る)、「送った一言」の件数の値 (A 社の DB に残るフィードバックの数で変わる。件数の絞りは
tests/test_feedback_views.py が見る)、B 社 (帯は 2 社で同じ)、左のメニューの入口 (この依頼では変えない)。
A 社の DB に何も足さない。
"""

from playwright.sync_api import expect

from .conftest import login, shot


def test_e58_feedback_tabs(page_a, company_a):
    login(page_a, company_a)
    page_a.goto(f"{company_a.base_url}/feedback/new/")
    send = page_a.locator("#feedback-tab-send")
    sent = page_a.locator("#feedback-tab-sent")
    issues = page_a.locator("#feedback-tab-issues")
    expect(page_a.locator("#feedback-tabs")).to_have_count(1)
    expect(send).to_have_attribute("aria-selected", "true")
    expect(sent).not_to_have_attribute("aria-selected", "true")
    expect(issues).not_to_have_attribute("aria-selected", "true")

    sent.click()
    expect(page_a).to_have_url(f"{company_a.base_url}/feedback/")
    expect(page_a.locator("#rows")).to_have_count(1)
    expect(sent).to_have_attribute("aria-selected", "true")
    expect(send).not_to_have_attribute("aria-selected", "true")

    issues.click()
    expect(page_a).to_have_url(f"{company_a.base_url}/feedback/?tab=issues")
    expect(page_a.locator("#issues")).to_be_visible()
    expect(page_a.locator("#rows")).to_have_count(0)
    expect(page_a.locator("#list-card")).to_have_count(0)
    expect(issues).to_have_attribute("aria-selected", "true")
    expect(sent).not_to_have_attribute("aria-selected", "true")
    shot(page_a, "E-58", "a-feedback-tabs")
