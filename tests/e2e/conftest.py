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
   - `SEIZOU_E2E_PARTNER_SECRET`: 2 社の間の署名の鍵。fixture が起こす runserver に、相手の会社の id から作った名前で渡す
   パスワードと鍵の値は、どのファイルにも、絵の名前にも、ログにも書かない。
5. 期待の文は、仕様書の 6 番の 3 節の「期待」の列をそのまま、試験の docstring に写す。
6. 作り物のデータだけが映る前提で書く。実在の会社名・人名・品番を試験に書かない。

会社の置き場 (`<SITES_DIR>/<id>/manage.py`) は `seizou new` が作る。この土台は作らない。
無ければ理由を付けて skip する。ポートが既に答えていればそれを使い、答えなければ
`manage.py runserver --noreload` を起こして終わりに止める (自分が起こした物だけ)。
"""

from __future__ import annotations

import csv
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
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from playwright.sync_api import Browser, BrowserContext, Page, expect, sync_playwright

E2E_DIR = Path(__file__).resolve().parent
REPO_ROOT = E2E_DIR.parents[1]
COMPANIES_DIR = REPO_ROOT / "examples" / "companies"
GATE = "SEIZOU_E2E"
GATE_SKIP_REASON = "2 社が立っていない (SEIZOU_E2E 未設定)"
PARTNER_SECRET_ENV = "SEIZOU_E2E_PARTNER_SECRET"
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


def _partner_of(company: Company) -> Company:
    """2 社のうち、company でない方の会社 (鍵の置き場の名前を相手の id から作るため)。"""
    for yaml_name in ("a_machining.yaml", "b_plating.yaml"):
        other = _load_company(yaml_name)
        if other.id != company.id:
            return other
    raise RuntimeError(f"{company.id} の相手の会社が見つからない")


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
    # 2 社の間の鍵: 相手の会社の id から作った名前で、2 社の runserver に同じ値を置く。
    # 会社の .env は既にある環境変数を上書きしないので、ここで置いた値が勝つ。値はログにも例外にも出さない。
    shared_secret = os.environ.get(PARTNER_SECRET_ENV)
    if shared_secret:
        child_env[f"SEIZOU_PARTNER_{_partner_of(company).id.upper()}_SECRET"] = shared_secret
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


# --- 助けの関数 (画面の操作。試験の中で何度も使う手順) -------------------------------

SAVE_WAIT_MS = 20_000  # 保存の後の移動を待つ上限 (発注は保存の中で相手へ送るので長め)
SELECT_WAIT_MS = 15_000  # 選ぶ欄の候補を待つ上限


def seed_name(company: Company, kind: str, code: str) -> str:
    """種のファイルから、取引先コード (kind="partners") か自社品番 (kind="items") の名前を読む。読むだけ。"""
    path = COMPANIES_DIR / company.id / f"{kind}.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if code in (row.get("取引先コード"), row.get("自社品番")):
                return row.get("名前") or row["品名"]
    raise LookupError(f"{company.id} の種 {kind}.csv に {code} が無い")


def unique_mark(prefix: str) -> str:
    """回すごとに違う印 (`E2E-<prefix>-<月日時分秒>`。35 字以内)。会社の DB に残る行を、自分の物だけ引くために使う。"""
    mark = f"E2E-{prefix}-{datetime.now():%m%d%H%M%S}"
    if len(mark) > 35:
        raise ValueError(f"印が 35 字を超える: {len(mark)} 字")
    return mark


def has_partner_secret(page: Page, company: Company) -> bool:
    """つながりの一覧 (`/connect/connections/`) の「鍵の設定」の欄が、全部の行で「済み」なら真。

    ログインは呼ぶ側が済ませておく。値は読まない (画面は値を出さず、済み / まだ だけを出す)。
    """
    page.goto(f"{company.base_url}/connect/connections/")
    headers = [text.strip() for text in page.locator("#rows thead th").all_inner_texts()]
    if "鍵の設定" not in headers:
        return False
    index = headers.index("鍵の設定")
    states = [
        row.locator("td").nth(index).inner_text().strip() for row in page.locator("#rows tbody tr").all()
    ]
    return bool(states) and all(state == "済み" for state in states)


def choose(page: Page, name: str, text: str) -> None:
    """選ぶ欄 (Tom Select。元の `<select name=...>` は隠れる) の入れ口に text を打ち、text を含む候補を押す。

    欄に着くと空の検索 (q=) の候補を取りに行く。その返事の前に打つと、打った字の検索が送られない
    ことがあるので、まず空の検索の返事と候補の表示を待つ。打った後は約 250ms 後に q=text の返事が来るので、
    それを待ってから押す (打つ前の候補を押すことは無い)。text は候補の字に含まれる字 (品番・名前) にする。
    押した後、元の select に値が入ったことを見て終わる。
    """

    def asked(response, expected: str) -> bool:
        url = urlparse(response.url)
        if not url.path.endswith("/select/"):
            return False
        return parse_qs(url.query, keep_blank_values=True).get("q") == [expected]

    select = page.locator(f"select[name='{name}']")
    wrapper = select.locator("xpath=following-sibling::div[contains(@class,'ts-wrapper')][1]")
    options = wrapper.locator(".ts-dropdown .option")
    with page.expect_response(lambda response: asked(response, ""), timeout=SELECT_WAIT_MS):
        wrapper.locator(".ts-control").click()
    options.first.wait_for(state="visible", timeout=SELECT_WAIT_MS)
    with page.expect_response(lambda response: asked(response, text), timeout=SELECT_WAIT_MS):
        wrapper.locator(".ts-control input").press_sequentially(text)
    option = options.filter(has_text=text).first
    option.wait_for(state="visible", timeout=SELECT_WAIT_MS)
    option.click()
    expect(select).not_to_have_value("", timeout=SELECT_WAIT_MS)


def _go_if_not_there(page: Page, company: Company, path: str) -> None:
    if urlparse(page.url).path != path:
        page.goto(f"{company.base_url}{path}")


def _click_save(page: Page) -> None:
    # 「このまま保存」にも「保存」が入るので、名前の全部で選ぶ。
    page.get_by_role("button", name="保存", exact=True).click()


def new_order(
    page: Page, company: Company, *, customer: str, part_no: str, qty, unit_price, mark: str
) -> str:
    """受注の新規登録 (`/orders/new/`) で明細 1 行の受注を入れて「保存」し、受注番号を返す。

    customer は得意先の名前 (候補の字に含まれる字。種の名前は `seed_name` で読める)。
    mark は得意先の注文番号に入れる印 (`unique_mark`)。保存の後に受注の詳細へ移ったことを見る。
    """
    _go_if_not_there(page, company, "/orders/new/")
    choose(page, "customer", customer)
    page.fill("input[name=customer_order_no]", mark)
    page.fill("input[name=order_date]", date.today().isoformat())
    choose(page, "lines-0-item", part_no)
    page.fill("input[name=lines-0-qty]", str(qty))
    page.fill("input[name=lines-0-unit_price]", str(unit_price))
    _click_save(page)
    page.wait_for_url(re.compile(r"/orders/\d+/$"), timeout=SAVE_WAIT_MS)
    return page.locator("h1").inner_text().strip()


def send_purchase_order(
    page: Page, company: Company, *, partner_code: str, part_no: str, qty, mark: str, unit_price=100
) -> str:
    """発注の新規登録 (`/purchasing/orders/new/`) で明細 1 行の発注を入れて「保存」し、発注番号を返す。

    保存と同時に相手へ送る。取引先と部品は標準の `<select>` なので、種の名前の候補の値で `select_option` する。
    単価は画面で必須なので、決まった作り物の値 (unit_price) を入れる。mark は備考に入れる。
    """
    _go_if_not_there(page, company, "/purchasing/orders/new/")
    partner_label = seed_name(company, "partners", partner_code)
    partner_value = page.locator("select[name=partner] option").filter(has_text=partner_label).first
    page.select_option("select[name=partner]", value=partner_value.get_attribute("value"))
    page.fill("textarea[name=note]", mark)
    item_value = page.locator("select[name=lines-0-item] option").filter(has_text=part_no).first
    page.select_option("select[name=lines-0-item]", value=item_value.get_attribute("value"))
    page.fill("input[name=lines-0-qty]", str(qty))
    page.fill("input[name=lines-0-unit_price]", str(unit_price))
    _click_save(page)
    page.wait_for_url(re.compile(r"/purchasing/orders/\d+/$"), timeout=SAVE_WAIT_MS)
    return page.locator("h1").inner_text().strip()


def fill_shipment(page: Page, company: Company, *, order_no: str, qty) -> None:
    """出荷の新規登録 (`/shipping/new/`) に、受注番号で選んだ明細と数量を入れて「保存」を押す (結果は待たない)。

    `/shipping/new/` に居れば (止められて同じ画面が出ているとき) 選んである明細を残し、空のときだけ選び直す。
    止まる・知らせる・保存できるは会社の設定で決まるので、結果は呼ぶ側が見る。
    """
    _go_if_not_there(page, company, "/shipping/new/")
    line = page.locator("select[name=order_line]")
    if line.input_value() == "":
        choose(page, "order_line", order_no)
    page.fill("input[name=qty]", str(qty))
    _click_save(page)
