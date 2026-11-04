"""E2E の土台が、外の置き場 (VPS など) の URL を環境変数から読む所 (`tests/e2e/conftest.py` の `load_companies`)。

ブラウザも 2 社も要らない (読むだけ)。E2E の門 (`SEIZOU_E2E`) とは別に、普段の pytest で回る。
URL の値は作り物 (`example.jp`)。パスワードと鍵は読まない。
"""

import pytest

from tests.e2e import conftest as e2e

A_URL = "SEIZOU_E2E_BASE_URL_A_MACHINING"
B_URL = "SEIZOU_E2E_BASE_URL_B_PLATING"


@pytest.fixture
def no_urls(monkeypatch):
    monkeypatch.delenv(A_URL, raising=False)
    monkeypatch.delenv(B_URL, raising=False)


def test_local_runserver_when_no_url(no_urls):
    companies = e2e.load_companies()
    assert set(companies) == {"a_machining", "b_plating"}
    for company in companies.values():
        assert not company.external
        assert company.base_url == f"http://127.0.0.1:{company.port}"


def test_external_urls_are_used_without_trailing_slash(no_urls, monkeypatch):
    monkeypatch.setenv(A_URL, "https://a.example.jp/")
    monkeypatch.setenv(B_URL, " https://b.example.jp ")
    companies = e2e.load_companies()
    a, b = companies["a_machining"], companies["b_plating"]
    assert a.external and b.external
    assert a.base_url == "https://a.example.jp"
    assert a.login_url == "https://a.example.jp/login/"
    assert b.base_url == "https://b.example.jp"
    # 置き場の読みと環境変数の名前は手元と同じ (外の置き場でも、パスワードは同じ名前の環境変数で渡す)。
    assert a.password_env == "SEIZOU_E2E_PASSWORD_A_MACHINING"


def test_one_company_only_is_refused(no_urls, monkeypatch):
    monkeypatch.setenv(A_URL, "https://a.example.jp")
    with pytest.raises(RuntimeError, match=B_URL):
        e2e.load_companies()


def test_env_name_follows_company_id():
    assert e2e.external_url_env("b_plating") == B_URL
