"""鍵の値を作る管理コマンド (seizou_secret) のテスト。

値はコマンドが作った物だけを使い、固定の鍵の文字列をファイルに書かない。
失敗の文に値が出ないよう、値を含む字の比べは真偽にしてから assert する。
"""

import io
import uuid

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from seizou.connect import signing

ENV_NAME = "SEIZOU_PARTNER_TESTCO_SECRET"
BODY = b'{"kind":"order","lines":[]}'


def _run(name):
    out, err = io.StringIO(), io.StringIO()
    call_command("seizou_secret", name, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _value_of(out):
    """標準出力の 1 行の = の後ろ (鍵の値)。"""
    return out.splitlines()[0].split("=", 1)[1]


def test_prints_one_line_with_name():
    out, err = _run(ENV_NAME)

    one_line = len(out.splitlines()) == 1
    starts_with_name = out.startswith(f"{ENV_NAME}=")
    assert one_line, "標準出力が 1 行でない"
    assert starts_with_name, "標準出力の行が <鍵の置き場の名前>= で始まらない"
    assert ".env" in err
    assert "貼らないでください" in err


def test_value_signs_and_verifies():
    out, _err = _run(ENV_NAME)
    value = _value_of(out)
    msg_id = str(uuid.uuid4())

    headers = signing.headers_for(value, msg_id, BODY)

    assert signing.verify(value, headers, BODY) == msg_id


def test_values_differ_each_time():
    first, _ = _run(ENV_NAME)
    second, _ = _run(ENV_NAME)

    differ = _value_of(first) != _value_of(second)
    assert differ, "2 回打った値が同じ"


def test_bad_name_is_refused():
    for name in ("seizou_partner_x_secret", "1ABC", "SEIZOU_PARTNER_X_SECRET=abc"):
        out, err = io.StringIO(), io.StringIO()
        with pytest.raises(CommandError) as excinfo:
            call_command("seizou_secret", name, stdout=out, stderr=err)
        empty = out.getvalue() == ""
        assert empty, f"{name[:8]}… で標準出力に字が出た"
        assert "abc" not in str(excinfo.value)
        assert "鍵の置き場の名前" in str(excinfo.value)


def test_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = sorted(tmp_path.iterdir())

    _run(ENV_NAME)

    assert sorted(tmp_path.iterdir()) == before
    assert not (tmp_path / ".env").exists()
