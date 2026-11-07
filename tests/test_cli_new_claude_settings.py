"""会社の置き場の .claude/settings.json (Claude Code に .env・DB・控えを読ませない deny) の試験 (決め 11-08 00:5x。仕組みの調べの候補 7)。

seizou new が置き場に書き、seizou ai-docs は無いときだけ、CLAUDE.md と AGENTS.md が既にあって止まる前に書く
(既にある settings.json は --force でも書き直さない)。置き場は seizou new で 1 度だけ作り (環境変数 SEIZOU_SITES_DIR。
答えは tests/fixtures/companies/sample.yaml の作り物)、書き換える試験は一時のフォルダに写してから打つ。
"""

from __future__ import annotations

import json
import shutil

import pytest

from seizou import cli
from tests.test_cli_new import FIXTURE, run_new

SETTINGS = (".claude", "settings.json")
DENY = ("Read(/.env)", "Read(/.env.*)", "Edit(/.env)", "Read(/db.sqlite3)", "Read(/backups/**)", "Bash(*seizou_secret*)")
EDITED = '{"permissions": {"deny": ["Read(/.env)"]}}\n'


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """試験用の答えで 1 度だけ作った置き場 (seizou new)。読むだけに使う。"""
    sites = tmp_path_factory.mktemp("sites")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SEIZOU_SITES_DIR", str(sites))
        code, _out, err = run_new(["new", str(FIXTURE)])
    assert code == 0, err
    return sites / "sample_co"


@pytest.fixture
def site(built, tmp_path):
    """作った置き場の写し (書き換える試験に使う)。"""
    return shutil.copytree(built, tmp_path / "copied_site")


def _settings(folder):
    return folder.joinpath(*SETTINGS)


def test_new_writes_deny_for_env_db_and_backups(built):
    data = json.loads(_settings(built).read_text(encoding="utf-8"))
    deny = data["permissions"]["deny"]
    assert list(deny) == list(DENY)
    # 道は置き場の直下を指す / の形。Write は Read の deny で止まるので書かない
    assert not [rule for rule in deny if rule.startswith("Write(") or "(./" in rule]


def test_ai_docs_writes_settings_before_stopping_on_existing_rule_docs(built, site):
    _settings(site).unlink()
    code, out, err = run_new(["ai-docs", str(site)])
    assert code == 2  # CLAUDE.md と AGENTS.md が既にあるので、その 2 つは書かずに止まる
    assert "既にあります" in err and len(err.strip().splitlines()) == 1
    assert f"書いた: {_settings(site)}" in out
    assert _settings(site).read_text(encoding="utf-8") == _settings(built).read_text(encoding="utf-8")


def test_ai_docs_keeps_existing_settings_even_with_force(site):
    _settings(site).write_text(EDITED, encoding="utf-8")
    code, out, err = run_new(["ai-docs", str(site), "--force"])
    assert code == 0, err
    assert "settings.json" not in out  # 既にあれば書かず、「書いた:」の行にも出さない
    assert _settings(site).read_text(encoding="utf-8") == EDITED


def test_write_claude_settings_keeps_existing_file(tmp_path):
    assert cli.write_claude_settings(tmp_path) is True
    _settings(tmp_path).write_text(EDITED, encoding="utf-8")
    assert cli.write_claude_settings(tmp_path) is False
    assert _settings(tmp_path).read_text(encoding="utf-8") == EDITED


def test_rule_docs_tell_ai_not_to_open_env(built):
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (built / name).read_text(encoding="utf-8")
        assert "AI は `.env` を開かない・中身を出さない" in text, name
    assert len((built / "AGENTS.md").read_text(encoding="utf-8").splitlines()) <= 12
