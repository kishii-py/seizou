"""Django の起動の検査 (system check。manage.py check・runserver・migrate が回し、seizou_doctor の「Django の起動の検査」も数える)。
書き方の決まりの検査 (seizou_check) とは別。

見る物: URL の木にある一覧の画面 (BaseListView の子) の columns の辞書の鍵と値と、ColumnSpec の誤り (知らない kind など)。
filters の宣言の誤り (FilterSpec が作れない宣言。書けない鍵・知らない kind・無い項目・when の無い flag など)。
"""

from __future__ import annotations

from django.core import checks as django_checks


def _column_name(item) -> str:
    """誤りの文に出す列の名前 (name)。無ければ要素の repr。"""
    if isinstance(item, dict):
        return str(item["name"]) if "name" in item else repr(item)
    if isinstance(item, (tuple, list)):
        return str(item[0]) if item else repr(item)
    return str(item)


def list_column_errors(view_classes) -> list:
    """渡された class のうち一覧の画面 (BaseListView の子) の columns を ColumnSpec で 1 度作り、誤りを Error で返す。

    seizou.E001 = 辞書に書けない鍵がある。seizou.E002 = sortable・total が真偽でない、か sort_key が空でない字でない。
    seizou.E003 = ColumnSpec が例外を投げた (知らない kind・name の無い辞書など)。1 要素に複数の誤りがあれば全部返す。
    """
    from seizou.core.views import BaseListView, ColumnSpec

    errors = []
    for view_class in view_classes:
        if not (isinstance(view_class, type) and issubclass(view_class, BaseListView)):
            continue
        model = getattr(view_class, "model", None)
        if model is None:
            continue
        where = f"{view_class.__module__}.{view_class.__name__}"
        for item in view_class.columns or ():
            name = _column_name(item)
            if isinstance(item, dict):
                unknown = [key for key in item if key not in ColumnSpec.KEYS]
                if unknown:
                    errors.append(
                        django_checks.Error(
                            f"{where} の columns の列 {name} に、書けない鍵 {'・'.join(map(repr, unknown))} があります。",
                            hint=f"辞書に書ける鍵は {' / '.join(ColumnSpec.KEYS)} です。字の間違いを直してください。",
                            obj=view_class,
                            id="seizou.E001",
                        )
                    )
                for key in ("sortable", "total"):
                    if key in item and not isinstance(item[key], bool):
                        errors.append(
                            django_checks.Error(
                                f"{where} の columns の列 {name} の {key} が真偽でありません ({item[key]!r})。",
                                hint=f"{key} は True か False で書きます (字の \"True\" や \"False\" は、どちらも真になります)。",
                                obj=view_class,
                                id="seizou.E002",
                            )
                        )
                if "sort_key" in item and not (isinstance(item["sort_key"], str) and item["sort_key"]):
                    errors.append(
                        django_checks.Error(
                            f"{where} の columns の列 {name} の sort_key が空か、字でありません ({item['sort_key']!r})。",
                            hint="sort_key は、同じ順になる表の項目か annotate の列の名前の字で書きます。並べないなら鍵ごと消します。",
                            obj=view_class,
                            id="seizou.E002",
                        )
                    )
            try:
                ColumnSpec(model, item)
            except Exception as error:  # noqa: BLE001 - どの誤りも起動の検査の文にする (起動は止めない)
                errors.append(
                    django_checks.Error(
                        f"{where} の columns の列 {name} を読めません ({type(error).__name__}: {error})。",
                        hint="列は字・(名前, 見出し) の組・辞書のどれかで書きます。kind は number / money / date のどれかです。",
                        obj=view_class,
                        id="seizou.E003",
                    )
                )
    return errors


def list_filter_errors(view_classes) -> list:
    """渡された class のうち一覧の画面 (BaseListView の子) の filters を FilterSpec で 1 度作り、誤りを Error で返す。

    seizou.E004 = FilterSpec が例外を投げた (1 つの絞りに 1 つ)。見るのは宣言 (filters) だけで、
    土台が自動で足す会社ごとの項目の絞りは見ない。
    """
    from seizou.core.views import BaseListView, FilterSpec

    errors = []
    for view_class in view_classes:
        if not (isinstance(view_class, type) and issubclass(view_class, BaseListView)):
            continue
        model = getattr(view_class, "model", None)
        if model is None:
            continue
        where = f"{view_class.__module__}.{view_class.__name__}"
        for item in view_class.filters or ():
            name = str(item["name"]) if isinstance(item, dict) and "name" in item else repr(item)
            try:
                FilterSpec(model, item)
            except Exception as error:  # noqa: BLE001 - どの誤りも起動の検査の文にする (起動は止めない)
                errors.append(
                    django_checks.Error(
                        f"{where} の filters の絞り {name} を読めません ({type(error).__name__}: {error})。",
                        hint=f"絞りは辞書で書きます。kind は {' / '.join(FilterSpec.KINDS)} のどれかです (書き方は FilterSpec の docstring)。",
                        obj=view_class,
                        id="seizou.E004",
                    )
                )
    return errors


def _url_view_classes(app_configs) -> list:
    """URL の木にある画面の class (同じ class は 1 回)。app_configs があれば、その app の画面だけ。"""
    from django.conf import settings
    from django.urls import get_resolver

    from seizou.core.screens import _walk

    if not getattr(settings, "ROOT_URLCONF", None):
        return []
    view_classes = list(dict.fromkeys(view_class for view_class, _url_name, _path in _walk(get_resolver().url_patterns)))
    if app_configs is not None:
        names = [config.name for config in app_configs]
        view_classes = [
            view_class
            for view_class in view_classes
            if any(view_class.__module__ == name or view_class.__module__.startswith(name + ".") for name in names)
        ]
    return view_classes


@django_checks.register()
def check_list_columns(app_configs=None, **kwargs):
    """URL の木にある一覧の画面の columns を見る (同じ class は 1 回)。app_configs があれば、その app の画面だけ。

    試験の中で作った画面は URL の木に無いので見ない (ほかの試験の run_checks() に漏れない)。
    """
    return list_column_errors(_url_view_classes(app_configs))


@django_checks.register()
def check_list_filters(app_configs=None, **kwargs):
    """URL の木にある一覧の画面の filters を見る (check_list_columns と同じ選び方。開発の記録 docs/nights/20261104/N122.md)。"""
    return list_filter_errors(_url_view_classes(app_configs))
