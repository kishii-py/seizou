"""置き場の一覧 (src/seizou/site_registry.py) と、seizou new と seizou ai-docs の登録の試験 (決め 11-08 01:0x。Claude Security の走査の候補 13)。

一覧はプラグインの MCP の起こし役 (plugin/mcp/start.py) が読む。一覧の置き場 SEIZOU_HOME は試験ごとの一時のフォルダ
(tests/conftest.py の seizou_home より狭く、この試験の中で monkeypatch で決める)。置き場は seizou new で 1 度だけ作り
(環境変数 SEIZOU_SITES_DIR。答えは tests/fixtures/companies/sample.yaml の作り物)、書き換える試験は一時のフォルダに写してから打つ。
"""

from __future__ import annotations

import json
import shutil
import sys

import pytest

from seizou import cli, site_registry
from tests.test_cli_new import FIXTURE, run_new


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """試験用の答えで 1 度だけ作った置き場 (seizou new) と、そのときの一覧のファイル。"""
    sites = tmp_path_factory.mktemp("sites")
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SEIZOU_SITES_DIR", str(sites))
        mp.setenv("SEIZOU_HOME", str(home))
        code, out, err = run_new(["new", str(FIXTURE)])
    assert code == 0, err
    return {"site": sites / "sample_co", "sites_json": home / "sites.json", "out": out}


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    monkeypatch.setenv("SEIZOU_HOME", str(folder))
    return folder


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# 一覧の読み書き


def test_register_writes_resolved_site_and_python(tmp_path, home):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()

    path = site_registry.register_site(first, "/作り物/a/python")
    site_registry.register_site(second / ".." / "b", "/作り物/b/python")  # 鍵は resolve した道
    site_registry.register_site(first, "/作り物/a2/python")  # 同じ置き場は python を書き直す

    assert path == home / "sites.json"
    assert _read(path) == {str(first.resolve()): "/作り物/a2/python", str(second.resolve()): "/作り物/b/python"}
    assert site_registry.load_sites() == _read(path)
    assert [name for name in home.iterdir() if name.name != "sites.json"] == []  # 一時のファイルを残さない


def test_register_uses_this_python_by_default(tmp_path, home):
    site_registry.register_site(tmp_path)
    assert _read(home / "sites.json") == {str(tmp_path.resolve()): sys.executable}


def test_sites_path_defaults_to_home_dot_seizou(monkeypatch, tmp_path):
    monkeypatch.delenv("SEIZOU_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert site_registry.sites_path() == tmp_path / ".seizou" / "sites.json"


def test_missing_registry_is_empty_and_broken_registry_is_kept(tmp_path, home):
    assert site_registry.load_sites() == {}
    home.mkdir()
    for text in ("{壊れた JSON", '["一覧でない"]', '{"/a": 1}'):
        (home / "sites.json").write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            site_registry.register_site(tmp_path)
        assert (home / "sites.json").read_text(encoding="utf-8") == text  # 壊れた一覧を黙って消さない


# seizou new と seizou ai-docs の登録


def test_new_registers_site_with_start_line_python(built):
    site = built["site"]
    assert _read(built["sites_json"]) == {str(site.resolve()): sys.executable}
    # README.md の起動の 1 行と同じ python (start_line は sys.executable を書く)
    assert sys.executable in (site / "README.md").read_text(encoding="utf-8")
    assert f"登録した: {site.resolve()}" in built["out"]


def test_ai_docs_registers_existing_site_before_stopping(built, tmp_path, home):
    site = shutil.copytree(built["site"], tmp_path / "copied_site")
    code, out, err = run_new(["ai-docs", str(site)])
    assert code == 2  # CLAUDE.md と AGENTS.md が既にあるので、その 2 つは書かずに止まる
    assert "既にあります" in err and len(err.strip().splitlines()) == 1
    assert f"登録した: {site.resolve()}" in out
    assert _read(home / "sites.json") == {str(site.resolve()): sys.executable}


def test_ai_docs_does_not_register_non_site(tmp_path, home):
    folder = tmp_path / "plain"
    folder.mkdir()
    shutil.copyfile(FIXTURE, folder / "answers.yaml")
    code, _out, _err = run_new(["ai-docs", str(folder)])
    assert code == 2
    assert not (home / "sites.json").exists()


def test_registration_failure_is_one_line_and_not_fatal(tmp_path, home, capsys):
    home.mkdir()
    (home / "sites.json").write_text("{壊れた JSON", encoding="utf-8")
    cli.register_site_for_plugin(tmp_path)
    captured = capsys.readouterr()
    assert "置き場の一覧に登録できませんでした" in captured.err and "seizou ai-docs" in captured.err
    assert captured.out == ""
