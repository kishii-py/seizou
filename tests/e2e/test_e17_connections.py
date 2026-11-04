"""E-17 取引先 (仕様書の 6 番の 3 節)。

手順: 一覧と入力。
期待: 名前・受け口の URL・鍵の置き場の名前・向き (鍵の値は出ない)。

この試験が見る所 (つながりの一覧 `/connect/connections/`): 2 社に同じ鍵が渡っていれば、2 社それぞれの一覧に、種の
`connections.yaml` の取引先の名前・向き・受け口の URL (空でなければ)・鍵の置き場の名前が出て、鍵の設定が「済み」であり、
鍵の値が画面の字にも HTML にも無い。外の置き場 (`company.external`) では、受け口の URL を種の URL と比べず、相手の
置き場の URL (`base_url`) と種の道 (`/connect/receive/order/`) をつないだ物と比べる (`seizou new --demo` の `partner_base_urls`)。
見ない所 (理由): 入力の画面 (つながりの入力画面は作っていない)。
"""

import os
from urllib.parse import urlsplit

import pytest
import yaml

from .conftest import COMPANIES_DIR, PARTNER_SECRET_ENV, has_partner_secret, login, seed_name, shot

NO_SECRET_REASON = "2 社に同じ鍵が渡っていない (SEIZOU_E2E_PARTNER_SECRET)"


def _seed_connections(company) -> list[dict]:
    path = COMPANIES_DIR / company.id / "connections.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["つながり"]


def _wanted_url(seed_url: str, company, partner) -> str:
    """一覧に出るはずの受け口の URL。手元は種の URL のまま。外の置き場は、相手の base_url と種の道をつないだ物。"""
    if not company.external:
        return seed_url
    return partner.base_url + urlsplit(seed_url).path


def test_e17_connection_list(page_a, page_b, company_a, company_b):
    login(page_a, company_a)
    login(page_b, company_b)
    if not (has_partner_secret(page_a, company_a) and has_partner_secret(page_b, company_b)):
        pytest.skip(NO_SECRET_REASON)

    # 立っている runserver が鍵を持っていても、この process に値が無ければ、画面に値が出ないことを比べられない。
    secret = os.environ.get(PARTNER_SECRET_ENV)
    if not secret:
        pytest.skip(NO_SECRET_REASON)
    for page, company, partner, key in ((page_a, company_a, company_b, "a"), (page_b, company_b, company_a, "b")):
        page.goto(f"{company.base_url}/connect/connections/")
        rows = page.locator("#rows tbody tr")
        rows.first.wait_for()
        texts = [" ".join(cell.strip() for cell in row.locator("td").all_inner_texts()) for row in rows.all()]
        for seed in _seed_connections(company):
            name = seed_name(company, "partners", seed["取引先コード"])
            wanted = [name, seed["向き"], seed["鍵の置き場の名前"], "済み"]
            if seed["受け口の URL"]:
                wanted.append(_wanted_url(seed["受け口の URL"], company, partner))
            assert any(all(word in text for word in wanted) for text in texts), (
                f"{company.id} のつながりの行に {name} の項目が揃っていない"
            )
        if secret in page.locator("body").inner_text() or secret in page.content():
            pytest.fail("鍵の値が画面に出ている")
        shot(page, "E-17", f"{key}-connections")
