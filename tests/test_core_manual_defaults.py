"""版を上げた置き場に、標準の説明書の断片のうち無い物を写す命令 (manage.py seizou_manual_defaults)。

出所: 11-14 に、PyPI の 0.1.0 で作った置き場を今の版に上げる通しで見つけた事。置き場の manual/ は作った時の断片のままなので、
後の版で増えた画面の「使い方」が「この画面の説明書はまだありません。」になる。手引きの写し方 (cp -n) は Windows に無く、
pip で入れた版では標準の組の道も違う。
"""

import io
from pathlib import Path

import pytest
from django.core.management import call_command

from seizou.core import manual, manual_defaults


@pytest.fixture
def site(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    return tmp_path


def _defaults():
    return sorted(path.name for path in manual_defaults.default_dir().glob("*.html"))


def test_default_dir_is_inside_the_package():
    names = _defaults()
    assert "masters_staff_list.html" in names
    assert len(names) > 150


def test_copies_only_the_missing_fragments(site):
    folder = site / "manual"
    folder.mkdir()
    own = folder / "orders_order_list.html"
    own.write_text("<p>自社で直した説明</p>", encoding="utf-8")

    result = manual_defaults.copy_missing()

    assert own.read_text(encoding="utf-8") == "<p>自社で直した説明</p>"
    assert "orders_order_list.html" not in result["copied"]
    assert "masters_staff_list.html" in result["copied"]
    assert len(result["copied"]) == len(_defaults()) - 1
    assert result["different"] == ["orders_order_list.html"]
    assert (folder / "masters_staff_list.html").read_bytes() == (manual_defaults.default_dir() / "masters_staff_list.html").read_bytes()
    # 写した後は、説明書がその断片を読める
    assert manual.read_fragment("masters_staff_list")

    again = manual_defaults.copy_missing()
    assert again["copied"] == []
    assert again["different"] == ["orders_order_list.html"]


def test_makes_the_folder_when_the_site_has_none(site):
    result = manual_defaults.copy_missing()
    assert (site / "manual").is_dir()
    assert len(result["copied"]) == len(_defaults())
    assert result["different"] == []


def test_site_only_fragments_are_left_alone(site):
    """標準の組に無い名前の断片 (会社が足した物) は、数えず、触らない。"""
    folder = site / "manual"
    folder.mkdir()
    (folder / "company_only.html").write_text("<p>会社だけの画面</p>", encoding="utf-8")
    result = manual_defaults.copy_missing()
    assert "company_only.html" not in result["copied"] + result["different"]
    assert (folder / "company_only.html").read_text(encoding="utf-8") == "<p>会社だけの画面</p>"


def test_command_prints_the_counts_and_the_next_step(site):
    folder = site / "manual"
    folder.mkdir()
    (folder / "orders_order_list.html").write_text("<p>自社で直した説明</p>", encoding="utf-8")

    out = io.StringIO()
    call_command("seizou_manual_defaults", stdout=out)
    text = out.getvalue()
    assert f"{len(_defaults()) - 1} 件" in text
    assert "標準と字が違う物: 1 件" in text
    assert "消してから" in text
    assert "orders_order_list.html" not in text

    out = io.StringIO()
    call_command("seizou_manual_defaults", "--list", stdout=out)
    text = out.getvalue()
    assert "写した物はありません" in text
    assert "orders_order_list.html" in text


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
