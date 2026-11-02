"""E2E (ブラウザで画面を通して確かめる試験) の土台と約束。

相方が 1 画面 1 ファイルで試験を足すときの約束は次の 6 つ。

1. 1 画面 1 ファイル。ファイルの名前は `tests/e2e/test_e<2 桁>_<短い名前>.py`。2 桁は
   仕様書の 6 番の 3 節の E-xx の番号で、以後変えない (足すときは末尾に足す)。
   関数の名前は `test_e<2 桁>_<何を>`。
2. 1 ブラウザ 1 文脈で 2 社を開く。会社ごとに cookie の名前が違う
   (`seizou_<id>_session`) ので、同じ文脈 (cookie の入れ物が 1 つ) のまま A 社と B 社に
   同時にログインできる。この土台は `page_a` と `page_b` を同じ文脈の 2 つのタブとして渡す。
   別の文脈にしない。
3. 絵は `shot(page, "E-xx", "<場面>")` で撮る。名前の頭に E-xx を付け、
   `docs/e2e/<今日の日付 YYYYMMDD>/<code>_<scene>.png` に画面全体を撮る。
4. 門と環境変数。環境変数 `SEIZOU_E2E` が `1` のときだけ、このフォルダの試験が動く
   (無ければ全部 skip。CI と普段の `uv run pytest` に影響しない)。
   - `SEIZOU_E2E_SITES_DIR`: 会社の置き場。無ければ `~/seizou-sites`
   - `SEIZOU_E2E_USER`: 管理者の ID。無ければ `admin`
   - `SEIZOU_E2E_PASSWORD_A_MACHINING`: A 社の管理者のパスワード
   - `SEIZOU_E2E_PASSWORD_B_PLATING`: B 社の管理者のパスワード
   パスワードの値は、どのファイルにも、絵の名前にも、ログにも書かない。
5. 期待の文は、仕様書の 6 番の 3 節の「期待」の列をそのまま、試験の docstring に写す。
6. 作り物のデータだけが映る前提で書く。実在の会社名・人名・品番を試験に書かない。

会社の置き場 (`<SITES_DIR>/<id>/manage.py`) は `seizou new` が作る。この土台は作らない。
無ければ理由を付けて skip する。ポートが既に答えていればそれを使い、答えなければ
`manage.py runserver --noreload` を起こして終わりに止める (自分が起こした物だけ)。
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

E2E_DIR = Path(__file__).resolve().parent
REPO_ROOT = E2E_DIR.parents[1]
COMPANIES_DIR = REPO_ROOT / "examples" / "companies"
GATE = "SEIZOU_E2E"
GATE_SKIP_REASON = "2 社が立っていない (SEIZOU_E2E 未設定)"
DEFAULT_USER = "admin"
VIEWPORT = {"width": 1280, "height": 800}
START_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Company:
    """YAML から読んだ会社の値。パスワードは持たず、環境変数の名前だけを持つ。"""

    id: str
    name: str
    port: int
    site: Path
    base_url: str
    password_env: str

    @property
    def login_url(self) -> str:
        return f"{self.base_url}/login/"

    @property
    def session_cookie(self) -> str:
        return f"seizou_{self.id}_session"


def _sites_dir() -> Path:
    return Path(os.environ.get("SEIZOU_E2E_SITES_DIR") or "~/seizou-sites").expanduser()


def _load_company(yaml_name: str) -> Company:
    answers = yaml.safe_load((COMPANIES_DIR / yaml_name).read_text(encoding="utf-8"))
    company_id = str(answers["id"])
    port = int(answers["port"])
    return Company(
        id=company_id,
        name=str(answers["name"]),
        port=port,
        site=_sites_dir() / company_id,
        base_url=f"http://127.0.0.1:{port}",
        password_env=f"SEIZOU_E2E_PASSWORD_{company_id.upper()}",
    )


# --- 門 -----------------------------------------------------------------


def pytest_collection_modifyitems(config, items):
    """門が閉じていれば、このフォルダの試験だけを skip にする (ほかの試験には触らない)。"""
    if os.environ.get(GATE) == "1":
        return
    skip = pytest.mark.skip(reason=GATE_SKIP_REASON)
    for item in items:
        if E2E_DIR in Path(str(item.path)).parents:
            item.add_marker(skip)


# --- 会社とサーバー -------------------------------------------------------


@pytest.fixture(scope="session")
def companies() -> dict[str, Company]:
    loaded = (_load_company("a_machining.yaml"), _load_company("b_plating.yaml"))
    return {company.id: company for company in loaded}


def _is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _login_page_answers(company: Company) -> bool:
    try:
        with urllib.request.urlopen(company.login_url, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


@contextmanager
def _running(company: Company) -> Iterator[Company]:
    manage = company.site / "manage.py"
    if not manage.exists():
        pytest.skip(
            f"{company.site} に manage.py が無い。`uv run seizou new examples/companies/{company.id}.yaml` で作ってから回す"
        )
    if _is_listening(company.port):
        # 立っていればそのまま使う。自分が起こした物ではないので、終わりに止めない。
        yield company
        return

    # 会社の settings の名前は manage.py が自分で決める。渡さない物を子に残さないため、E2E の環境変数は外す。
    child_env = {key: value for key, value in os.environ.items() if not key.startswith("SEIZOU_E2E")}
    child_env.pop("DJANGO_SETTINGS_MODULE", None)
    command = [sys.executable, str(manage), "runserver", f"127.0.0.1:{company.port}", "--noreload"]
    with tempfile.TemporaryFile() as log:
        # 新しい process group に入れ、終わりにその group だけを止める (pkill を使わない)。
        process = subprocess.Popen(
            command, cwd=REPO_ROOT, env=child_env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            deadline = time.monotonic() + START_TIMEOUT_SECONDS
            while not _login_page_answers(company):
                if process.poll() is not None or time.monotonic() > deadline:
                    log.seek(0)
                    tail = log.read().decode("utf-8", errors="replace")[-1500:]
                    raise RuntimeError(f"{company.id} のログインの画面が {START_TIMEOUT_SECONDS} 秒以内に開かない:\n{tail}")
                time.sleep(0.3)
            yield company
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


@pytest.fixture(scope="session")
def company_a(companies) -> Iterator[Company]:
    with _running(companies["a_machining"]) as company:
        yield company


@pytest.fixture(scope="session")
def company_b(companies) -> Iterator[Company]:
    with _running(companies["b_plating"]) as company:
        yield company


# --- ブラウザ -------------------------------------------------------------


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """Chromium を session で 1 つ (headless)。pytest-playwright は依存に無いので自分で包む。"""
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def context(browser) -> Iterator[BrowserContext]:
    """試験ごとに 1 つの文脈 (cookie の入れ物)。2 社のページは同じ文脈で開く。"""
    created = browser.new_context(viewport=VIEWPORT)
    created.set_default_timeout(10_000)
    yield created
    created.close()


@pytest.fixture
def page_a(context, company_a) -> Page:
    return context.new_page()


@pytest.fixture
def page_b(context, company_b) -> Page:
    return context.new_page()


# --- 助けの関数 ------------------------------------------------------------


def login(page: Page, company: Company, user: str | None = None, password: str | None = None) -> None:
    """会社の `/login/` を開き、ID とパスワードを入れて送る。

    ID は引数か `SEIZOU_E2E_USER` (無ければ `admin`)。パスワードは引数か会社の環境変数。
    環境変数が無ければ skip する。
    """
    secret = password if password is not None else os.environ.get(company.password_env)
    if not secret:
        pytest.skip(f"環境変数 {company.password_env} が渡されていない (パスワードの値はどこにも書かない)")
    page.goto(company.login_url)
    page.fill("input[name=username]", user or os.environ.get("SEIZOU_E2E_USER") or DEFAULT_USER)
    page.fill("input[name=password]", secret)
    page.click("button[type=submit]")
    page.wait_for_load_state("load")


def is_logged_in(page: Page, company: Company) -> bool:
    """道が `/login/` でなく、画面に会社の名前が出ていれば真。"""
    if urlparse(page.url).path.rstrip("/") == "/login":
        return False
    return company.name in page.locator("body").inner_text()


def shot(page: Page, code: str, scene: str) -> Path:
    """`docs/e2e/<今日の日付 YYYYMMDD>/<code>_<scene>.png` に画面全体の絵を撮る。"""
    if not re.fullmatch(r"E-\d{2}", code):
        raise ValueError(f"code は E-01 の形: {code!r}")
    if not re.fullmatch(r"[a-z0-9-]+", scene):
        raise ValueError(f"scene は英小文字と数字とハイフンだけ: {scene!r}")
    folder = REPO_ROOT / "docs" / "e2e" / datetime.now().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{code}_{scene}.png"
    page.screenshot(path=str(path), full_page=True)
    return path
