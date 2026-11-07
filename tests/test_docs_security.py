"""SECURITY.md の報告の窓口・AI に送るデータ・個人情報の扱いと、運用の手引きの鍵の入れ替えの節と、VPS の 19 の段の形を確かめる。

標準の library だけを使い、この試験の中では Django を使わない (名前を import する tests/test_docs_vps.py は、tests/test_wording.py を通して Django を読む)。
見方 (見出しで切る形・鍵の行の形・置き字の形・書かない言い回し) は、既にある文書の試験の名前を import して同じ見方で見る (字をここに写さない)。
訳: 報告先の空欄と、AI に送る物・鍵の入れ替え・2 社の分け方の書き落としを、文書を直した後に戻さないため
(エンタープライズ化の調べ 第一弾の候補 19・21。本人の決め 11-08 00:31・00:36)。変えてよい条件: 本人が決め直したとき。
"""

import re
from pathlib import Path

from tests.test_docs_phrases import BANNED_PHRASES
from tests.test_docs_vps import ENV_LINE, PLACEHOLDER, SECRET_NAME_PARTS, _sections

ROOT = Path(__file__).resolve().parents[1]
SECURITY = ROOT / "SECURITY.md"
OPERATIONS = ROOT / "docs" / "operations.md"
VPS = ROOT / "docs" / "vps.md"

REPORT_HEADING = "## 報告の仕方"
VERSIONS_HEADING = "## 対応する版"
AI_DATA_HEADING = "## AI に送るデータ"
PERSONAL_HEADING = "## 個人情報の扱い"
ROTATION_HEADING = "## 鍵を入れ替えるとき・漏れたとき・取引をやめるとき"
STEP19_HEADING = "## 19. "

# AI に送るデータの 5 つの口の名前と、節に要る語
AI_GATES = ["AI チャット", "注文書の読み取り", "見張り", "フィードバック", "MCP"]
AI_WORDS = ["保持", "学習", "米国", "privacy.claude.com"]

# メールアドレスの形 (報告の窓口は GitHub の非公開の報告だけで、メールの宛先は公開しない。本人の決め 11-08 00:31)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def _read(path):
    return path.read_text(encoding="utf-8")


def _section(path, heading):
    """heading で始まる見出し (## ) の塊。無ければ試験を落とす。"""
    found = [part for part in _sections(_read(path)) if part.startswith(heading)]
    assert len(found) == 1, f"{path.name} に見出し「{heading.strip()}」が {len(found)} 個ある (1 個のはず)"
    return found[0]


def test_security_report_channel_is_private_reporting():
    text = _read(SECURITY)
    assert "[空欄" not in text, "SECURITY.md に空欄が残っている"
    assert "private vulnerability reporting" in text, "SECURITY.md に報告の窓口 (private vulnerability reporting) が無い"
    assert "3 営業日" in text, "SECURITY.md に返事の目安 (3 営業日) が無い"
    emails = EMAIL.findall(_section(SECURITY, REPORT_HEADING))
    assert not emails, f"SECURITY.md の報告の仕方にメールアドレスの形の字がある: {emails}"


def test_security_supported_versions_table():
    assert "`0.1.x`" in _section(SECURITY, VERSIONS_HEADING), "SECURITY.md の対応する版の表に 0.1.x が無い"


def test_security_ai_data_section_names_all_gates():
    section = _section(SECURITY, AI_DATA_HEADING)
    missing = [word for word in [*AI_GATES, *AI_WORDS] if word not in section]
    assert not missing, f"SECURITY.md の「AI に送るデータ」に無い語: {missing}"


def test_security_personal_data_section_names_login_id():
    assert "ログイン ID" in _section(SECURITY, PERSONAL_HEADING), "SECURITY.md の「個人情報の扱い」にログイン ID が無い"


def test_operations_key_rotation_section():
    section = _section(OPERATIONS, ROTATION_HEADING)
    for word in ["SECRET_KEY_FALLBACKS", "seizou_secret"]:
        assert word in section, f"operations.md の鍵の入れ替えの節に {word} が無い"


def test_vps_step19_separates_companies_and_limits_receive():
    section = _section(VPS, STEP19_HEADING)
    for word in ["limit_req_zone", "location = /connect/receive/order/", "NoNewPrivileges=yes", "seizou-a_machining"]:
        assert word in section, f"vps.md の 19 の段に {word} が無い"


def test_security_and_operations_env_secret_lines_are_placeholders():
    picked = []
    for path in [SECURITY, OPERATIONS]:
        lines = [(name, value.strip()) for name, value in ENV_LINE.findall(_read(path))]
        secret_lines = [(name, value) for name, value in lines if any(part in name for part in SECRET_NAME_PARTS)]
        picked.extend(secret_lines)
        bad = [name for name, value in secret_lines if value and PLACEHOLDER.fullmatch(value) is None]
        assert not bad, f"{path.name} の鍵の行の値が置き字 (<値> の形) でない: {bad}"
    assert picked, "鍵の行の拾い方が operations.md の鍵の入れ替えの行を拾っていない"


def test_security_new_sections_have_no_banned_phrases():
    for heading in [REPORT_HEADING, VERSIONS_HEADING, AI_DATA_HEADING, PERSONAL_HEADING]:
        section = _section(SECURITY, heading)
        for phrase in BANNED_PHRASES:
            assert phrase not in section, f"SECURITY.md の「{heading[3:]}」に「{phrase}」がある"
