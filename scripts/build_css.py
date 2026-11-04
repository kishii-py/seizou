"""CSS を作る道具 (Tailwind の単体の実行ファイルと daisyUI)。

標準の library だけで動く (Django に依存しない)。

使い方:
    uv run python scripts/build_css.py                  配布物の CSS を作り直す
    uv run python scripts/build_css.py --check          作り直した物と今の CSS を比べる (違えば rc 1)
    uv run python scripts/build_css.py --out <パス> --source <置き場> --theme <名前> --primary <色>
                                                        会社の CSS を会社の theme つきで作る

道具の置き場は .tools/tailwind/。環境変数 SEIZOU_TOOLS_DIR があれば、その下の tailwind/ を使う。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR_TOML = ROOT / "scripts" / "vendor.toml"
INPUT_CSS = ROOT / "scripts" / "css" / "input.css"
SAFELIST_HTML = ROOT / "scripts" / "css" / "safelist.html"
DEFAULT_OUT = ROOT / "src" / "seizou" / "core" / "static" / "css" / "app.css"
REQUIRED_CLASSES = ("btn", "table", "alert", "modal", "badge")


class BuildError(Exception):
    """CSS を作れなかった理由 (日本語)。"""


def tools_dir() -> Path:
    base = os.environ.get("SEIZOU_TOOLS_DIR")
    return (Path(base) if base else ROOT / ".tools") / "tailwind"


def load_items(path: Path = VENDOR_TOML) -> list[dict]:
    with path.open("rb") as f:
        return tomllib.load(f).get("item", [])


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, sha256: str) -> None:
    """版を入れた URL から取り、sha256 が一覧と合うときだけ dest に置く。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".download-")
    tmp = Path(tmp_name)
    try:
        with (
            os.fdopen(fd, "wb") as out,
            urllib.request.urlopen(url, timeout=120) as resp,
        ):
            shutil.copyfileobj(resp, out)
        actual = sha256_of(tmp)
        if actual != sha256:
            raise BuildError(
                f"取った物の sha256 が一覧と違います: {url}\n"
                f"  一覧: {sha256}\n  実際: {actual}\n"
                "scripts/vendor.toml の版と sha256 を確かめてください。"
            )
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def current_platform() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return "macos-arm64"
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "linux-x64"
    if system == "Windows" and machine in ("amd64", "x86_64"):
        return "windows-x64"
    raise BuildError(
        f"この OS と CPU ({system} {machine}) 向けの Tailwind は一覧にありません。"
    )


def prepare_executable(path: Path) -> None:
    """取った実行ファイルを動ける形にし、--help で起動を確かめる。"""
    system = platform.system()
    if system != "Windows":
        path.chmod(path.stat().st_mode | 0o755)
    if system == "Darwin":
        result = subprocess.run(
            ["codesign", "--force", "--sign", "-", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise BuildError(
                f"Tailwind の実行ファイルに署名を付けられませんでした:\n{result.stderr}"
            )
    result = subprocess.run(
        [str(path), "--help"], check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise BuildError(
            f"Tailwind の実行ファイルが起動しませんでした (rc {result.returncode}):\n{result.stderr}"
        )


def source_sha_marker(dest: Path) -> Path:
    """取った直後 (署名の前) の sha256 を控えるファイル (実行ファイルの隣。.tools/ は git の外)。"""
    return dest.with_name(dest.name + ".source-sha256")


def recorded_source_sha(dest: Path) -> str | None:
    try:
        return source_sha_marker(dest).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def record_source_sha(dest: Path, sha256: str) -> None:
    source_sha_marker(dest).write_text(sha256 + "\n", encoding="utf-8")


def is_current(dest: Path, sha256: str) -> bool:
    """置いてある道具が一覧の版か。macOS は prepare_executable の署名で実行ファイルの sha256 が変わるので、
    取った直後の sha256 の控え (source_sha_marker) が一覧と合えば同じ版とみなす (毎回 80 MB を取り直さない)。"""
    if not dest.exists():
        return False
    if sha256_of(dest) == sha256:
        return True
    return recorded_source_sha(dest) == sha256


def ensure_tools(items: list[dict], directory: Path) -> Path:
    """道具の置き場に実行ファイルと mjs 2 つをそろえ、実行ファイルのパスを返す。"""
    plat = current_platform()
    executable = None
    for item in items:
        if "tool" not in item:
            continue
        if "platform" in item and item["platform"] != plat:
            continue
        dest = directory / item["tool"]
        fresh = False
        if not is_current(dest, item["sha256"]):
            download(item["url"], dest, item["sha256"])
            record_source_sha(dest, item["sha256"])
            fresh = True
        if "platform" in item:
            executable = dest
            if fresh:
                prepare_executable(dest)
    if executable is None:
        raise BuildError("一覧に Tailwind の実行ファイルの項目がありません。")
    return executable


def package_template_dirs() -> list[Path]:
    """部品のテンプレートの置き場 (seizou の package の下の templates/)。"""
    spec = importlib.util.find_spec("seizou")
    if spec is not None and spec.submodule_search_locations:
        package = Path(next(iter(spec.submodule_search_locations)))
    else:
        package = ROOT / "src" / "seizou"
    dirs = [package / "templates", *sorted(package.glob("*/templates"))]
    return [d for d in dirs if d.is_dir()]


def css_string(value: str) -> str:
    return '"' + value.replace("\\", "/").replace('"', '\\"') + '"'


def compose_input(sources: list[Path], theme: str | None, primary: str | None) -> str:
    """入力の CSS の元に @source の行と会社の theme を足した一時の入力の中身。"""
    lines = [INPUT_CSS.read_text(encoding="utf-8").rstrip("\n"), ""]
    for source in [SAFELIST_HTML, *package_template_dirs(), *sources]:
        if source.exists():
            lines.append(f"@source {css_string(source.resolve().as_posix())};")
    if theme:
        lines += [
            "",
            '@plugin "./daisyui-theme.mjs" {',
            f"  name: {css_string(theme)};",
            "  default: false;",
            "  prefersdark: false;",
            "  color-scheme: light;",
            f"  --color-primary: {primary};",
            "}",
        ]
    return "\n".join(lines) + "\n"


def verify_output(path: Path) -> str | None:
    """作った CSS を確かめる。良ければ None、悪ければ理由 (日本語) を返す。"""
    if not path.exists():
        return "CSS が出力されていません。"
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return "CSS が空です。"
    missing = [
        name for name in REQUIRED_CLASSES if not re.search(rf"\.{name}(?![\w-])", text)
    ]
    if missing:
        return "CSS に決まった class がありません: " + "、".join(missing)
    return None


def same_content(built: Path, current: Path) -> bool:
    """--check の比べ方。作り直した物と今の CSS がバイトで同じなら真。"""
    return current.exists() and built.read_bytes() == current.read_bytes()


def build(
    out_tmp: Path, sources: list[Path], theme: str | None, primary: str | None
) -> None:
    directory = tools_dir()
    directory.mkdir(parents=True, exist_ok=True)
    executable = ensure_tools(load_items(), directory)
    input_tmp = directory / "input.tmp.css"
    input_tmp.write_text(compose_input(sources, theme, primary), encoding="utf-8")
    result = subprocess.run(
        [str(executable), "-i", str(input_tmp), "-o", str(out_tmp), "--minify"],
        cwd=directory,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BuildError(
            f"Tailwind が失敗しました (rc {result.returncode}):\n{result.stderr}"
        )
    problem = verify_output(out_tmp)
    if problem:
        raise BuildError(problem)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tailwind と daisyUI で CSS を作る。")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="出力先 (既定は配布物の app.css)"
    )
    parser.add_argument(
        "--source",
        type=Path,
        action="append",
        default=[],
        help="足すテンプレートの置き場",
    )
    parser.add_argument("--theme", help="会社の theme の名前")
    parser.add_argument("--primary", help="会社の theme の primary の色")
    parser.add_argument(
        "--check", action="store_true", help="作り直した物と出力先を比べるだけにする"
    )
    args = parser.parse_args(argv)
    if bool(args.theme) != bool(args.primary):
        parser.error("--theme と --primary は 2 つ一緒に渡してください。")

    out: Path = args.out.resolve()
    fd, tmp_name = tempfile.mkstemp(suffix=".css", prefix="seizou-")
    os.close(fd)
    out_tmp = Path(tmp_name)
    try:
        build(out_tmp, args.source, args.theme, args.primary)
        if args.check:
            if same_content(out_tmp, out):
                print(f"CSS は作り直した物と同じです: {out}")
                return 0
            print(
                f"CSS が作り直した物と違います: {out}\n"
                "scripts/build_css.py を回して、出来た CSS を commit してください。",
                file=sys.stderr,
            )
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, staged_name = tempfile.mkstemp(
            dir=out.parent, prefix=".app-", suffix=".css"
        )
        os.close(fd)
        shutil.copyfile(out_tmp, staged_name)
        os.chmod(staged_name, 0o644)
        os.replace(staged_name, out)
        print(f"CSS を作りました: {out}")
        return 0
    except BuildError as e:
        print(
            f"CSS を作れませんでした。前の CSS をそのまま残します。\n{e}",
            file=sys.stderr,
        )
        return 1
    finally:
        out_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
