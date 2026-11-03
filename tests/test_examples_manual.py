"""2 社の種の画面ごとの説明書の断片 (examples/companies/<id>/manual/) と、seizou new --demo が置き場に写す口 (cli.copy_manual)。

断片の名前と置き場の約束は seizou.core.manual (manual/<URL の名前の : を _ に替えた名前>.html と前書きの index.html)。
断片の形は、素の HTML の <section> 1 つに、画面の名前の <h2>・何の画面かの <p>・使う人の 4 行・できること・枠の見出し・注意を並べた物。
小窓と使い方のページの試験は、settings.BASE_DIR を一時の置き場に替えてから写す (fixture は tests/test_core_manual_fragment.py の形を写した物。既にある試験は import しない)。
"""

import re
from html.parser import HTMLParser
from importlib import import_module
from pathlib import Path

import pytest
import yaml
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou import cli
from seizou.core import manual as core_manual
from seizou.core.manual import fragment_name, fragment_part
from seizou.core.screens import USECASE_KEYS, all_screens

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_DIR = ROOT / "examples" / "companies"
COMPANY_IDS = ("a_machining", "b_plating")
INTRO = core_manual.INTRO_NAME
INTRO_TITLE = "はじめに"

# 1 段目の画面 (流れの主役)。2 社とも断片がある
FIRST_TIER = (
    "core_home",
    "core_settings",
    "masters_item_list",
    "masters_item_detail",
    "orders_order_list",
    "orders_order_create",
    "orders_order_detail",
    "orders_orderline_list",
    "connect_inbox_list",
    "connect_inbox_detail",
    "connect_inbox_accept",
    "connect_connection_list",
    "shipping_shipment_list",
    "shipping_shipment_create",
    "shipping_shipment_detail",
    "shipping_deliverynote_list",
    "shipping_deliverynote_create",
    "shipping_deliverynote_detail",
    "purchasing_purchaseorder_list",
    "purchasing_purchaseorder_create",
    "purchasing_purchaseorder_detail",
    "purchasing_receipt_list",
    "purchasing_receipt_create",
    "workorders_workorder_list",
    "workorders_workorder_create",
    "workorders_workorder_detail",
    "workorders_plan_index",
    "billing_invoice_list",
    "billing_invoice_create",
    "billing_invoice_detail",
    "billing_payment_create",
    "stock_stock_list",
    "feedback_feedback_list",
    "feedback_feedback_create",
)

# 2 社で字が違ってよい断片 (受注より多い出荷の設定と、部品マスターの会社ごとの項目で画面の動きと字が変わる物)
OVER_SHIPMENT_FRAGMENTS = ("shipping_shipment_create", "shipping_shipment_update", "orders_order_update")
ITEM_FIELD_FRAGMENTS = ("masters_item_list", "masters_item_create", "masters_item_update", "masters_item_detail")
DIFFERENT = {*OVER_SHIPMENT_FRAGMENTS, *ITEM_FIELD_FRAGMENTS, "masters_item_import", "masters_item_modal"}

# 枠の「?」が読む <h3> の id (ダッシュボードの 4 つの節と、詳細の画面の基本情報と関係する物のタブ)
HOME_FRAMES = ("shipping", "production", "recent-orders", "due-alerts")
FRAME_IDS = {*HOME_FRAMES, "basic", "related"}

# 使い方のページと小窓が形を付ける要素
ALLOWED_TAGS = {"section", "h2", "h3", "p", "ul", "ol", "li", "table", "tr", "th", "td"}

# 1 段目で必ず注意に書く決まり
RULE_OF = {
    "orders_order_create": "R02",
    "shipping_shipment_create": "R01",
    "shipping_deliverynote_create": "R08",
    "billing_invoice_create": "R09",
    "billing_payment_create": "R21",
    "connect_inbox_accept": "R10",
    "workorders_workorder_create": "R06",
}

# ポカよけの値ごとの動きの字
OVER_SHIPMENT_TEXT = {
    "stop": "赤の知らせが出て保存されません。",
    "warn": "黄の知らせと「このまま保存」のボタンが出ます。",
}

# 断片に書かない物 (生の字で見る物と、タグを除いた字で見る物)
RAW_BANNED = ("class=", "<script", "<style", "{{", "{%", "<img", "style=", "href=", "src=", "<!--")
TEXT_BANNED = ("self.", "call_service", "=")
# 見本のデータの番号 (受注番号・品番の形と、取引先コードの形)
SAMPLE_NUMBER = re.compile(r"[A-Z]{2}-[0-9]|(?<![A-Z0-9])[CS][0-9]{3}(?![0-9])")
RULE_NUMBER = re.compile(r"(?<![A-Za-z0-9])R[0-9]{2}(?![0-9])")
QUOTED = re.compile(r"「([^「」]+)」")
RULE_SECTIONS = ("## 中心の決まり", "## 同梱のポカよけ")

HTMX = {"HTTP_HX_REQUEST": "true"}
MANUAL_MISSING = "この画面の説明はまだありません。"


class FragmentParser(HTMLParser):
    """断片のタグと属性と、タグを除いた字 (と <h2>・<h3> の字) を集める。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []  # (開くか閉じるか, タグ, 属性)
        self.texts = []
        self.h2 = []
        self.comments = 0
        self._open = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(("start", tag, attrs))
        self._open.append(tag)
        if tag == "h2":
            self.h2.append("")

    def handle_startendtag(self, tag, attrs):
        self.tags.append(("start", tag, attrs))

    def handle_endtag(self, tag):
        self.tags.append(("end", tag, []))
        if self._open and self._open[-1] == tag:
            self._open.pop()

    def handle_data(self, data):
        self.texts.append(data)
        if self._open and self._open[-1] == "h2":
            self.h2[-1] += data

    def handle_comment(self, data):
        self.comments += 1


def parse(raw: str) -> FragmentParser:
    parser = FragmentParser()
    parser.feed(raw)
    parser.close()
    return parser


def plain(raw: str) -> str:
    """タグを除いた字 (空白を 1 つにそろえる)。"""
    return " ".join("".join(parse(raw).texts).split())


def manual_folder(company_id: str) -> Path:
    return COMPANIES_DIR / company_id / "manual"


def fragments(company_id: str) -> dict[str, str]:
    """種の manual/ の断片の名前と字 (前書きを含む)。"""
    folder = manual_folder(company_id)
    if not folder.is_dir():
        return {}
    return {path.stem: path.read_text(encoding="utf-8") for path in sorted(folder.glob("*.html"))}


def screens_by_name() -> dict:
    return {fragment_name(screen.url_name): screen for screen in all_screens()}


def answers(company_id: str) -> dict:
    return yaml.safe_load((COMPANIES_DIR / f"{company_id}.yaml").read_text(encoding="utf-8"))


def item_labels(company_id: str) -> list[str]:
    return [field["label"] for field in answers(company_id).get("item_fields") or []]


def other_company(company_id: str) -> str:
    return next(other for other in COMPANY_IDS if other != company_id)


def view_tabs(screen) -> tuple:
    """画面の view の class の tabs (無ければ空)。"""
    module = import_module(f"seizou.{screen.app}.views")
    return tuple(getattr(getattr(module, screen.view, None), "tabs", ()) or ())


def rule_texts() -> dict[str, str]:
    """RULES.md の「中心の決まり」と「同梱のポカよけ」の表の番号と 2 列目の文。"""
    rules = {}
    section = ""
    for line in (ROOT / "RULES.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section = line
            continue
        if not section.startswith(RULE_SECTIONS):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and re.fullmatch(r"R[0-9]{2}", cells[0]):
            rules[cells[0]] = " ".join(cells[1].split())
    return rules


def source_text() -> str:
    """「」の字を探す元 (部品の .py・.html・.js と RULES.md と 2 社の答えの YAML。種の manual/ は入れない)。"""
    parts = []
    for path in sorted((ROOT / "src" / "seizou").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".html", ".js"}:
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    parts.append((ROOT / "RULES.md").read_text(encoding="utf-8"))
    for path in sorted(COMPANIES_DIR.glob("*.yaml")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


# ---------------------------------------------------------------- 写す口 (cli.copy_manual)


def test_copy_manual_copies_html_and_keeps_existing(tmp_path):
    seeds = tmp_path / "seeds"
    site = tmp_path / "site"
    (seeds / "manual" / "images").mkdir(parents=True)
    (seeds / "manual" / "orders_order_list.html").write_text("<section><h2>受注</h2></section>", encoding="utf-8")
    (seeds / "manual" / "index.html").write_text("<section><h2>はじめに</h2></section>", encoding="utf-8")
    (seeds / "manual" / "memo.txt").write_text("写さない", encoding="utf-8")
    (seeds / "manual" / "images" / "orders_order_list.png").write_bytes(b"png")
    (site / "manual").mkdir(parents=True)
    (site / "manual" / "index.html").write_text("<section><h2>自社の前書き</h2></section>", encoding="utf-8")

    assert cli.copy_manual(seeds, site) == 1

    assert (site / "manual" / "orders_order_list.html").read_text(encoding="utf-8") == "<section><h2>受注</h2></section>"
    assert (site / "manual" / "index.html").read_text(encoding="utf-8") == "<section><h2>自社の前書き</h2></section>"
    assert not (site / "manual" / "memo.txt").exists()
    assert not (site / "manual" / "images").exists()
    # 2 回目は全部が既にあるので 0
    assert cli.copy_manual(seeds, site) == 0


def test_copy_manual_without_folder_is_zero(tmp_path):
    seeds = tmp_path / "seeds"
    site = tmp_path / "site"
    seeds.mkdir()
    site.mkdir()

    assert cli.copy_manual(seeds, site) == 0
    assert not (site / "manual").exists()
    assert list(site.iterdir()) == []


# ---------------------------------------------------------------- 断片の名前と組


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_fragment_names_are_screens(company_id):
    folder = manual_folder(company_id)
    assert folder.is_dir(), f"{folder} が無い"
    others = [path.name for path in folder.iterdir() if not (path.is_file() and path.suffix == ".html")]
    assert others == [], f"manual/ に .html でない物がある (絵と、ほかのファイルは置かない): {others}"
    names = set(fragments(company_id)) - {INTRO}
    unknown = sorted(names - set(screens_by_name()))
    assert unknown == [], f"画面の目録に無い断片がある: {unknown}"


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_first_tier_fragments_exist(company_id):
    names = set(fragments(company_id))
    missing = [name for name in (*FIRST_TIER, INTRO) if name not in names]
    assert missing == [], f"1 段目の断片が無い: {missing}"


def test_two_companies_have_same_names_and_text():
    a_folder, b_folder = (manual_folder(company_id) for company_id in COMPANY_IDS)
    a_names = sorted(path.name for path in a_folder.glob("*.html"))
    b_names = sorted(path.name for path in b_folder.glob("*.html"))
    assert a_names == b_names
    differ = [
        name for name in a_names if Path(name).stem not in DIFFERENT and (a_folder / name).read_bytes() != (b_folder / name).read_bytes()
    ]
    assert differ == [], f"2 社で字が違う (違ってよいのは設定で動きが変わる断片だけ): {differ}"


# ---------------------------------------------------------------- 断片の形と字


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_fragment_shape(company_id):
    screens = screens_by_name()
    company_names = [str(answers(other)["name"]) for other in COMPANY_IDS]
    problems = []
    for name, raw in fragments(company_id).items():
        where = f"{company_id}/{name}"
        body = raw.strip()
        if not (body.startswith("<section>") and body.endswith("</section>")):
            problems.append(f"{where}: <section> で始まり </section> で終わらない")
        parsed = parse(raw)
        if parsed.comments:
            problems.append(f"{where}: コメントがある")
        for kind, tag, attrs in parsed.tags:
            if tag not in ALLOWED_TAGS:
                problems.append(f"{where}: 使えない要素 <{tag}>")
            if kind == "start" and attrs and not (tag == "h3" and [key for key, _ in attrs] == ["id"]):
                problems.append(f"{where}: <{tag}> に属性 {attrs} (属性は <h3> の id だけ)")
        title = INTRO_TITLE if name == INTRO else screens[name].name if name in screens else None
        if parsed.h2 != [title]:
            problems.append(f"{where}: <h2> が {parsed.h2} (目録の画面の名前 {title} の 1 つだけ)")
        text = "".join(parsed.texts)
        size = len(re.sub(r"\s", "", text))
        if not 120 <= size <= 900:
            problems.append(f"{where}: タグを除いた字が {size} 字 (120〜900 字)")
        problems += [f"{where}: {word} を書かない" for word in RAW_BANNED if word in raw]
        problems += [f"{where}: {word} を書かない (タグの外)" for word in TEXT_BANNED if word in text]
        problems += [f"{where}: 会社の名前 {company} を書かない" for company in company_names if company in raw]
        if SAMPLE_NUMBER.search(text):
            problems.append(f"{where}: 見本のデータの番号 {SAMPLE_NUMBER.search(text).group(0)} を書かない")
        if name == INTRO:
            continue
        # 使う人の 4 行 (目録の usecase を / で分けた値。作る人への注は写さないので、目録の値の頭と同じならよい)
        values = dict(part.split(": ", 1) for part in screens[name].usecase.split(" / ") if ": " in part)
        position = -1
        for key in USECASE_KEYS:
            match = re.search(rf"<li>{key}: (.*?)</li>", raw, re.S)
            if match is None or match.start() < position:
                problems.append(f"{where}: <li>{key}: の行が無いか、並びが違う")
                continue
            position = match.start()
            written = re.sub(r"\s", "", match.group(1))
            if not re.sub(r"\s", "", values.get(key, "")).startswith(written) or not written:
                problems.append(f"{where}: {key} の値が目録の usecase と違う")
        can = re.search(r"<h3>できること</h3>\s*<ul>(.*?)</ul>", raw, re.S)
        count = len(re.findall(r"<li>", can.group(1))) if can else 0
        if not 3 <= count <= 6:
            problems.append(f"{where}: できることの <li> が {count} (3〜6)")
        # 注意は当たる決まりのある画面だけ。1〜2 行で、どの行も決まりの番号で始まる
        note = re.search(r"<h3>注意</h3>\s*<ul>(.*?)</ul>", raw, re.S)
        if "<h3>注意</h3>" in raw:
            lines = re.findall(r"<li>(.*?)</li>", note.group(1), re.S) if note else []
            if not 1 <= len(lines) <= 2 or not all(re.match(r"R[0-9]{2} ", line) for line in lines):
                problems.append(f"{where}: 注意は決まりの番号で始まる 1〜2 行")
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_frame_ids(company_id):
    screens = screens_by_name()
    problems = []
    for name, raw in fragments(company_id).items():
        where = f"{company_id}/{name}"
        ids = re.findall(r'<h3 id="([^"]*)">', raw)
        problems += [f"{where}: 枠の id {frame} は無い枠" for frame in ids if frame not in FRAME_IDS]
        if name == "core_home":
            missing = [frame for frame in HOME_FRAMES if frame not in ids]
            problems += [f"{where}: ダッシュボードの節の枠 {missing} が無い"] if missing else []
        elif set(ids) & set(HOME_FRAMES):
            problems.append(f"{where}: ダッシュボードの節の枠はダッシュボードだけ")
        if name in FIRST_TIER and name.endswith("_detail") and "basic" not in ids:
            problems.append(f"{where}: 詳細の画面に基本情報の枠 (basic) が無い")
        if "basic" in ids and not name.endswith("_detail"):
            problems.append(f"{where}: 基本情報の枠は詳細の画面だけ")
        if "related" in ids and not (name.endswith("_detail") and name in screens and view_tabs(screens[name])):
            problems.append(f"{where}: 関係する物の枠は、タブのある詳細の画面だけ")
        if name.endswith("_detail") and "basic" in ids and name in screens and view_tabs(screens[name]) and "related" not in ids:
            problems.append(f"{where}: タブのある詳細の画面に関係する物の枠 (related) が無い")
        for frame in ids:
            piece = fragment_part(raw, frame) or ""
            size = len(re.sub(r"\s", "", "".join(parse(piece).texts)))
            if size < 20:
                problems.append(f"{where}: 枠 {frame} の字が {size} 字 (20 字以上)")
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_rules_are_quoted_from_rules_md(company_id):
    rules = rule_texts()
    assert {"R01", "R02", "R09", "R21"} <= set(rules), "RULES.md の表を読めない"
    assert not {"R13", "R20"} & set(rules), "書き方の決まりの表まで読んでいる"
    problems = []
    found = {}
    for name, raw in fragments(company_id).items():
        text = plain(raw)
        numbers = set(RULE_NUMBER.findall(text))
        found[name] = numbers
        for number in sorted(numbers):
            if number not in rules:
                problems.append(f"{company_id}/{name}: {number} は中心の決まりとポカよけの表に無い (書き方の決まりは書かない)")
            elif rules[number] not in text:
                problems.append(f"{company_id}/{name}: {number} の文が RULES.md のままでない ({rules[number]})")
    for name, number in RULE_OF.items():
        if number not in found.get(name, set()):
            problems.append(f"{company_id}/{name}: 注意に {number} が無い")
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_quoted_words_exist_in_source(company_id):
    source = source_text()
    problems = []
    for name, raw in fragments(company_id).items():
        for word in QUOTED.findall(plain(raw)):
            if word not in source:
                problems.append(f"{company_id}/{name}: 「{word}」が部品のソースと RULES.md と答えの YAML に無い")
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_settings_change_only_their_fragments(company_id):
    texts = {name: plain(raw) for name, raw in fragments(company_id).items()}
    value = answers(company_id)["settings"]["over_shipment"]
    problems = []
    for name in OVER_SHIPMENT_FRAGMENTS:
        if name not in texts:
            continue
        if OVER_SHIPMENT_TEXT[value] not in texts[name]:
            problems.append(f"{company_id}/{name}: 受注より多い出荷の動き ({value}) の字が無い")
        problems += [
            f"{company_id}/{name}: ほかの値 ({other}) の字がある" for other, sentence in OVER_SHIPMENT_TEXT.items() if other != value and sentence in texts[name]
        ]
    own, others = item_labels(company_id), item_labels(other_company(company_id))
    for name in ITEM_FIELD_FRAGMENTS:
        if name in texts:
            problems += [f"{company_id}/{name}: 自社の項目 {label} が無い" for label in own if label not in texts[name]]
    for name, text in texts.items():
        problems += [f"{company_id}/{name}: 相手の会社の項目 {label} がある" for label in others if label in text]
    assert problems == [], "\n".join(problems)


# ---------------------------------------------------------------- 写した後の小窓と使い方のページ


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def site(settings, tmp_path):
    """会社の置き場 (一時の置き場)。manual/ はまだ作らない。"""
    settings.BASE_DIR = tmp_path
    return tmp_path


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_modal_shows_fragment_after_copy(company_id, client_logged_in, site):
    copied = cli.copy_manual(COMPANIES_DIR / company_id, site)
    assert copied == len(fragments(company_id))
    for name in FIRST_TIER:
        response = client_logged_in.get(reverse("core:manual_fragment", args=[name]), **HTMX)
        assert response.status_code == 200, name
        html = response.content.decode()
        assert core_manual.MISSING_SENTENCE not in html, name
        assert "使う人:" in html, name
    html = client_logged_in.get(reverse("core:manual_fragment", args=["orders_order_detail"]), {"part": "basic"}, **HTMX).content.decode()
    assert "基本情報" in html
    assert "使う人:" not in html
    assert "<h3>注意</h3>" not in html


@pytest.mark.parametrize("company_id", COMPANY_IDS)
def test_manual_page_lists_only_missing_screens(company_id, client_logged_in, site):
    cli.copy_manual(COMPANIES_DIR / company_id, site)
    screens = all_screens()
    written = set(fragments(company_id)) & {fragment_name(screen.url_name) for screen in screens}
    html = client_logged_in.get(reverse("core:manual")).content.decode()
    assert html.count(MANUAL_MISSING) == len(screens) - len(written)
    assert f"<h2>{INTRO_TITLE}</h2>" in html
