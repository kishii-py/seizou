"""記録の行き先 (seizou.core.logging_conf・会社の settings.py の LOGGING) のテスト。

意図: 本番 (DEBUG=0) の 500 の誤りと seizou の記録が stderr (gunicorn → journald) に 1 回ずつ出ることと、
settings.py が読む module が Django を読まないことを、試験で崩れないようにするため。
作り物の設定と作り物の view だけを使う。記録の行き先は stderr の代わりに StringIO へ向ける (caplog は LOGGING を
直していなくても通るため)。試験の後に Django の既定の記録の形 (django.utils.log.DEFAULT_LOGGING) を当て直し、
seizou の logger を元の形 (handler なし・親へ渡す) に戻して、ほかの試験の caplog を壊さない。
"""

import ast
import io
import logging
import logging.config
import re
import string
from pathlib import Path

import pytest
from django.test import Client
from django.urls import path
from django.utils.log import DEFAULT_LOGGING

import seizou.scaffold
from seizou.core import logging_conf

SOURCE = Path(logging_conf.__file__)
TEMPLATE = Path(seizou.scaffold.__file__).parent / "settings.py.tmpl"
#: 作り物の view が投げる例外の字 (記録とメールの本文の比べに使う)
BOOM_TEXT = "作り物の誤りの字"
#: 記録の 1 行の頭 (時刻と +0900 の形の時差)
LINE_HEAD = r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[+-]\d{4} "


class FakeBoomError(RuntimeError):
    """作り物の view が投げる例外 (class の名前が記録に出ることを見る)。"""


def boom(request):
    raise FakeBoomError(BOOM_TEXT)


#: 試験の中だけの urlconf (settings.ROOT_URLCONF でこの module を指す)
urlpatterns = [path("boom/", boom)]


def rendered_settings() -> str:
    """雛形の settings.py を、seizou new と同じ string.Template で描いた字。"""
    return string.Template(TEMPLATE.read_text(encoding="utf-8")).safe_substitute(id="t", seizou_items="")


def top_level_imports(tree: ast.Module) -> list[str]:
    """module の頭で読む module の名前 (関数と class の中の遅い import は数えない)。"""
    names = []
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
        else:
            pending.extend(child for child in ast.iter_child_nodes(node) if isinstance(child, ast.stmt))
    return names


@pytest.fixture
def stream():
    """default_logging() を、stderr の handler だけ StringIO へ向けて当てる。試験の後に Django の既定の形へ戻す。"""
    buffer = io.StringIO()
    config = logging_conf.default_logging()
    config["handlers"]["stderr"]["stream"] = buffer
    logging.config.dictConfig(config)
    yield buffer
    logging.config.dictConfig(DEFAULT_LOGGING)
    # DEFAULT_LOGGING は seizou の logger を名指ししないので、dictConfig は handler と propagate を戻さない。手で戻す
    seizou_logger = logging.getLogger("seizou")
    seizou_logger.handlers.clear()
    seizou_logger.setLevel(logging.NOTSET)
    seizou_logger.propagate = True


# ---------------------------------------------------------------- (1) LOGGING


def test_module_does_not_import_django_or_seizou_at_top():
    names = top_level_imports(ast.parse(SOURCE.read_text(encoding="utf-8")))
    heavy = [name for name in names if name.split(".")[0] in ("django", "seizou")]
    assert heavy == []


def test_scaffold_reads_default_logging_once():
    tree = ast.parse(rendered_settings())
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "seizou.core.logging_conf"
        and [alias.name for alias in node.names] == ["default_logging"]
    ]
    assigns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "LOGGING" for t in node.targets)
    ]
    assert len(imports) == 1
    assert len(assigns) == 1
    value = assigns[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "default_logging"
    assert value.args == [] and value.keywords == []


def test_default_logging_returns_new_dict_each_time():
    first = logging_conf.default_logging()
    first["loggers"]["seizou"]["level"] = "DEBUG"
    assert logging_conf.default_logging()["loggers"]["seizou"]["level"] == "INFO"
    assert list(logging_conf.default_logging()["handlers"]) == ["stderr"]


@pytest.mark.django_db
def test_500_and_seizou_lines_go_to_stderr_once(stream, settings):
    settings.DEBUG = False
    settings.ROOT_URLCONF = __name__

    response = Client(raise_request_exception=False).get("/boom/")
    logging.getLogger("seizou.core").info("作り物の記録")

    assert response.status_code == 500
    text = stream.getvalue()
    assert len(re.findall(LINE_HEAD + r"ERROR django\.request Internal Server Error: /boom/$", text, re.M)) == 1
    assert "FakeBoomError" in text
    assert len(re.findall(LINE_HEAD + r"INFO seizou\.core 作り物の記録$", text, re.M)) == 1


# ---------------------------------------------------------------- (5) 前の SECRET_KEY


def run_settings(folder: Path, monkeypatch, env_text: str) -> dict:
    """描いた雛形を一時の置き場の settings.py として exec し、名前の辞書を返す。

    .env の行は雛形の _read_env が環境変数に入れる (既にある環境変数が先)。入れた値が後の試験に残らないように、
    .env に書く名前は先に monkeypatch で控えてから消しておく (試験の後に元の値か「無い」に戻る)。
    """
    folder.mkdir()
    (folder / ".env").write_text(env_text, encoding="utf-8")
    for line in env_text.splitlines():
        name = line.split("=", 1)[0].strip()
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    path = folder / "settings.py"
    path.write_text(rendered_settings(), encoding="utf-8")
    namespace = {"__file__": str(path), "__name__": "fake_site_settings"}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return namespace


def test_scaffold_reads_secret_key_fallbacks_after_secret_key():
    tree = ast.parse(rendered_settings())
    assigns = {
        node.targets[0].id: (index, node)
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
    }
    count = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SECRET_KEY_FALLBACKS" for t in node.targets)
    )
    assert count == 1
    index, node = assigns["SECRET_KEY_FALLBACKS"]
    assert index > assigns["SECRET_KEY"][0]
    reads = [
        call
        for call in ast.walk(node.value)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "get"
        and ast.unparse(call.func.value) == "os.environ"
        and [ast.literal_eval(arg) for arg in call.args] == ["SECRET_KEY_FALLBACKS", ""]
    ]
    assert len(reads) == 1


def test_scaffold_secret_key_fallbacks_from_env(tmp_path, monkeypatch):
    empty = run_settings(tmp_path / "empty", monkeypatch, "SECRET_KEY=fake-site-key\nSECRET_KEY_FALLBACKS=\n")
    assert empty["SECRET_KEY_FALLBACKS"] == []
    assert empty["LOGGING"] == logging_conf.default_logging()

    two = run_settings(tmp_path / "two", monkeypatch, "SECRET_KEY=fake-site-key\nSECRET_KEY_FALLBACKS=fake-old-1, fake-old-2\n")
    assert two["SECRET_KEY_FALLBACKS"] == ["fake-old-1", "fake-old-2"]
