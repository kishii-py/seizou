"""E-17 取引先 (仕様書の 6 番の 3 節)。

手順: 一覧と入力。
期待: 名前・受け口の URL・鍵の置き場の名前・向き (鍵の値は出ない)。

この試験が見る所 (つながりの一覧 `/connect/connections/`): 2 社に同じ鍵が渡っていれば、2 社それぞれの一覧に、種の
`connections.yaml` の取引先の名前・向き・受け口の URL (空でなければ)・鍵の置き場の名前が出て、鍵の設定が「済み」であり、
鍵の値が画面の字にも HTML にも無い。
見ない所 (理由): 入力の画面 (つながりの入力画面は作っていない)。
"""

import os

import pytest
import yaml

from .conftest import COMPANIES_DIR, PARTNER_SECRET_ENV, has_partner_secret, login, seed_name, shot

NO_SECRET_REASON = "2 社に同じ鍵が渡っていない (SEIZOU_E2E_PARTNER_SECRET)"


def _seed_connections(company) -> list[dict]:
    path = COMPANIES_DIR / company.id / "connections.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["つながり"]


def test_e17_connection_list(page_a, page_b, company_a, company_b):
    login(page_a, company_a)
    login(page_b, company_b)
    if not (has_partner_secret(page_a, company_a) and has_partner_secret(page_b, company_b)):
        pytest.skip(NO_SECRET_REASON)

    secret = os.environ[PARTNER_SECRET_ENV]
    for page, company, key in ((page_a, company_a, "a"), (page_b, company_b, "b")):
        page.goto(f"{company.base_url}/connect/connections/")
        rows = page.locator("#rows tbody tr")
        rows.first.wait_for()
        texts = [" ".join(cell.strip() for cell in row.locator("td").all_inner_texts()) for row in rows.all()]
        for seed in _seed_connections(company):
            name = seed_name(company, "partners", seed["取引先コード"])
            wanted = [name, seed["向き"], seed["鍵の置き場の名前"], "済み"]
            if seed["受け口の URL"]:
                wanted.append(seed["受け口の URL"])
            assert any(all(word in text for word in wanted) for text in texts), (
                f"{company.id} のつながりの行に {name} の項目が揃っていない"
            )
        if secret in page.locator("body").inner_text() or secret in page.content():
            pytest.fail("鍵の値が画面に出ている")
        shot(page, "E-17", f"{key}-connections")
