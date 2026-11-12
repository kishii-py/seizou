"""入れ方の確かめ (scripts/install_smoke.py) が、seizou new の新しい終わりの塊の形でもパスワードを記録に出さないこと (依頼書 N426)。

見る物:

- (h) _new_report の新しい形の出力 (管理者のパスワードと、--demo-password random の 3 人のパスワードの行を含む字) を tail() に渡すと、
  どの値も結果に無い。
- (i) 同じ出力の日本語を全部「?」に替えた字 (英語の Windows の端末の形。cp1252 で表せない字を ? にする) を渡しても、どの値も結果に無い。
- (j) 起動の 1 行 (runserver 18001 を含む行) は伏せずに残る。

出力は cli._new_report を capsys の下で呼んで作る (手で行を書かない。出力の形が変わったら、この試験も同じ形を見る)。
値は試験の中で作る 16 字の作り物の字。runserver は起こさない。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from seizou import cli

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install_smoke.py"
PORT = 18001
ADMIN_PASSWORD = "Sakumono-Admin01"
DEMO_PASSWORDS = {"genba": "Sakumono_Genba01", "jimu": "Sakumono-Jimu001", "keiri": "Sakumono_Keiri01"}


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("install_smoke_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["install_smoke_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def report(capsys, tmp_path):
    """管理者のパスワードと、作り物の 3 人の乱数のパスワードを画面に出す形の終わりの塊。"""
    site = tmp_path / "sites" / "a_machining"
    admin_lines = [f"管理者の ID: {cli.ADMIN_ID}", f"管理者のパスワード: {ADMIN_PASSWORD}", "パスワードはこの 1 度だけ出します。"]
    cli._new_report(site, PORT, admin_lines, dict(DEMO_PASSWORDS), "random", None, True)
    text = capsys.readouterr().out
    values = [ADMIN_PASSWORD, *DEMO_PASSWORDS.values()]
    assert all(len(value) == 16 and value in text for value in values)
    return {"text": text, "values": values, "start": cli.start_line(site, PORT)}


def _like_english_windows(text: str) -> str:
    """英語の Windows の端末 (cp1252) で表せない字を「?」にした字 (seizou.console と同じ置き換え)。"""
    return text.encode("cp1252", errors="replace").decode("cp1252")


def test_tail_hides_passwords(smoke, report):
    # (h)
    shown = smoke.tail(report["text"])
    leaked = [value for value in report["values"] if value in shown]
    assert not leaked, shown


def test_tail_hides_passwords_when_japanese_is_question_marks(smoke, report):
    # (i)
    text = _like_english_windows(report["text"])
    assert "?" in text and "管理者" not in text
    assert all(value in text for value in report["values"])
    shown = smoke.tail(text)
    leaked = [value for value in report["values"] if value in shown]
    assert not leaked, shown


def test_tail_keeps_start_line(smoke, report):
    # (j)
    for text, start in ((report["text"], report["start"]), (_like_english_windows(report["text"]), _like_english_windows(report["start"]))):
        lines = [line for line in smoke.tail(text).splitlines() if f"runserver {PORT}" in line]
        assert lines == [start]
