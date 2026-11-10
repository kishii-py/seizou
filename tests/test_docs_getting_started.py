"""docs/getting-started.md (入れて触る文書) が、今の画面と命令の動きに合っていることを字で確かめる。

見る物 (依頼書 N390。出所は 2026-11-10 の精査 Q1・Q5・Q6 と、係 a・b の報告):

- (a) 触り方の節に、B 社で出荷の前に入荷を記録する手 (「入荷」の字が 2 回以上と、左のメニューの組「製造部」) がある。
  B 社の答えは receiving_required: stop で、入荷の無い明細の出荷を止めるため (精査 Q1 の 1・Q3 の T13)。
- (b) uv の入れ方の版が、プラグインの立ち上げの手順 (plugin/skills/build/SKILL.md) の版と同じ。macOS と Linux の install.sh と、
  Windows の install.ps1 の 2 つがある (精査 Q1 の 7・Q6 の 3)。
- (c) 最初の ```yaml の塊 (自分の会社の答えの最小の例) の鍵が id・name・port の 3 つで、seizou.cli.load_answers が誤りにしない (精査 Q1 の 9)。
- (d) 答えの YAML の鍵の表の 1 列目の鍵が、どれも src/seizou/cli.py に "<鍵>" の字で在る (cli が読まない鍵を書かない)。
- (e) 「会期」の字が無い (大会の言い方を外す。精査 Q5 の 5 節の (b))。
- (f) 起動の 1 行の塊の後に、2 つ目の端末 (「新しい端末」) と止め方 (「Control」) の字がある (精査 Q1 の 6)。
- (g) 0.0.0.0 で立てる段落のどれにも、自動ログイン (SEIZOU_DEV_AUTO_LOGIN) を 0 にする字が同じ段落にある。
  自動ログインは接続元を見ないので、社内に開くと誰でも管理者として入るため (core/middleware.py の DevAutoLoginMiddleware)。

標準の library と yaml と seizou.cli だけを使い、Django を読まない。
"""

import re
from pathlib import Path

import yaml

from seizou import cli

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "getting-started.md"
SKILL = ROOT / "plugin" / "skills" / "build" / "SKILL.md"
CLI = ROOT / "src" / "seizou" / "cli.py"

#: 答えの YAML の鍵の表を置く見出し
ANSWERS_HEADING = "### 自分の会社の答えの YAML"
#: 箇条の 1 項目の頭 (「- 」「* 」「1. 」。字下げを含む)
ITEM_HEAD = re.compile(r"^\s*(?:[-*]|\d+\.)\s")
#: 自動ログインを 0 にする字 (`SEIZOU_DEV_AUTO_LOGIN` を 0 に / SEIZOU_DEV_AUTO_LOGIN=0)
AUTO_LOGIN_OFF = re.compile(r"SEIZOU_DEV_AUTO_LOGIN`?\s*(?:を\s*0\s*に|=\s*0)")


def _text():
    return DOC.read_text(encoding="utf-8")


def _section(text, heading):
    """見出しの行から、同じか上の段の次の見出しの前までの字。見出しが無ければ空。コードの塊の中の # の行は見出しにしない。"""
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if line.strip() == heading:
            body = []
            in_code = False
            for rest in lines[number + 1 :]:
                if rest.lstrip().startswith("```"):
                    in_code = not in_code
                elif not in_code and rest.startswith("#") and len(rest) - len(rest.lstrip("#")) <= level:
                    break
                body.append(rest)
            return "\n".join(body)
    return ""


def _paragraphs(text):
    """空行で区切った塊を、さらに箇条の 1 項目ずつに分けた並び (項目の続きの行は、その項目に入れる)。"""
    units = []
    for block in re.split(r"\n\s*\n", text):
        current = []
        for line in block.splitlines():
            if ITEM_HEAD.match(line) and current:
                units.append("\n".join(current))
                current = []
            current.append(line)
        if current:
            units.append("\n".join(current))
    return units


def test_b_company_records_receiving_before_shipping():
    body = _section(_text(), "## 触り方")
    assert body, "getting-started に「## 触り方」の節が無い"
    assert body.count("入荷") >= 2, "触り方に、B 社で出荷の前に入荷を記録する手 (「入荷」の字 2 回以上) が無い"
    assert "製造部" in body, "触り方に、入荷の入口の左のメニューの組「製造部」の字が無い"


def test_uv_install_lines_match_skill_version():
    match = re.search(r"astral\.sh/uv/([0-9][0-9A-Za-z.]*)/install\.sh", SKILL.read_text(encoding="utf-8"))
    assert match, "SKILL.md に uv の版つきの入れ方 (astral.sh/uv/<版>/install.sh) が無い"
    version = match.group(1)
    text = _text()
    for name in ("install.sh", "install.ps1"):
        line = f"astral.sh/uv/{version}/{name}"
        assert line in text, f"getting-started に {line} が無い (SKILL.md の版 {version} と同じ版で書く)"


def test_first_yaml_block_is_minimal_answers(tmp_path):
    match = re.search(r"^[ \t]*```yaml[ \t]*\n(.*?)^[ \t]*```", _text(), re.DOTALL | re.MULTILINE)
    assert match, "getting-started に ```yaml の塊 (自分の会社の答えの最小の例) が無い"
    block = "\n".join(line.strip() for line in match.group(1).splitlines())
    answers = yaml.safe_load(block)
    assert isinstance(answers, dict) and set(answers) == {"id", "name", "port"}, f"最初の yaml の塊の鍵が id・name・port の 3 つでない: {answers!r}"
    path = tmp_path / "answers.yaml"
    path.write_text(block, encoding="utf-8")
    assert cli.load_answers(path) == answers


def test_answers_key_table_names_only_keys_cli_reads():
    body = _section(_text(), ANSWERS_HEADING)
    assert body, f"getting-started に「{ANSWERS_HEADING}」の見出しが無い"
    rows = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    keys = []
    for row in rows[2:]:
        first = row.strip("|").split("|")[0].strip().strip("`")
        keys.append(first)
    assert keys, f"「{ANSWERS_HEADING}」に鍵の表 (見出しと区切りの後の行) が無い"
    source = CLI.read_text(encoding="utf-8")
    missing = [key for key in keys if f'"{key}"' not in source]
    assert not missing, f"鍵の表の鍵が src/seizou/cli.py に無い: {missing}"


def test_no_hackathon_period_wording():
    assert "会期" not in _text(), "getting-started に「会期」の字がある (大会の言い方を外す)"


def test_second_terminal_and_stop_after_start_line():
    text = _text()
    match = re.search(r"^[ \t]*```[^\n]*\n(?:(?!^[ \t]*```).)*?runserver 8001(?:(?!^[ \t]*```).)*?^[ \t]*```", text, re.DOTALL | re.MULTILINE)
    assert match, "getting-started に、起動の 1 行 (runserver 8001) の塊が無い"
    after = text[match.end() :]
    assert "新しい端末" in after, "起動の 1 行の塊の後に、B 社を起動する「新しい端末」の字が無い"
    assert "Control" in after, "起動の 1 行の塊の後に、止め方 (「Control」) の字が無い"


def test_lan_paragraphs_turn_auto_login_off():
    units = [unit for unit in _paragraphs(_text()) if "0.0.0.0" in unit]
    assert units, "getting-started に 0.0.0.0 の字が無い (社内のスマホから開く段が無い)"
    for unit in units:
        assert "SEIZOU_DEV_AUTO_LOGIN" in unit and AUTO_LOGIN_OFF.search(unit), (
            "0.0.0.0 の段落に、SEIZOU_DEV_AUTO_LOGIN を 0 にする字が無い: " + unit[:60]
        )
