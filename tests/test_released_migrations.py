"""出した migration を消さない・名前を変えない (CONTRIBUTING.md の「コードの貢献」)。

src/seizou/released_migrations.txt に並べた migration が、今の src/seizou/*/migrations/ に全部あることを確かめる。
会社の置き場の fields/ の migration は、部品の migration に依存して作られる。出した migration を消すか名前を変えると、
その版を入れた会社で migrate が依存を見つけられずに止まる。一覧に無い新しい migration は通す (足すのは自由)。

標準の library だけを使い、Django を読まない。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "seizou"
LISTING = PACKAGE / "released_migrations.txt"


def _listed():
    """一覧の行 (空行と # で始まる行を除く) を、書いた順に返す。"""
    lines = LISTING.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _on_disk():
    return {f"{path.parent.parent.name}/{path.name}" for path in PACKAGE.glob("*/migrations/0*.py")}


def test_listing_is_well_formed():
    listed = _listed()
    assert listed, "released_migrations.txt に migration の行が無い"
    bad = [line for line in listed if line.count("/") != 1 or not line.endswith(".py")]
    assert not bad, "「<部品の名前>/<ファイルの名前>.py」の形でない行: " + ", ".join(bad)
    duplicated = sorted({line for line in listed if listed.count(line) > 1})
    assert not duplicated, "2 回書いた行: " + ", ".join(duplicated)


def test_released_migrations_still_exist():
    missing = sorted(set(_listed()) - _on_disk())
    assert not missing, (
        "出した migration が src/seizou/*/migrations/ に無い (消したか名前を変えた。会社の fields/ の migration が依存する): "
        + ", ".join(missing)
    )
