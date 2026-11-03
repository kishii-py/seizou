"""決まりの検査が使う名前の一覧。

土台の画面の名前と、上書きしてよい属性の名前と、部品の app の名前は、決まりの検査の中ではこの module にだけ書く。
seizou.core.views を import しない (Django の設定が無くても検査を動かすため)。
"""

#: seizou.core.views の土台の画面 (01 の 10.3)。R15 の検査が見る。
BASE_VIEWS = frozenset(
    {
        "BaseListView",
        "BaseDetailView",
        "BaseFormView",
        "BaseModalView",
        "BaseSelectView",
    }
)

#: 土台を継承した画面が上書きしてよい属性 (01 の 10.3。R15 の 2 文目)。
#: seizou.core.views の OVERRIDABLE と同じ並び (tests/test_r15_inherit_base_view.py が突き合わせる)。
OVERRIDABLE_ATTRS = (
    "model",
    "ordering",
    "columns",
    "search_fields",
    "tabs",
    "form_class",
    "service",
    "template_name",
    "get_queryset",
    "screen_name",
    "screen_help",
)

#: 部品の app の名前 (01 の 1 節の表)。R16 の検査 (夜の N9) が見る。
PART_APPS = (
    "core",
    "masters",
    "orders",
    "workorders",
    "progress",
    "shipping",
    "billing",
    "connect",
    "purchasing",
    "intake",
    "attachments",
    "assistant",
    "manuals",
    "bookmarks",
    "accounts",
)
