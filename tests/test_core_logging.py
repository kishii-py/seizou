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
