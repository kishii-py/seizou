"""README・SECURITY・THIRD_PARTY_NOTICES の形を、作品の実物と照らし合わせて確かめる。

標準の library だけを使い、Django を読まない。
設定の既定は src/seizou/core/conf.py を ast で読み (conf.py は Django を import するため)、
同梱物の版は scripts/vendor.toml を tomllib で読む。
"""

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
]

# README の選べる設定の鍵 (01 の 9 節の 5 つ)
SETTING_KEYS = [
    "order_form",
    "partial_shipment",
    "over_shipment",
    "over_workorder",
    "order_number",
    "production_plan",
]

# THIRD_PARTY_NOTICES の表の名前 → scripts/vendor.toml の項目の名前の頭
VENDOR_NAMES = {
    "Tailwind CSS": "tailwindcss",
    "daisyUI": "daisyui",
    "htmx": "htmx",
    "Tom Select": "tom-select",
    "Noto Sans JP": "noto-sans-jp",
}


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _section(text, heading):
    """見出し (## の行) から次の ## の見出しの前までの行。見出しが無ければ空。"""
    lines = text.splitlines()
    start = None
    for number, line in enumerate(lines):
        if line.strip() == heading:
            start = number + 1
            break
    if start is None:
        return []
    body = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def _table_rows(lines):
    """表の行 (| で始まる行) を、前後の空白と ` を外したセルの並びにする。見出しと区切りの行も含む。"""
    rows = []
    for line in lines:
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def _conf_defaults():
    """src/seizou/core/conf.py の DEFAULTS の辞書を、import せずに読む。"""
    tree = ast.parse(_read("src/seizou/core/conf.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DEFAULTS" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("conf.py に DEFAULTS が無い")


def _as_doc_value(value):
    """既定の値を README の書き方にする (真偽は YAML の true / false)。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def test_readme_has_new_sections():
    headings = {line.strip() for line in _read("README.md").splitlines() if line.startswith("## ")}
    for heading in ["## 前提", "## 選べる設定", "## 取引先とつなぐ", "## 決まり", "## 会期後の道筋"]:
        assert heading in headings, f"README に見出し「{heading}」が無い"


def test_readme_settings_table_matches_defaults():
    rows = _table_rows(_section(_read("README.md"), "## 選べる設定"))
    assert rows, "README の選べる設定に表が無い"
    header = rows[0]
    assert "設定の鍵" in header and "既定" in header, "選べる設定の表の見出しに「設定の鍵」と「既定」が無い"
    key_column = header.index("設定の鍵")
    default_column = header.index("既定")
    table = {row[key_column]: row[default_column] for row in rows[2:]}
    defaults = _conf_defaults()
    for key in SETTING_KEYS:
        assert key in table, f"選べる設定の表に {key} が無い"
        assert table[key] == _as_doc_value(defaults[key]), (
            f"{key} の既定が conf.py と違う (README: {table[key]}、conf.py: {defaults[key]!r})"
        )


def test_readme_links_rules_md():
    assert re.search(r"\]\(RULES\.md\)", _read("README.md")), "README に RULES.md への link が無い"


def test_readme_after_period_is_five_lines_or_less():
    body = _section(_read("README.md"), "## 会期後の道筋")
    items = [line for line in body if line.startswith("- ")]
    assert items, "README の会期後の道筋に箇条が無い"
    assert len(items) <= 5, f"会期後の道筋の箇条が {len(items)} 行ある (5 行まで)"


def test_readme_uses_based_on_wording():
    assert "項目を元にした" in _read("README.md"), "README に「項目を元にした」が無い"


def test_third_party_versions_match_vendor_toml():
    vendor = tomllib.loads(_read("scripts/vendor.toml"))["item"]
    rows = _table_rows(_section(_read("THIRD_PARTY_NOTICES.md"), "## 同梱する物"))
    assert rows, "THIRD_PARTY_NOTICES の同梱する物に表が無い"
    header = rows[0]
    name_column = header.index("名前")
    version_column = header.index("版")
    table = {row[name_column]: row[version_column] for row in rows[2:]}
    for doc_name, prefix in VENDOR_NAMES.items():
        matched = [item for item in vendor if item["name"].startswith(prefix)]
        assert matched, f"vendor.toml に {prefix} の項目が無い"
        versions = {item["version"] for item in matched}
        assert len(versions) == 1, f"vendor.toml の {prefix} の項目の版がそろっていない: {sorted(versions)}"
        assert doc_name in table, f"THIRD_PARTY_NOTICES の表に {doc_name} が無い"
        assert table[doc_name] == matched[0]["version"], (
            f"{doc_name} の版が vendor.toml と違う (文書: {table[doc_name]}、vendor.toml: {matched[0]['version']})"
        )


def test_security_names_env_file_and_partner_secret():
    text = _read("SECURITY.md")
    assert ".env" in text, "SECURITY に .env が無い"
    assert "SEIZOU_PARTNER_" in text, "SECURITY に SEIZOU_PARTNER_ が無い"


# 鍵の値に見える字の並び: base64 の字が 32 字以上続き、英大文字・英小文字・数字が混ざる物。
# URL は外してから探す (URL の道の部分は base64 の字だけで長く続くことがあるため)。
SECRET_LIKE = re.compile(r"[A-Za-z0-9+/]{32,}={0,2}")
URL = re.compile(r"https?://\S+")


def _looks_secret(run):
    return (
        re.search(r"[A-Z]", run) is not None
        and re.search(r"[a-z]", run) is not None
        and re.search(r"[0-9]", run) is not None
    )


def test_no_secret_like_values_in_docs():
    for name in DOCS:
        text = URL.sub(" ", _read(name))
        found = [run for run in SECRET_LIKE.findall(text) if _looks_secret(run)]
        assert not found, f"{name} に鍵の値に見える字の並びがある: {[run[:8] + '…' for run in found]}"
