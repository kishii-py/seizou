"""R13: 数と金額を変える処理は、部品の services の関数を通す。画面から表に直接書かない。

決まりの検査 (seizou.core.rules.check) を見本のファイルに直に当てる。Django の設定に頼らない。
"""

from pathlib import Path

from seizou.core.rules import check

FIXTURES = Path(__file__).parent / "fixtures" / "rules" / "r13"
RULES_MD = Path(__file__).parent.parent / "RULES.md"

WRITE_LINE = "def act(Item):\n    Item.objects.create(name='作り物')\n"


def _line_of(path: Path, text: str) -> int:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if text in line:
            return number
    raise AssertionError(f"{path} に {text} が無い")


def _rules_md_text(code: str) -> str:
    for line in RULES_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {code} |"):
            return line.split("|")[2].strip().replace("`", "")
    raise AssertionError(f"RULES.md に {code} の行が無い")


def _r13(result):
    return [(v.path, v.line) for v in result.violations if v.code == "R13"]


def test_bad_examples_report_direct_writes():
    bad = FIXTURES / "bad"
    views = bad / "views.py"
    forms = bad / "forms.py"
    tags = bad / "templatetags" / "tags.py"
    command = bad / "management" / "commands" / "load.py"
    result = check([bad])

    expected = [
        (str(forms), _line_of(forms, "self.instance.save()")),
        (str(command), _line_of(command, "Item.objects.bulk_create(")),
        (str(tags), _line_of(tags, "Item.objects.get_or_create(")),
        (str(views), _line_of(views, "Item.objects.create(")),
        (str(views), _line_of(views, "form.save()")),
        (str(views), _line_of(views, "obj.delete()")),
        (str(views), _line_of(views, '.update(status="closed")')),
    ]
    assert [(v.path, v.line) for v in result.violations] == sorted(expected)
    assert {v.code for v in result.violations} == {"R13"}
    assert result.scanned == 4
    assert result.silenced == 0


def test_good_examples_have_no_violation():
    result = check([FIXTURES / "good"])

    assert result.violations == ()
    assert result.scanned == 2


def test_services_and_models_are_out_of_scope(tmp_path):
    for name in ("services.py", "models.py", "selectors.py"):
        (tmp_path / name).write_text(WRITE_LINE, encoding="utf-8")
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0002_data.py").write_text(WRITE_LINE, encoding="utf-8")
    views = tmp_path / "views.py"
    views.write_text(WRITE_LINE, encoding="utf-8")

    # migrations の中のファイルは、直に渡しても見ない。
    result = check([tmp_path, migrations / "0002_data.py"])

    assert _r13(result) == [(str(views), 2)]
    assert result.scanned == 5


def test_dict_update_is_not_reported(tmp_path):
    views = tmp_path / "views.py"
    views.write_text(
        "def screen(request, kwargs):\n"
        "    context = {}\n"
        "    context.update({'screen_name': '品目'})\n"
        "    kwargs.update(page=1)\n"
        "    request.session.update({'last': 1})\n"
        "    return context\n",
        encoding="utf-8",
    )

    assert _r13(check([views])) == []


def test_positional_save_and_delete_are_not_reported(tmp_path):
    views = tmp_path / "views.py"
    views.write_text(
        "def screen(request, cache, storage, image, buffer, content):\n"
        "    cache.delete('item_count')\n"
        "    storage.save('drawings/a.png', content)\n"
        "    image.save(buffer, 'PNG')\n"
        "    return storage.delete('drawings/a.png')\n",
        encoding="utf-8",
    )

    assert _r13(check([views])) == []


def test_ignore_mark_silences_r13(tmp_path):
    views = tmp_path / "views.py"
    views.write_text(
        "def screen(request, obj, other):\n"
        "    obj.delete()  # seizou: ignore[R13] 作り物の見本。番号つきの印\n"
        "    other.delete()  # seizou: ignore[R14] 番号が違う\n",
        encoding="utf-8",
    )

    result = check([views])

    assert result.silenced == 1
    assert _r13(result) == [(str(views), 3)]


def test_message_is_the_rules_md_text():
    result = check([FIXTURES / "bad"])

    assert {v.message for v in result.violations} == {_rules_md_text("R13")}
    assert all("services" in v.fix for v in result.violations)
