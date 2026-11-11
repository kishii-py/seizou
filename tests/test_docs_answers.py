"""docs/answers.md (答えの YAML の書き方の文書) が、今の命令 seizou new の読み方に合っていることを字で確かめる。

見る物 (依頼書 N427。出所は 2026-11-10 の精査 Q5 の 4 節の「足りない物」の 2 と、本体の決め 11-11 14:2x):

- (a) src/seizou/cli.py の REQUIRED_KEYS・COMPANY_KEYS・FIELD_TYPES・FIELD_FLAGS の字の全部と、load_answers が読む根の鍵
  (industry・recipe・company・logo・item_fields・settings・partner_base_urls) が、文書に `<字>` の形で在る
  (命令が読む鍵を書き漏らさない)。定数は cli.py を ast で読む。
- (b) 最初の ```yaml の塊 (いちばん小さい答え) の鍵が id・name・port の 3 つで、seizou.cli.load_answers が誤りにしない
  (tests/test_docs_getting_started.py の test_first_yaml_block_is_minimal_answers と同じ形)。

標準の library と yaml と seizou.cli だけを使い、Django を読まない。
"""

import ast
import re
from pathlib import Path

import yaml

from seizou import cli

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "answers.md"
CLI = ROOT / "src" / "seizou" / "cli.py"

#: 字の並びを文書に書かせる cli.py の定数
CLI_CONSTANTS = ("REQUIRED_KEYS", "COMPANY_KEYS", "FIELD_TYPES", "FIELD_FLAGS")
#: load_answers が読む根の鍵のうち、REQUIRED_KEYS の外の物
ROOT_KEYS = ("industry", "recipe", "company", "logo", "item_fields", "settings", "partner_base_urls")


def _text():
    return DOC.read_text(encoding="utf-8")


def _cli_constants():
    """cli.py の頭の CLI_CONSTANTS の値 (字の tuple) を、import せずに読む。"""
    tree = ast.parse(CLI.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in CLI_CONSTANTS:
                values[node.targets[0].id] = ast.literal_eval(node.value)
    missing = [name for name in CLI_CONSTANTS if name not in values]
    assert not missing, f"src/seizou/cli.py に定数が無い: {missing}"
    return values


def test_answers_doc_names_every_key_cli_reads():
    text = _text()
    words = [word for value in _cli_constants().values() for word in value]
    words += list(ROOT_KEYS)
    missing = [word for word in words if f"`{word}`" not in text]
    assert not missing, f"docs/answers.md に `<字>` の形で無い鍵と値: {missing}"


def test_answers_doc_first_yaml_block_loads(tmp_path):
    match = re.search(r"^[ \t]*```yaml[ \t]*\n(.*?)^[ \t]*```", _text(), re.DOTALL | re.MULTILINE)
    assert match, "docs/answers.md に ```yaml の塊 (いちばん小さい答え) が無い"
    block = "\n".join(line.strip() for line in match.group(1).splitlines())
    answers = yaml.safe_load(block)
    assert isinstance(answers, dict) and set(answers) == {"id", "name", "port"}, f"最初の yaml の塊の鍵が id・name・port の 3 つでない: {answers!r}"
    path = tmp_path / "answers.yaml"
    path.write_text(block, encoding="utf-8")
    assert cli.load_answers(path) == answers
