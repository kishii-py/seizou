"""画面ごとの説明書の断片の標準の組 (src/seizou/core/manual_default/) と、seizou new がそれを置き場の manual/ に写す口 (cli.copy_manual_default)。

標準の組は A 社の種の断片 (examples/companies/a_machining/manual/) を元に、会社の設定と会社ごとの項目で字が変わる 8 本と、
作り物の会社の見本と書いた前書き (index.html) を、業種と会社に寄らない字にした物。
形と字の決まりは、2 社の種の試験 (tests/test_examples_manual.py) を、種のフォルダを標準の組に差し替えて回す (既にある試験は変えない)。
--demo のときは種の断片を先に写すので、同じ名前は種の断片が勝つ。
"""

import shutil
from pathlib import Path

import pytest

from seizou import cli
from tests import test_examples_manual as seed_tests
from tests.test_cli_new import FIXTURE, run_new

DEFAULT_ID = "manual_default"
DEFAULT_DIR = Path(cli.__file__).resolve().parent / "core" / "manual_default"
# A 社の種と字が違う断片 (設定と会社ごとの項目で字が変わる 8 本と、前書き)
SETTING_FRAGMENTS = (
    "masters_item_create",
    "masters_item_detail",
    "masters_item_list",
    "masters_item_modal",
    "masters_item_update",
    "orders_order_update",
    "shipping_shipment_create",
    "shipping_shipment_update",
)
NEUTRAL = (*SETTING_FRAGMENTS, seed_tests.INTRO)
# 種の試験のうち、会社の答えの YAML の設定と種の置き場を読まない物 (種のフォルダを標準の組に差し替えて回す)
SEED_CHECKS = (
    seed_tests.test_fragment_names_are_screens,
    seed_tests.test_first_tier_fragments_exist,
    seed_tests.test_every_screen_has_fragment,
    seed_tests.test_fragment_shape,
    seed_tests.test_frame_ids,
    seed_tests.test_rules_are_quoted_from_rules_md,
)
# 受注より多い出荷の設定の画面の字 (core.settings_screen の選べる値)
OVER_SHIPMENT_LABELS = ("止める", "知らせる", "通す")


def default_names() -> list[str]:
    return sorted(path.name for path in DEFAULT_DIR.glob("*.html"))


def source_outside_default() -> str:
    """「」の字を探す元。種の試験の source_text と同じ物から、標準の組のフォルダを除いた物。"""
    parts = []
    for path in sorted((seed_tests.ROOT / "src" / "seizou").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".html", ".js"} and DEFAULT_ID not in path.parts:
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    parts.append((seed_tests.ROOT / "RULES.md").read_text(encoding="utf-8"))
    for path in sorted(seed_tests.COMPANIES_DIR.glob("*.yaml")):
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


@pytest.fixture
def default_as_company(monkeypatch):
    """種の試験の manual_folder を、DEFAULT_ID のときだけ標準の組のフォルダを返す形に差し替える。"""
    real = seed_tests.manual_folder
    monkeypatch.setattr(seed_tests, "manual_folder", lambda company_id: DEFAULT_DIR if company_id == DEFAULT_ID else real(company_id))


# ---------------------------------------------------------------- 組と形 (種と同じ決まり)


@pytest.mark.parametrize("check", SEED_CHECKS, ids=lambda check: check.__name__)
def test_default_set_passes_seed_checks(check, default_as_company):
    check(DEFAULT_ID)


@pytest.mark.parametrize("company_id", (DEFAULT_ID, *seed_tests.COMPANY_IDS))
def test_quoted_words_exist_outside_default(company_id, default_as_company):
    """断片の「」の字が、標準の組の外 (部品のソースと RULES.md と答えの YAML) にある。

    種の試験の source_text は src/seizou の下の .html を全部読むので、標準の組を置くと、種の「」の字も標準の組の中で見つかる。
    ここでは標準の組を除いて、標準の組と 2 社の種の両方を見る。
    """
    source = source_outside_default()
    problems = [
        f"{company_id}/{name}: 「{word}」"
        for name, raw in seed_tests.fragments(company_id).items()
        for word in seed_tests.QUOTED.findall(seed_tests.plain(raw))
        if word not in source
    ]
    assert problems == [], "\n".join(problems)


# ---------------------------------------------------------------- A 社の種とのずれ


def test_default_set_is_seed_a_except_neutral():
    a_folder = seed_tests.manual_folder("a_machining")
    names = default_names()
    assert names == sorted(path.name for path in a_folder.glob("*.html"))
    differ = sorted(Path(name).stem for name in names if (DEFAULT_DIR / name).read_bytes() != (a_folder / name).read_bytes())
    assert differ == sorted(NEUTRAL), "標準の組と A 社の種で字が違ってよいのは、設定と会社ごとの項目で字が変わる 8 本と前書きだけ"


def test_default_set_has_no_company_words():
    words = ["作り物"]
    for company_id in seed_tests.COMPANY_IDS:
        words += [str(seed_tests.answers(company_id)["industry"]), *seed_tests.item_labels(company_id)]
    texts = {path.stem: seed_tests.plain(path.read_text(encoding="utf-8")) for path in sorted(DEFAULT_DIR.glob("*.html"))}
    problems = [f"{name}: {word}" for name, text in texts.items() for word in words if word in text]
    assert problems == [], "\n".join(problems)
    # 受注より多い出荷の動きは、会社の設定の 3 つの値を全部書く (1 つの会社の値の字にしない)
    for name in seed_tests.OVER_SHIPMENT_FRAGMENTS:
        missing = [label for label in OVER_SHIPMENT_LABELS if f"「{label}」" not in texts[name]]
        assert missing == [], f"{name}: {missing}"
        assert not [sentence for sentence in seed_tests.OVER_SHIPMENT_TEXT.values() if sentence in texts[name]], name


# ---------------------------------------------------------------- 写す順 (種が勝つ)


def test_seed_fragments_win_and_default_fills_the_rest(tmp_path):
    b_folder = seed_tests.manual_folder("b_plating")
    seeds = tmp_path / "seeds"
    (seeds / "manual").mkdir(parents=True)
    for name in SETTING_FRAGMENTS:
        shutil.copyfile(b_folder / f"{name}.html", seeds / "manual" / f"{name}.html")
    site = tmp_path / "site"

    assert cli.copy_manual(seeds, site) == len(SETTING_FRAGMENTS)
    assert cli.copy_manual_default(site) == len(default_names()) - len(SETTING_FRAGMENTS)

    written = sorted((site / "manual").glob("*.html"))
    assert [path.name for path in written] == default_names()
    for path in written:
        source = b_folder if path.stem in SETTING_FRAGMENTS else DEFAULT_DIR
        assert path.read_bytes() == (source / path.name).read_bytes(), path.name
    # 2 回目は全部が既にあるので 0
    assert cli.copy_manual_default(site) == 0


def test_full_seed_leaves_nothing_for_default(tmp_path):
    b_folder = seed_tests.manual_folder("b_plating")
    site = tmp_path / "site"

    assert cli.copy_manual(seed_tests.COMPANIES_DIR / "b_plating", site) == len(default_names())
    assert cli.copy_manual_default(site) == 0
    for name in NEUTRAL:
        assert (site / "manual" / f"{name}.html").read_bytes() == (b_folder / f"{name}.html").read_bytes(), name


# ---------------------------------------------------------------- seizou new (--demo 無し)


def test_new_without_demo_copies_default_set(tmp_path, monkeypatch):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))

    code, out, err = run_new(["new", str(FIXTURE)])

    assert code == 0, err
    manual = tmp_path / "sample_co" / "manual"
    names = default_names()
    assert sorted(path.name for path in manual.glob("*.html")) == names
    assert all((manual / name).read_bytes() == (DEFAULT_DIR / name).read_bytes() for name in names)
    assert f"説明書の断片を写しました: {len(names)} 件 (標準 {len(names)}・種 0。{manual})" in out
    assert (manual / "images").is_dir()
