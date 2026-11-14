"""版を上げた置き場の説明書の断片をそろえる命令 (manage.py seizou_manual_defaults)。

出所: 11-14 に、PyPI の 0.1.0 で作った置き場を今の版に上げる通しで見つけた事と、本人の決め 11-14 12:1x。
置き場の manual/ は作った時の断片のままなので、後の版で増えた画面の「使い方」が「この画面の説明書はまだありません。」になり、
前からある断片は古い説明のまま残る。命令は、無い断片を写し、会社が手を入れていない断片 (前の版の標準と同じ中身の物) だけを
新しい標準に替える。会社が直した断片は替えない。

見本: tests/fixtures/manual_010/ は、tag v0.1.0 の標準の断片 3 枚の写し (今の標準とは文が違う)。CI は git の履歴を持たないので、
前の版の中身はこの見本で確かめる。
"""

import io
import re
from pathlib import Path

import pytest
from django.core.management import call_command

from seizou.core import manual, manual_defaults

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "manual_010"
OLD_NAMES = ["feedback_feedback_detail.html", "orders_order_detail.html", "shipping_shipment_update.html"]


@pytest.fixture
def site(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    return tmp_path


def _defaults():
    return sorted(path.name for path in manual_defaults.default_dir().glob("*.html"))


def _standard(name):
    return (manual_defaults.default_dir() / name).read_bytes()


def test_default_dir_is_inside_the_package():
    names = _defaults()
    assert "masters_staff_list.html" in names
    assert len(names) > 150


def test_fixtures_are_old_versions_of_the_standard():
    """見本は今の標準と中身が違い、前の版の印の一覧に同じ名前で載っている。"""
    history = manual_defaults.load_history()
    for name in OLD_NAMES:
        data = (FIXTURES / name).read_bytes()
        assert data != _standard(name)
        assert manual_defaults.content_hash(data) in history[name]


def test_copies_missing_replaces_untouched_and_keeps_edited(site):
    folder = site / "manual"
    folder.mkdir()
    # 前の版の標準のまま (会社が手を入れていない)
    for name in OLD_NAMES[:2]:
        (folder / name).write_bytes((FIXTURES / name).read_bytes())
    # 会社が直した断片
    own = folder / "orders_order_list.html"
    own.write_text("<p>自社で直した説明</p>", encoding="utf-8")

    result = manual_defaults.sync()

    assert result["replaced"] == OLD_NAMES[:2]
    for name in OLD_NAMES[:2]:
        assert (folder / name).read_bytes() == _standard(name)
    assert own.read_text(encoding="utf-8") == "<p>自社で直した説明</p>"
    assert result["kept"] == ["orders_order_list.html"]
    assert "masters_staff_list.html" in result["copied"]
    assert len(result["copied"]) == len(_defaults()) - 3
    assert manual.read_fragment("masters_staff_list")

    again = manual_defaults.sync()
    assert again["copied"] == [] and again["replaced"] == []
    assert again["kept"] == ["orders_order_list.html"]


def test_old_version_with_windows_line_endings_is_replaced(site):
    """改行だけが CRLF の物も、手を入れていない物と見る (0.1.0 を Windows の git で取り出した置き場の形)。"""
    folder = site / "manual"
    folder.mkdir()
    name = OLD_NAMES[2]
    old = (FIXTURES / name).read_bytes()
    assert b"\r\n" not in old
    (folder / name).write_bytes(old.replace(b"\n", b"\r\n"))

    result = manual_defaults.sync()
    assert name in result["replaced"]
    assert (folder / name).read_bytes() == _standard(name)


def test_an_old_version_under_another_name_is_kept(site):
    """印は断片の名前ごとに見る。別の画面の前の版の中身が入っていても、手を入れていない物とは見ない。"""
    folder = site / "manual"
    folder.mkdir()
    data = (FIXTURES / OLD_NAMES[0]).read_bytes()
    (folder / "orders_order_list.html").write_bytes(data)

    result = manual_defaults.sync()
    assert "orders_order_list.html" in result["kept"]
    assert (folder / "orders_order_list.html").read_bytes() == data


def test_makes_the_folder_when_the_site_has_none(site):
    result = manual_defaults.sync()
    assert (site / "manual").is_dir()
    assert len(result["copied"]) == len(_defaults())
    assert result["replaced"] == [] and result["kept"] == []


def test_site_only_fragments_are_left_alone(site):
    """標準の組に無い名前の断片 (会社が足した物) は、数えず、触らない。"""
    folder = site / "manual"
    folder.mkdir()
    (folder / "company_only.html").write_text("<p>会社だけの画面</p>", encoding="utf-8")
    result = manual_defaults.sync()
    assert "company_only.html" not in result["copied"] + result["replaced"] + result["kept"]
    assert (folder / "company_only.html").read_text(encoding="utf-8") == "<p>会社だけの画面</p>"


def test_history_file_shape():
    """前の版の印の一覧: 1 行が「<断片の名前> <sha256 の 16 進 64 字>」。名前と印の順に並び、同じ行が 2 回無い。"""
    path = manual_defaults.history_path()
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert lines == sorted(set(lines))
    for line in lines:
        assert re.fullmatch(r"[a-z0-9_]+\.html [0-9a-f]{64}", line), line
    names = {line.split(" ")[0] for line in lines}
    # 0.1.0 の標準は 160 枚。それより前からある名前が全部載っている
    assert len(names) >= 160
    assert set(OLD_NAMES) <= names


def test_command_prints_the_three_counts(site):
    folder = site / "manual"
    folder.mkdir()
    (folder / OLD_NAMES[0]).write_bytes((FIXTURES / OLD_NAMES[0]).read_bytes())
    (folder / "orders_order_list.html").write_text("<p>自社で直した説明</p>", encoding="utf-8")

    out = io.StringIO()
    call_command("seizou_manual_defaults", stdout=out)
    text = out.getvalue()
    assert f"写しました: {len(_defaults()) - 2} 件" in text
    assert "新しい標準に替えました: 1 件" in text
    assert "自社で直した断片: 1 件" in text and "替えていません" in text
    assert "orders_order_list.html" not in text

    (folder / OLD_NAMES[1]).write_bytes((FIXTURES / OLD_NAMES[1]).read_bytes())
    out = io.StringIO()
    call_command("seizou_manual_defaults", "--list", stdout=out)
    text = out.getvalue()
    assert "写した物はありません" in text
    assert "新しい標準に替えました: 1 件" in text
    assert OLD_NAMES[1] in text
    assert "orders_order_list.html" in text


def test_command_says_so_when_everything_is_current(site):
    manual_defaults.sync()
    out = io.StringIO()
    call_command("seizou_manual_defaults", stdout=out)
    text = out.getvalue()
    assert "写した物はありません" in text
    assert "替えました" not in text
    assert "自社で直した断片" not in text


def test_command_stops_with_a_sentence_without_a_site(settings):
    from django.core.management.base import CommandError

    del settings.BASE_DIR
    with pytest.raises(CommandError) as caught:
        call_command("seizou_manual_defaults", stdout=io.StringIO())
    assert "置き場" in str(caught.value)


def test_upgrade_steps_name_the_command():
    text = (Path(__file__).resolve().parent.parent / "docs" / "operations.md").read_text(encoding="utf-8")
    section = text.split("## 版を上げる手順", 1)[1].split("\n## ", 1)[0]
    assert "manage.py seizou_manual_defaults" in section
    # Windows に無い cp -n の写し方を、手順に残さない
    assert "cp -n" not in section
    # 手を入れていない断片を替える事と、直した断片を替えない事を書く
    assert "新しい標準に替え" in section and "替えません" in section


def test_generator_uses_the_same_hash_and_only_the_standard_set():
    """一覧を作る道具 (scripts/manual_history.py) は、命令と同じ作り方の印を、標準の組だけから作る (作り物の会社の種は入れない)。"""
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("manual_history", root / "scripts" / "manual_history.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sample = b"<section>\r\n<h2>x</h2>\r\n</section>\r\n"
    assert module.content_hash(sample) == manual_defaults.content_hash(sample)
    assert manual_defaults.content_hash(sample) == manual_defaults.content_hash(sample.replace(b"\r\n", b"\n"))
    assert module.SOURCE == "src/seizou/core/manual_default"
    assert "examples" not in module.SOURCE
    assert module.TARGET.name == manual_defaults.history_path().name
    assert module.MIN_BYTES >= 100
