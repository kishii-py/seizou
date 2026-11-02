"""CSS を作る道具 (scripts/build_css.py) の試験。Tailwind は実際には走らせない。"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ALL_CLASSES = ".btn{a:1}.table{a:1}.alert{a:1}.modal{a:1}.badge{a:1}"


@pytest.fixture(scope="module")
def build_css():
    spec = importlib.util.spec_from_file_location(
        "build_css", ROOT / "scripts" / "build_css.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verify_output_rejects_missing_file(build_css, tmp_path):
    assert build_css.verify_output(tmp_path / "none.css") is not None


def test_verify_output_rejects_empty(build_css, tmp_path):
    css = tmp_path / "app.css"
    css.write_text("  \n", encoding="utf-8")
    assert build_css.verify_output(css) is not None


def test_verify_output_rejects_missing_class(build_css, tmp_path):
    css = tmp_path / "app.css"
    css.write_text(".btn{a:1}.table{a:1}.alert{a:1}.modal{a:1}", encoding="utf-8")
    problem = build_css.verify_output(css)
    assert problem is not None
    assert "badge" in problem


def test_verify_output_rejects_prefix_only(build_css, tmp_path):
    css = tmp_path / "app.css"
    css.write_text(ALL_CLASSES.replace(".btn{", ".btn-primary{"), encoding="utf-8")
    assert "btn" in build_css.verify_output(css)


def test_verify_output_accepts_required_classes(build_css, tmp_path):
    css = tmp_path / "app.css"
    css.write_text(ALL_CLASSES, encoding="utf-8")
    assert build_css.verify_output(css) is None


def test_same_content(build_css, tmp_path):
    built = tmp_path / "built.css"
    current = tmp_path / "current.css"
    built.write_text(ALL_CLASSES, encoding="utf-8")
    assert not build_css.same_content(built, current)
    current.write_text(ALL_CLASSES, encoding="utf-8")
    assert build_css.same_content(built, current)
    current.write_text(ALL_CLASSES + ".card{a:1}", encoding="utf-8")
    assert not build_css.same_content(built, current)


def test_main_keeps_previous_css_on_failure(build_css, tmp_path, monkeypatch):
    out = tmp_path / "app.css"
    out.write_text(ALL_CLASSES, encoding="utf-8")

    def failing_build(*args):
        raise build_css.BuildError("試験の失敗")

    monkeypatch.setattr(build_css, "build", failing_build)
    assert build_css.main(["--out", str(out)]) != 0
    assert out.read_text(encoding="utf-8") == ALL_CLASSES


def test_main_check_fails_when_css_differs(build_css, tmp_path, monkeypatch):
    out = tmp_path / "app.css"
    out.write_text(ALL_CLASSES, encoding="utf-8")

    def fake_build(out_tmp, *args):
        out_tmp.write_text(ALL_CLASSES + ".card{a:1}", encoding="utf-8")

    monkeypatch.setattr(build_css, "build", fake_build)
    assert build_css.main(["--out", str(out), "--check"]) != 0
    assert out.read_text(encoding="utf-8") == ALL_CLASSES
