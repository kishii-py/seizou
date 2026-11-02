"""E-01 ログイン (仕様書の 6 番の 3 節)。

手順: 誤った ID で入る → 正しい ID で入る。
期待: 誤りは日本語の知らせ。正しければ受注の一覧へ。

今は最初の画面 (はじめに) を見る。受注の一覧ができたら相方が足す。
"""

import re

from .conftest import is_logged_in, login, shot

JAPANESE = re.compile(r"[ぁ-んァ-ン一-龥]")
UNKNOWN_ID = "no-such-user"
UNKNOWN_SECRET = "not-a-real-secret"


def test_e01_wrong_id_shows_japanese_notice(page_a, company_a):
    login(page_a, company_a, user=UNKNOWN_ID, password=UNKNOWN_SECRET)
    assert page_a.url.rstrip("/").endswith("/login")
    notice = page_a.locator('[role="alert"]').first
    notice.wait_for(state="visible")
    assert JAPANESE.search(notice.inner_text())
    assert not is_logged_in(page_a, company_a)
    shot(page_a, "E-01", "a-wrong-id")


def test_e01_correct_id_reaches_first_screen(page_a, company_a):
    login(page_a, company_a)
    assert is_logged_in(page_a, company_a)
    assert page_a.url.rstrip("/").endswith("/login") is False
    shot(page_a, "E-01", "a-logged-in")
