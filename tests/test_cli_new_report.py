"""seizou new の終わりの塊 (src/seizou/cli.py の _new_report) の並びを確かめる (依頼書 N426。精査 Q1 の 6 と 16。本体の決め 11-11 14:2x)。

見る物:

- (a) 管理者のパスワードを出す形で、行の並びが「できた物: 」→「管理者の ID: admin」→「管理者のパスワード: <値>」(行の終わりが値)
  →「パスワードはこの 1 度だけ出します。」→「起動:」で始まる行 → start_line(site, port) と同じ字だけの行
  →「http://127.0.0.1:<port>/login/」を含む行 →「点検:」→「練習するときは:」の順で、「次にすること: 」で始まる行が無い。
- (b) demo_accounts=True で demo_password=None のときだけ、作り物の 3 人 (genba・jimu・keiri) の在りか (docs/getting-started.md) を指す行が 1 つある。
  demo_password が random のときと値を渡したとき、demo_accounts=False のときは無い (合言葉の値は出力に出さない)。
- (c) --password-file の形 (admin_lines にファイルの行) でも、起動の行の並びは同じ。

_new_report を直に呼び、capsys で出力を受ける (seizou new は回さない)。admin_lines は _new_admin が作る形を写す。値と道は作り物。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seizou import cli

PORT = 8010
ADMIN_PASSWORD = "Sakumono-Admin01"
DEMO_LINE_WORDS = ("genba・jimu・keiri", "docs/getting-started.md")


def _report(capsys, site: Path, admin_lines: list[str], **options) -> list[str]:
    values = {"made_passwords": {}, "demo_password": None, "password_file": None, **options}
    cli._new_report(site, PORT, admin_lines, **values)
    return capsys.readouterr().out.splitlines()


def _shown_admin_lines() -> list[str]:
    return [f"管理者の ID: {cli.ADMIN_ID}", f"管理者のパスワード: {ADMIN_PASSWORD}", "パスワードはこの 1 度だけ出します。"]


def _index(lines: list[str], test) -> int:
    found = [number for number, line in enumerate(lines) if test(line)]
    assert len(found) == 1, f"当たる行が 1 つでない: {found}\n" + "\n".join(lines)
    return found[0]


def _assert_start_block(lines: list[str], site: Path) -> int:
    """「起動:」の行 → 起動の 1 行だけの行 → login の URL の行 → 点検 → 練習 の順を見て、「起動:」の行の番号を返す。"""
    start = _index(lines, lambda line: line.startswith("起動:"))
    assert lines[start + 1] == cli.start_line(site, PORT)
    assert f"http://127.0.0.1:{PORT}/login/" in lines[start + 2]
    check = _index(lines, lambda line: line.startswith("点検:"))
    practice = _index(lines, lambda line: line.startswith("練習するときは:"))
    assert start + 2 < check < practice
    assert not [line for line in lines if line.startswith("次にすること: ")]
    return start


def test_report_lines_are_in_user_order(capsys, tmp_path):
    # (a)
    site = tmp_path / "sites" / "sample_co"
    lines = _report(capsys, site, _shown_admin_lines())
    made = _index(lines, lambda line: line.startswith("できた物: "))
    assert lines[made + 1] == "管理者の ID: admin"
    assert lines[made + 2] == f"管理者のパスワード: {ADMIN_PASSWORD}"
    assert lines[made + 2].endswith(ADMIN_PASSWORD)
    assert lines[made + 3] == "パスワードはこの 1 度だけ出します。"
    start = _assert_start_block(lines, site)
    assert made + 3 < start
    assert sum(ADMIN_PASSWORD in line for line in lines) == 1


@pytest.mark.parametrize(
    ("demo_accounts", "demo_password", "shown"),
    [
        (True, None, True),
        (True, "random", False),
        (True, "sakumono-value", False),
        (False, None, False),
    ],
)
def test_demo_accounts_line_only_with_public_passwords(capsys, tmp_path, demo_accounts, demo_password, shown):
    # (b) 公開の合言葉のときだけ在りかを指す (乱数か渡した値のときは、作り物の利用者の行が既にある)
    site = tmp_path / "sites" / "a_machining"
    made = cli.demo_passwords(demo_password) if demo_accounts else {}
    lines = _report(capsys, site, _shown_admin_lines(), made_passwords=made, demo_password=demo_password, demo_accounts=demo_accounts)
    pointers = [line for line in lines if all(word in line for word in DEMO_LINE_WORDS)]
    assert len(pointers) == (1 if shown else 0), "\n".join(lines)
    _assert_start_block(lines, site)


def test_password_file_form_keeps_start_block(capsys, tmp_path):
    # (c) パスワードを画面に出さない形でも、起動の行の並びは同じ
    site = tmp_path / "sites" / "sample_co"
    target = tmp_path / "secrets" / "admin.txt"
    admin_lines = [f"管理者の ID: {cli.ADMIN_ID}", f"管理者のパスワードを書いたファイル: {target} (画面には出しません。持ち主だけが読めます)"]
    lines = _report(capsys, site, admin_lines, password_file=target)
    made = _index(lines, lambda line: line.startswith("できた物: "))
    assert lines[made + 1] == "管理者の ID: admin"
    assert not [line for line in lines if line.startswith("管理者のパスワード: ")]
    start = _assert_start_block(lines, site)
    assert made + 2 < start
