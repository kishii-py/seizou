"""決まりの検査 (決まりの検査の設計の決め。core の公開する物の設計の決めの seizou.core.rules.check)。

自社のアプリのコードを Python の ast で読み、書き方の決まりの違反の一覧を返す。
同じ検査を、開発の設定の起動のとき (check_at_startup) と CI (manage.py seizou_check) で回す。
check は Django の設定を読まない (DJANGO_SETTINGS_MODULE が無くても import と実行ができる)。

決まりを足すときは、決まりごとの module を作り、RULES の並びに足す。
"""

from __future__ import annotations

import importlib.util
import logging
import os
from collections.abc import Iterable
from pathlib import Path

from . import (
    r13_services_only,
    r14_no_raw_sql,
    r15_inherit_base_view,
    r16_no_core_model_change,
    r17_http_outside_atomic,
    r18_csrf_single_place,
    r19_no_dynamic_classnames,
    r22_reason_docstring,
)
from .engine import Result, Violation, run

__all__ = ["RULES", "Result", "Violation", "check", "check_at_startup", "default_paths", "last_result"]

logger = logging.getLogger(__name__)

#: 見る決まりの並び
RULES = (
    r13_services_only,
    r14_no_raw_sql,
    r15_inherit_base_view,
    r16_no_core_model_change,
    r17_http_outside_atomic,
    r18_csrf_single_place,
    r19_no_dynamic_classnames,
    r22_reason_docstring,
)

_last_result: Result | None = None


def check(paths: Iterable[str | os.PathLike]) -> Result:
    """パスの一覧 (ファイルかフォルダ) を検査し、違反の一覧と黙らせた数と走査した本数を返す。"""
    return run(paths, RULES)


def default_paths() -> list[Path]:
    """既定の検査の対象: 部品の置き場 (seizou の package) と、会社のプロジェクトの BASE_DIR。

    settings に BASE_DIR が無ければ、部品の置き場だけを返す。Django の設定を読む。
    """
    from django.conf import settings

    spec = importlib.util.find_spec("seizou")
    paths = [Path(location) for location in (spec.submodule_search_locations or [])]
    base_dir = getattr(settings, "BASE_DIR", None)
    if base_dir is not None:
        paths.append(Path(base_dir))
    return paths


def check_at_startup() -> Result | None:
    """開発の設定 (DEBUG が真) のときだけ検査し、結果を覚える。

    DEBUG が偽 (本番と pytest) では何もしない。検査そのものの誤りで起動を止めない。
    """
    global _last_result
    try:
        from django.conf import settings

        if not settings.DEBUG:
            return None
        _last_result = check(default_paths())
    except Exception:
        logger.exception("決まりの検査が誤りで止まりました。起動は続けます。")
        _last_result = None
    return _last_result


def last_result() -> Result | None:
    """起動のときに覚えた検査の結果。まだ走っていなければ None。"""
    return _last_result
