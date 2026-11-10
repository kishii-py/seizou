"""置き場の manage.py の命令が、端末の文字コードで出せない字で止まらない事 (seizou.console と core の app の読み込み。開発の記録 docs/nights/20261111/N399.md)。

置き場は命令 seizou new の試験の置き場 (tests/test_cli_new.py の built。作り物の答え) を使い回す。
Windows の管か file に向けた形は tests/test_cli_windows.py と同じく、PYTHONUTF8 を外し PYTHONIOENCODING を置いて模す。
雛形の守りの 1 行が無い前の形の manage.py (前の版で作った置き場) でも、core の app の読み込みの守りだけで止まらない事を見る。
改行は Windows では \\r\\n になるので、行に分けてから比べる。
"""

from __future__ import annotations

import io
import subprocess
import sys

import pytest

from seizou import console
from tests.test_cli_new import built  # noqa: F401 (試験の置き場の fixture)
from tests.test_cli_windows import _child_env_like_windows_pipe

#: 雛形に守りの 1 行を足す前の manage.py の中身 (前の版で作った置き場の形。雛形の 1 行に頼らない事を見る)
OLD_MANAGE = '''\
#!/usr/bin/env python
"""会社 sample_co の Django の管理の命令。seizou new が作った。"""

import os
import sys
from pathlib import Path


def main():
    base_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(base_dir))
    # 代入にする (setdefault にしない)。外から別の settings の名前が渡っても、この会社の settings を読む。
    os.environ["DJANGO_SETTINGS_MODULE"] = "settings"
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
'''
OLD_SCRIPT = "manage_old.py"
NEW_SCRIPT = "manage.py"

#: cp1252 にも cp932 にも無い字 (😀 と ✓)、cp932 に無い字 (—)、cp1252 に無い字 (日本語と ①・) を混ぜた 1 行。
#: 「〜」は cp932 で書いて読み戻すと「～」(全角のチルダ) に替わるので使わない (Python の cp932 の対応表の形)
SAMPLE = "良い — 😀 ✓ ①・"
SAMPLE_CP932 = "良い ? ? ? ①・"
SAMPLE_CP1252 = "?? — ? ? ??"


def lines(data: bytes, encoding: str) -> list[str]:
    return [line.rstrip() for line in data.decode(encoding).splitlines()]


def as_terminal(text: str, encoding: str) -> str:
    """text を encoding の端末に「?」の置き換えで書いたときの字 (出せない字だけが ? になる)。"""
    return text.encode(encoding, errors="replace").decode(encoding)


@pytest.fixture(scope="module")
def site(built):  # noqa: F811
    assert built["code"] == 0, built["err"]
    (built["site"] / OLD_SCRIPT).write_text(OLD_MANAGE, encoding="utf-8")
    return built["site"]


def run(site, script: str, args: list[str], encoding: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=site,
        env=_child_env_like_windows_pipe(encoding),
        capture_output=True,
        check=False,
        timeout=300,
    )


def assert_no_traceback(result: subprocess.CompletedProcess, encoding: str) -> None:
    err = result.stderr.decode(encoding, errors="replace")
    assert "Traceback" not in err, err
    assert "UnicodeEncodeError" not in err, err


@pytest.fixture(scope="module")
def doctor_utf8(site):
    result = run(site, NEW_SCRIPT, ["seizou_doctor"], "utf-8")
    assert_no_traceback(result, "utf-8")
    return result


# (a) 英語の Windows の管 (cp1252)。直す前は点検の最初の行 (「良い  …」) で UnicodeEncodeError の traceback になった


@pytest.mark.parametrize("script", [NEW_SCRIPT, OLD_SCRIPT])
def test_doctor_on_cp1252_finishes_and_marks_unencodable(site, doctor_utf8, script):
    result = run(site, script, ["seizou_doctor"], "cp1252")
    assert_no_traceback(result, "cp1252")
    # 終わりの番号は UTF-8 の端末と同じ (0 = 直す物が無い・1 = 直す物がある。点検の結果で決まり、文字コードで変わらない)
    assert result.returncode == doctor_utf8.returncode
    assert result.returncode in (0, 1)
    out = lines(result.stdout, "cp1252")
    # 行は 1 行も欠けず、出せない字だけが「?」になる (日本語の「良い」は「??」)
    assert out == [as_terminal(line, "cp1252") for line in lines(doctor_utf8.stdout, "utf-8")]
    assert [line for line in out if line.startswith("?? ")]


# (b) UTF-8 の端末と (c) 日本語の Windows の管 (cp932)。命令の中で書いた字が、どう出るかを見る


def test_utf8_output_is_unchanged(site):
    result = run(site, NEW_SCRIPT, ["shell", "--no-imports", "-c", f"print({SAMPLE!r})"], "utf-8")
    assert_no_traceback(result, "utf-8")
    assert result.returncode == 0
    assert lines(result.stdout, "utf-8") == [SAMPLE]


@pytest.mark.parametrize("script", [NEW_SCRIPT, OLD_SCRIPT])
def test_cp932_keeps_japanese_and_marks_only_missing(site, script):
    result = run(site, script, ["shell", "--no-imports", "-c", f"print({SAMPLE!r})"], "cp932")
    assert_no_traceback(result, "cp932")
    assert result.returncode == 0
    assert lines(result.stdout, "cp932") == [SAMPLE_CP932]


def test_log_line_on_cp1252_is_written(site):
    # runserver の記録と同じ行き先 (標準エラーの handler。seizou.core.logging_conf) に、日本語の記録が「?」で出る
    code = f"import logging; logging.getLogger('seizou.console_test').warning({SAMPLE!r})"
    result = run(site, NEW_SCRIPT, ["shell", "--no-imports", "-c", code], "cp1252")
    assert_no_traceback(result, "cp1252")
    assert result.returncode == 0
    err = result.stderr.decode("cp1252")
    assert "Logging error" not in err
    assert [line for line in err.splitlines() if line.rstrip().endswith(SAMPLE_CP1252)], err


# 守りの関数そのもの (seizou.console.replace_unencodable_output)


def stream(encoding: str) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, newline="")


def written(target: io.TextIOWrapper, text: str) -> bytes:
    target.write(text)
    target.flush()
    return target.buffer.getvalue()


def test_utf8_stream_keeps_every_char_and_encoding():
    target = stream("utf-8")
    console.replace_unencodable_output([target])
    assert target.encoding == "utf-8"
    assert written(target, SAMPLE) == SAMPLE.encode("utf-8")


def test_cp932_stream_keeps_encoding_and_japanese():
    target = stream("cp932")
    console.replace_unencodable_output([target])
    assert target.encoding == "cp932"
    assert written(target, SAMPLE).decode("cp932") == SAMPLE_CP932


def test_guard_twice_is_the_same(monkeypatch):
    once, twice = stream("cp1252"), stream("cp1252")
    console.replace_unencodable_output([once])
    # 既定 (引数なし) は標準出力と標準エラーを直す。2 回呼んでも形は同じ
    monkeypatch.setattr(sys, "stdout", twice)
    monkeypatch.setattr(sys, "stderr", stream("cp932"))
    console.replace_unencodable_output()
    console.replace_unencodable_output()
    assert (twice.encoding, twice.errors) == (once.encoding, once.errors) == ("cp1252", "replace")
    assert (sys.stderr.encoding, sys.stderr.errors) == ("cp932", "replace")
    assert written(twice, SAMPLE) == written(once, SAMPLE)


def test_guard_skips_streams_it_cannot_change():
    # reconfigure を持たない物 (None・io.StringIO) と閉じた stream は飛ばし、誤りを出さない (起動を止めない)
    closed = stream("cp1252")
    closed.close()
    console.replace_unencodable_output([None, io.StringIO(), closed])
