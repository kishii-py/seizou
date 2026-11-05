"""会社の置き場の .gitignore (git に入れない物 = 鍵と DB と上げたファイル) の試験 (決め 11-06 00:0x。N172)。

seizou new が置き場に書き、seizou ai-docs は無いときだけ書く (既にある .gitignore は --force でも書き直さない)。置き場は
seizou new で 1 度だけ作り (環境変数 SEIZOU_SITES_DIR。答えは tests/fixtures/companies/sample.yaml の作り物)、書き換える試験は一時のフォルダに写してから打つ。
"""

from __future__ import annotations

import shutil

import pytest

from tests.test_cli_new import FIXTURE, run_new

RULE_DOCS = ("CLAUDE.md", "AGENTS.md")
EDITED = "# 人が直した .gitignore\n.env\n"


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


def test_new_writes_gitignore_for_secrets_and_db(built):
    lines = (built / ".gitignore").read_text(encoding="utf-8").splitlines()
    for line in (".env", "db.sqlite3", "media/"):
        assert line in lines, line
    assert not [line for line in lines if line.startswith("manual")]  # 説明書と絵は置き場の一部なので git に入れる


def test_ai_docs_writes_gitignore_only_when_missing(built, site):
    for name in (*RULE_DOCS, ".gitignore"):
        (site / name).unlink()
    code, out, err = run_new(["ai-docs", str(site)])
    assert code == 0, err
    assert out.strip().endswith("・AGENTS.md・.gitignore")
    assert (site / ".gitignore").read_text(encoding="utf-8") == (built / ".gitignore").read_text(encoding="utf-8")
    for name in RULE_DOCS:
        (site / name).unlink()
    code, out, err = run_new(["ai-docs", str(site)])
    assert code == 0, err
    assert out.strip().endswith("・AGENTS.md")  # 既にあれば書かず、「書いた:」の行にも出さない


def test_force_keeps_edited_gitignore(site):
    (site / ".gitignore").write_text(EDITED, encoding="utf-8")
    code, out, err = run_new(["ai-docs", str(site), "--force"])
    assert code == 0, err
    assert out.strip().endswith("・AGENTS.md")
    assert (site / ".gitignore").read_text(encoding="utf-8") == EDITED
