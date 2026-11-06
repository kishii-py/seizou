"""seizou new が会社の置き場に書く、AI が守る決まり (CLAUDE.md と、その短い版の AGENTS.md) の試験 (決め 11-05 17:2x。N165)。

置き場は一時のフォルダ (環境変数 SEIZOU_SITES_DIR)。試験用の答えは tests/fixtures/companies/sample.yaml (作り物)。
雛形 (src/seizou/scaffold/*.tmpl) は拡張子が .tmpl なので tests/test_wording.py が見ない。言葉の決まりは、ここで作った物を見る。
"""

from __future__ import annotations

import pytest

from tests.test_cli_new import FIXTURE, run_new, secret_value
from tests.test_wording import BANNED, BUTTON_OLD, RECEIVED_OLD

RULE_DOCS = ("CLAUDE.md", "AGENTS.md")
# CLAUDE.md に要る字 (部品と会社の違い・書き方の決まり・検査の回し方・変えた後の流れ・鍵・言葉)
CLAUDE_WORDS = (
    "seizou_check",
    "/seizou:fix",
    "/simplify",
    "services",
    "fields/",
    "templates/",
    "checks.py",
    "settings.py",
    "manual/",
    "Base*View",
    "transaction.atomic",
    "manage.py check",
    "README.md",
    ".env",
    "db.sqlite3",
    "協力企業",
    "受信した注文",
    "ボタン",
)
AGENTS_WORDS = ("seizou_check", "services", "fields/", "transaction.atomic", "manage.py check", ".env", "協力企業")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """試験用の答えで 1 度だけ作った置き場 (読むだけの試験で使い回す)。"""
    sites = tmp_path_factory.mktemp("sites")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SEIZOU_SITES_DIR", str(sites))
        code, _out, err = run_new(["new", str(FIXTURE)])
    assert code == 0, err
    return sites / "sample_co"


def _read(site, name):
    return (site / name).read_text(encoding="utf-8")


def test_new_writes_rule_docs_with_company_name(site):
    for name in RULE_DOCS:
        text = _read(site, name)
        assert "見本の会社" in text, name
        assert "sample_co" in text, name
        assert "${" not in text, name  # 雛形の差し込みが全部埋まった
    assert len(_read(site, "AGENTS.md").splitlines()) < len(_read(site, "CLAUDE.md").splitlines())


def test_claude_md_has_rules_and_after_change_flow(site):
    text = _read(site, "CLAUDE.md")
    for word in CLAUDE_WORDS:
        assert word in text, word
    # 変えた後の流れ: seizou_check → /simplify → もう 1 度 seizou_check と manage.py check
    flow = text[text.index("## 変えた後の流れ") :]
    assert flow.index("seizou_check") < flow.index("/simplify") < flow.rindex("seizou_check") < flow.index("manage.py check")


def test_agents_md_is_short_version(site):
    text = _read(site, "AGENTS.md")
    for word in AGENTS_WORDS:
        assert word in text, word
    assert len(text.splitlines()) <= 12


def test_rule_docs_ask_for_reason_docstring(site):
    """決まりを足すときは docstring に「訳:」を書く (書き方の決まり R22。N226)。CLAUDE.md は書き方の決まりの節に理由と一緒に、AGENTS.md は番号と 1 句。"""
    text = _read(site, "CLAUDE.md")
    rules = text[text.index("## 書き方の決まり") : text.index("## 検査の回し方")]
    for word in ("訳:", "R22", "未確認", "checks.py", "改善が止まる"):
        assert word in rules, word
    agents = _read(site, "AGENTS.md")
    assert "訳:" in agents and "R22" in agents


def test_rule_docs_have_no_secret_and_no_old_words(site):
    secret = secret_value(site)
    for name in RULE_DOCS:
        text = _read(site, name)
        assert secret not in text, name
        for word in (BANNED, RECEIVED_OLD, BUTTON_OLD):
            assert word not in text, (name, word)
