"""リポジトリの改行の決まり (.gitattributes) の試験。

訳: Git for Windows の既定 (core.autocrlf=true) で clone すると、字のファイルが CRLF で取り出される。
同梱物 (scripts/vendor.toml の path の物) の bytes が変わり、tests/test_build_vendor.py の sha256 の試験が落ちる見立て
(出所 = 精査 Q6 (Windows) の 13 と本体の決め 11-11 14:2x。Windows の実機で clone し直す確かめは未確認)。
そこで .gitattributes で字のファイルを LF にそろえ、同梱物と絵は bytes を変えない印を付ける。
変えてよい条件 = 同梱物の置き場を変えたときは、.gitattributes の -text の行を一緒に直す。

取り出したファイルの bytes でなく index の改行 (git ls-files --eol の i/…) を見るので、取り出した OS の設定に左右されない。
git が無いか、このファイルの親の親がリポジトリの根でないとき (pip で入れた形など) は skip する。
"""

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GITATTRIBUTES = ROOT / ".gitattributes"
VENDOR_TOML = ROOT / "scripts" / "vendor.toml"
LF_RULE = "* text=auto eol=lf"
#: 字として扱わない事を見る絵 (今のリポジトリに在る PNG の 1 本)
SAMPLE_IMAGE = "docs/images/dashboard.png"

#: 改行を見ない道 (リポジトリの根からの道)。
#: わざと CRLF を持たせた試験のデータを足すときは、ここに道を書く。BOM だけの物は外さなくてよい (CRLF でなければ当たらない)。
CRLF_ALLOWED_PATHS: frozenset[str] = frozenset()


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise AssertionError(f"git {' '.join(args)} が失敗しました: {message}")
    return result.stdout.decode("utf-8")


@pytest.fixture
def git_repo():
    if shutil.which("git") is None:
        pytest.skip("git が無いので、index の改行と .gitattributes の印を見られない")
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=ROOT, capture_output=True, check=False
    )
    if result.returncode != 0:
        pytest.skip("git の作業場でない (pip で入れた形など) ので、index の改行を見られない")
    top = Path(result.stdout.decode("utf-8").strip()).resolve()
    if top != ROOT:
        pytest.skip(f"このファイルの親の親 ({ROOT}) がリポジトリの根 ({top}) でないので、道が合わない")


def _vendored_paths() -> list[str]:
    with VENDOR_TOML.open("rb") as f:
        items = tomllib.load(f)["item"]
    return [item["path"] for item in items if "path" in item]


def _check_attr(attrs: list[str], paths: list[str]) -> dict[str, dict[str, str]]:
    """git check-attr -z の答えを {道: {印: 値}} にする。値は set / unset / unspecified か、印に書いた値。"""
    fields = _git("check-attr", "-z", *attrs, "--", *paths).split("\0")
    answer: dict[str, dict[str, str]] = {}
    for i in range(0, len(fields) - 2, 3):
        path, attr, value = fields[i : i + 3]
        answer.setdefault(path, {})[attr] = value
    return answer


def test_gitattributes_sets_lf_and_keeps_vendored_bytes(git_repo):
    assert GITATTRIBUTES.is_file(), (
        ".gitattributes がありません (字のファイルを LF にそろえ、同梱物と絵は bytes を変えない印を付ける)"
    )
    lines = [line.strip() for line in GITATTRIBUTES.read_text(encoding="utf-8").splitlines()]
    assert LF_RULE in lines, f".gitattributes に「{LF_RULE}」の行がありません"

    vendored = _vendored_paths()
    assert vendored
    attrs = _check_attr(["text"], vendored)
    not_kept = [f"{path} (text: {attrs[path]['text']})" for path in vendored if attrs[path]["text"] != "unset"]
    assert not not_kept, "同梱物が -text になっていません (sha256 を見る試験があるため): " + ", ".join(not_kept)

    image = _check_attr(["binary", "text"], [SAMPLE_IMAGE])[SAMPLE_IMAGE]
    assert image["binary"] == "set" or image["text"] == "unset", f"{SAMPLE_IMAGE} が字として扱われます: {image}"


def test_no_tracked_file_has_crlf(git_repo):
    found = []
    for entry in _git("ls-files", "--eol", "-z").split("\0"):
        if not entry:
            continue
        info, _, path = entry.partition("\t")
        index_eol = info.split()[0]
        if index_eol in ("i/crlf", "i/mixed") and path not in CRLF_ALLOWED_PATHS:
            found.append(f"{path} ({index_eol})")
    assert not found, "index に CRLF か混ざりの改行のファイルがあります (LF にそろえる): " + ", ".join(found)
