"""雛形の settings.py が .env の SECRET_KEY_FALLBACKS を読むか (依頼書 N387 の 1)。

出所は精査 Q4 (2026-11-10) のつまずく所 4。docs/operations.md の「鍵を入れ替えるとき」のとおりに前の値を
.env の SECRET_KEY_FALLBACKS に置いても、雛形にその行が無く、全員のログインが切れた。
(a) 雛形の式を ast で取り出し、作り物の environ で値を見る。(b) seizou new で置き場を 1 つ作り、子の process の
settings に .env の行が入るかを見る。値はどれも作り物の字。
"""

import ast
import string
from pathlib import Path
from types import SimpleNamespace

import pytest

import seizou.scaffold
from seizou import cli
from tests.test_cli_new import FIXTURE, manage, run_new

TEMPLATE = Path(seizou.scaffold.__file__).parent / "settings.py.tmpl"


def fallbacks_expression() -> ast.expr:
    """雛形の SECRET_KEY_FALLBACKS の代入の式。SECRET_KEY の代入のすぐ後にある事も見る。"""
    text = TEMPLATE.read_text(encoding="utf-8")
    tree = ast.parse(string.Template(text).safe_substitute(id="t", seizou_items=""))
    names = [
        node.targets[0].id
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        else None
        for node in tree.body
    ]
    index = names.index("SECRET_KEY")
    assert names[index + 1] == "SECRET_KEY_FALLBACKS"
    return tree.body[index + 1].value


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"SECRET_KEY_FALLBACKS": "作り物の前の値1,作り物の前の値2"}, ["作り物の前の値1", "作り物の前の値2"]),
        ({"SECRET_KEY_FALLBACKS": " 作り物の前の値1 , "}, ["作り物の前の値1"]),
        ({"SECRET_KEY_FALLBACKS": ""}, []),
        ({}, []),
    ],
)
def test_template_reads_fallbacks_after_secret_key(environ, expected):
    expression = ast.Expression(body=fallbacks_expression())
    code = compile(expression, str(TEMPLATE), "eval")
    # 内包の中から os が見えるよう、名前は globals に渡す。
    assert eval(code, {"os": SimpleNamespace(environ=environ)}) == expected


def test_new_site_reads_fallbacks_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))
    monkeypatch.delenv("SEIZOU_DB", raising=False)
    monkeypatch.delenv("SECRET_KEY_FALLBACKS", raising=False)
    code, out, err = run_new(["new", str(FIXTURE)])
    assert code == 0, err
    site = tmp_path / "sample_co"
    cli.append_secret_lines(site / ".env", ["SECRET_KEY_FALLBACKS=作り物の前の値"])

    result = manage(site, "shell", "-c", "from django.conf import settings; print(len(settings.SECRET_KEY_FALLBACKS))")

    assert result.returncode == 0, result.stderr
    # shell -c は命令の前に自動の import の行を出すので、最後の行だけを比べる。
    assert result.stdout.strip().splitlines()[-1] == "1"
