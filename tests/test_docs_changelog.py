"""変更の記録 (CHANGELOG.md) の、出す前の版の節の形。

出所: 本人の決め 11-14 09:1x (次の版 0.2.0 を出す準備)。上げる会社がする事を、版を上げる通しで確かめた結果から書く。
変えてよい条件 = 版を出した時 (節の頭に日付を入れ、出す前だけの確かめを外す)。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "seizou"


def _text():
    return (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _section(version):
    text = _text()
    heads = [match for match in re.finditer(r"^## (\S+).*$", text, re.M)]
    for index, match in enumerate(heads):
        if match.group(1) == version:
            end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
            return text[match.start() : end]
    raise AssertionError(f"{version} の節が無い")


def test_new_version_is_above_the_first_one():
    text = _text()
    assert text.index("## 0.2.0") < text.index("## 0.1.0")


def test_new_version_has_the_four_parts():
    section = _section("0.2.0")
    for head in ("### 0.1.0 の置き場を上げる会社がすること", "### 動きが変わった物", "### 足した物", "### 安全に関わる直し"):
        assert head in section, head
    # 上げる通しで確かめた範囲と、確かめていない範囲を書く
    assert "未確認" in section
    assert "manage.py seizou_manual_defaults" in section
    assert "manage.py seizou_serve" in section


def _package_version():
    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def _released_lines():
    return (PACKAGE / "released_migrations.txt").read_text(encoding="utf-8").splitlines()


def test_migration_count_matches_the_block_added_for_the_version():
    """節に書いた migration の本数は、出した一覧 (released_migrations.txt) の「0.2.0 で足した物」の塊の行の数と同じ。"""
    lines = _released_lines()
    starts = [index for index, line in enumerate(lines) if line.startswith("#") and "0.2.0" in line]
    assert len(starts) == 1, "出した一覧に、0.2.0 で足した物の塊の見出しが 1 つ要る"
    block = [line for line in lines[starts[0] + 1 :] if line.strip() and not line.startswith("#")]
    match = re.search(r"migrate が (\d+) 本の migration を当てる", _section("0.2.0"))
    assert match, "migration の本数の文が無い"
    assert int(match.group(1)) == len(block)


def test_a_released_version_lists_every_migration_and_has_a_dated_section():
    """出す版の commit (版の番号に .dev が無い) では、出した一覧に全部の migration が載り、変更の記録のその版の節の頭に日付がある。

    途中の版 (.dev) の間は、まだ出していない migration があってよい (次に版を出す時に足す)。
    訳: 一覧に無い migration を配ると、後でその migration を消すか名前を変えても試験が気づかず、その版を入れた会社で migrate が止まる。
    出所 = 0.2.0 を出す準備 (本人の決め 11-14)。変えてよい条件 = 版の出し方を変えたとき。
    """
    version = _package_version()
    listed = {line.strip() for line in _released_lines() if line.strip() and not line.startswith("#")}
    on_disk = {f"{path.parent.parent.name}/{path.name}" for path in PACKAGE.glob("*/migrations/0*.py")}
    if ".dev" in version:
        assert listed <= on_disk
        return
    assert on_disk - listed == set(), sorted(on_disk - listed)
    assert re.search(rf"^## {re.escape(version)} \(\d{{4}}-\d{{2}}-\d{{2}}\)$", _text(), re.M), f"{version} の節の頭に日付が無い"


def _released_versions():
    """変更の記録の、日付つきの節の版の番号 (新しい順)。"""
    return re.findall(r"^## (\d+\.\d+\.\d+) \(\d{4}-\d{2}-\d{2}\)$", _text(), re.M)


def _numbers(version):
    return tuple(int(part) for part in version.split("."))


def test_package_version_fits_the_changelog_and_the_lock():
    """版の番号は、lock と同じで、変更の記録の出した版と食い違わない。

    出す版の commit では、いちばん新しい日付つきの節と同じ番号。出した後の main は、次の途中の版 (.dev。GOVERNANCE.md の決まり) で、
    その番号は、いちばん新しい出した版より大きい (出した版と同じか小さい番号の途中の版は、出した版より前と見分けが付かない)。
    出所 = 0.2.0 を出した時の段取り (本人の決め 11-14)。変えてよい条件 = 版の番号の決まり (GOVERNANCE.md) を変えたとき。
    """
    version = _package_version()
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert f'name = "seizou"\nversion = "{version}"\n' in lock
    released = _released_versions()
    assert released, "変更の記録に、日付つきの節が無い"
    if ".dev" in version:
        assert _numbers(version.split(".dev")[0]) > _numbers(released[0]), (version, released[0])
    else:
        assert version == released[0]


def test_intro_names_the_next_patch_version_of_the_current_line():
    """頭の文の版の番号の例は、GOVERNANCE.md と同じ (0.2.0 の次は 0.2.1)。"""
    intro = _text().split("## ", 1)[0]
    assert "`0.2.0` の次は `0.2.1`" in intro
    assert "`0.1.0` の次は `0.1.1`" not in intro


def test_new_version_names_the_permission_change_and_the_fragment_counts():
    """権限が変わった操作 (注文回答の送り直し) と、説明書の断片の数の読み方を書く (11-14 に確かめた事)。"""
    section = _section("0.2.0")
    assert "送り直す" in section and "金額の閲覧" in section
    # 説明書の断片: 標準が変わった数 (43)・手を入れていない物を替える事・直した物は替えない事・替える前の物の在りか
    assert "43 枚" in section
    assert "新しい標準に替える" in section and "自社で直した断片は替えず" in section
    assert "site/manual/" in section
    # 配布物を空の環境に入れて置き場を作る通しも確かめた
    assert "空の環境" in section


def test_new_version_says_where_the_upgrade_was_tried():
    """上げる通しを確かめた所と、確かめていない所を書き分ける (VPS の形は 11-14 に確かめた。PostgreSQL と Windows の実機はまだ)。"""
    intro = _section("0.2.0").split("### ", 1)[0]
    assert "VPS の形" in intro and "gunicorn と nginx" in intro
    unverified = intro.split("未確認", 1)[0].rsplit("。", 1)[-1]
    assert "PostgreSQL" in unverified and "Windows の実機" in unverified
    assert "VPS" not in unverified
