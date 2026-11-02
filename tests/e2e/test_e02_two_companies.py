"""E-02 2 社の同時のログイン (仕様書の 6 番の 3 節)。

手順: A 社にログインした後、同じブラウザで B 社にログインし、A 社の画面を再読み込み。
期待: A 社のログインが切れていない (両方で操作できる)。

同じ文脈 (cookie の入れ物が 1 つ) で 2 社を開く。会社ごとに cookie の名前が違うので、
2 つの名前が並ぶ (名前だけを見る。値は読まない)。
"""

from .conftest import is_logged_in, login, shot


def test_e02_both_companies_stay_logged_in(context, page_a, page_b, company_a, company_b):
    login(page_a, company_a)
    assert is_logged_in(page_a, company_a)
    login(page_b, company_b)
    assert is_logged_in(page_b, company_b)

    page_a.reload()
    assert is_logged_in(page_a, company_a)
    assert is_logged_in(page_b, company_b)

    names = {cookie["name"] for cookie in context.cookies()}
    assert company_a.session_cookie in names
    assert company_b.session_cookie in names
    assert company_a.session_cookie == "seizou_a_machining_session"
    assert company_b.session_cookie == "seizou_b_plating_session"

    shot(page_a, "E-02", "a-after-b-login")
    shot(page_b, "E-02", "b-logged-in")
