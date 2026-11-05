"""命令 seizou ai-docs (src/seizou/cli.py) の試験 (決め 11-05 22:5x。N170)。

既にある会社の置き場に、seizou new と同じ CLAUDE.md と AGENTS.md を後から書く。置き場は seizou new で 1 度だけ作り
(環境変数 SEIZOU_SITES_DIR。答えは tests/fixtures/companies/sample.yaml の作り物)、書き換える試験は一時のフォルダに写してから打つ。
"""

from __future__ import annotations

import shutil

import pytest

from seizou import cli
from tests.test_cli_new import FIXTURE, run_new

RULE_DOCS = ("CLAUDE.md", "AGENTS.md")
EDITED = "# 人が直した決まり\n"


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
    """作った置き場の写し。フォルダの名前を会社の id (sample_co) と変え、id を answers.yaml から読むことも見る。"""
    return shutil.copytree(built, tmp_path / "copied_site")


def _texts(folder):
    return {name: (folder / name).read_text(encoding="utf-8") for name in RULE_DOCS}


def test_ai_docs_restores_what_new_wrote(site):
    before = _texts(site)  # seizou new が書いた物の写し
    for name in RULE_DOCS:
        (site / name).unlink()
    code, out, err = run_new(["ai-docs", str(site)])
    assert code == 0, err
    assert "書いた:" in out
    assert _texts(site) == before


def test_folder_without_manage_py_is_refused(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    shutil.copyfile(FIXTURE, folder / "answers.yaml")
    code, _out, err = run_new(["ai-docs", str(folder)])
    assert code == 2
    assert "置き場ではありません" in err and "manage.py" in err
    assert len(err.strip().splitlines()) == 1
    for name in RULE_DOCS:
        assert not (folder / name).exists(), name


def test_existing_rule_doc_is_kept_without_force(site):
    (site / "CLAUDE.md").write_text(EDITED, encoding="utf-8")
    (site / "AGENTS.md").unlink()
    code, _out, err = run_new(["ai-docs", str(site)])
    assert code == 2
    assert "既にあります: CLAUDE.md" in err and "--force" in err
    assert len(err.strip().splitlines()) == 1
    assert (site / "CLAUDE.md").read_text(encoding="utf-8") == EDITED
    assert not (site / "AGENTS.md").exists()  # 片方だけがあるときも、何も書かない


def test_force_rewrites_both(built, site):
    for name in RULE_DOCS:
        (site / name).write_text(EDITED, encoding="utf-8")
    code, _out, err = run_new(["ai-docs", str(site), "--force"])
    assert code == 0, err
    assert _texts(site) == _texts(built)


def test_help_shows_ai_docs_and_force(capsys):
    assert "ai-docs" in cli.build_parser().format_help()
    with pytest.raises(SystemExit) as stopped:
        cli.main(["ai-docs", "--help"])
    assert stopped.value.code == 0
    out = capsys.readouterr().out
    assert "site" in out and "--force" in out
