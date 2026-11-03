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
    "stock",
    "feedback",
)

#: 古い版 (daisyUI 2〜4) の class 名で、今の版 (5) に無い物。R19 の検査が見る。
#: 今の版にある名前 (alert-info・menu-title など) と、一般の語 (placeholder・online・offline) は入れない。
OLD_CLASS_NAMES = (
    # ボタンの組 (今は join・join-item)
    "btn-group",
    "btn-group-vertical",
    "btn-group-horizontal",
    # 入力の組 (今は join)
    "input-group",
    "input-group-vertical",
    "input-group-xs",
    "input-group-sm",
    "input-group-md",
    "input-group-lg",
    # form の枠 (今は fieldset・label)
    "form-control",
    "label-text",
    "label-text-alt",
    # 枠線の印 (今は既定で枠線)
    "input-bordered",
    "select-bordered",
    "textarea-bordered",
    "file-input-bordered",
    # カード (今は card-sm・card-border)
    "card-compact",
    "card-bordered",
    "card-normal",
    # タブ (今は tabs-box・tabs-lift・tabs-border・tabs-xs〜tabs-lg)
    "tabs-boxed",
    "tab-lifted",
    "tab-bordered",
    "tab-xs",
    "tab-sm",
    "tab-md",
    "tab-lg",
    # 下の帯 (今は dock)
    "btm-nav",
    "btm-nav-xs",
    "btm-nav-sm",
    "btm-nav-md",
    "btm-nav-lg",
    # 見本の枠 (今は無い)
    "artboard",
    "artboard-demo",
    "artboard-horizontal",
    "phone-1",
    "phone-2",
    "phone-3",
    "phone-4",
    "phone-5",
    "phone-6",
    # 表とメニュー (今は table-xs〜table-lg・menu-xs〜menu-lg)
    "table-compact",
    "table-normal",
    "menu-compact",
    "menu-normal",
    # 角と引き出し (今は rounded-field・rounded-selector・lg:drawer-open)
    "rounded-btn",
    "rounded-badge",
    "drawer-mobile",
)
