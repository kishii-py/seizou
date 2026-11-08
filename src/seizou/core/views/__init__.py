"""画面の土台 (画面の土台と core の公開する物の設計の決め)。

部品の画面はここの Base*View を継承する (R15)。継承した画面が上書きしてよいのは
model・ordering・columns・search_fields・filters・presets・tabs・form_class・service・template_name・
get_queryset・screen_name・screen_help の 13 だけ (OVERRIDABLE)。

約束:
- 一覧: 検索 (q)・並び替え (sort)・ページ送り (page・per) ・会社ごとの項目の列・同じ条件の CSV (format=csv)。
  htmx の要求には行の部分 (<app>/_rows.html か core/_rows.html) だけを返す。ただしブラウザの戻る
  (htmx の HX-History-Restore-Request) には画面の全体を返す。部分と全体は同じ URL なので Vary: HX-Request を付ける。
  合計の行は、列が合計を選んだときだけ出す (BaseListView の docstring)。
  画面の全体を描くときも、行の部分は同じ順 (<app>/_rows.html → core/_rows.html) で選ぶ (core/list.html の include)。
- 詳細: 1 件の項目と、関係する物の一覧 (tabs = ((見出し, related_name), ...))。行の __str__ が FK を読むタブは、
  3 つ目に FK の名前の並びを書くと行と相手を 1 回で引く ((見出し, related_name, ("FK の名前", ...)))。
- 入力: form の値が通ったら、まず検査の層 (seizou.core.checks.run) を通し、それから service を呼ぶ。
  ポカよけの止めるは赤の知らせ、知らせるは黄の知らせと「このまま保存」(confirmed=1 で保存)、
  中心の決まり (RuleError) は赤の知らせ。入力の値は消さない。できたら緑の知らせと移動。
  検査の層に渡す data と service の引数は form が決める: form に service_kwargs() があればその辞書、
  無ければ cleaned_data (会社ごとの項目は ext= に分けて渡す)。検査の層の action は service の module と名前
  (例 seizou.shipping.services.ship → "shipping.ship")。
  新規登録の画面は、GET の値のうち form の欄の名前と同じ物を初期値にする (例 ?order_line=<pk>)。保存はいつもの検査と service を通る。
- 小窓: 見るだけと「詳細を見る」。template は <app>/_modal.html か core/_modal.html。
  会社ごとの項目 (一覧に出す物) と、tabs の関係する物も出す。htmx でない要求 (新しいタブ) は詳細へ移す。
- 画面の応答はブラウザに保存させない (Cache-Control: no-store。ログアウトの後の戻るで中身を見せない)。
- 候補の口: Tom Select の候補を 1 ページ (per_page 件) JSON で返す。
- rule_help: 赤の知らせの決まりの番号の「?」が開く説明 (RULES.md の行)。htmx の要求には小窓の中身、htmx でない要求
  (小窓の入力の赤い帯の「?」が開く新しいタブ) には画面の全体を返す。同じ URL なので Vary: HX-Request を付ける。

URL の名前の約束 (画面どうしの移動に使う。無ければ link を出さない):
  <app>:<model>_list / _detail / _create / _update / _modal

置き場 (11-08 に 1 つの views.py を分けた。読み込みの道 seizou.core.views.<名前> は前と同じ):
  base (OVERRIDABLE・知らせ・URL と値の道具・ScreenMixin・検索の揺れ)・list_specs (列と絞り)・lists (一覧と候補の口)・
  details (詳細と小窓とタブ)・forms (入力と欄の道具)・home (最初の画面と数字のカード)・rules (決まりの「?」)・
  misc (ログイン・鈴・小さい道具)。ここは名前を並べて読み直すだけで、中身を書かない。
  試験は seizou.core.views の名前を差し替える (monkeypatch) ので、差し替えられる名前 (dashboard_stats・RULES_MD) は
  使う側 (HomeView・rule_line) が呼ぶ時にこの package から読む。
  画面の app は ScreenMixin.screen_app が module の名前から決める (seizou.core.views.home のような views の中の module は core)。
"""

from .base import (
    CODE_NAMES,
    CODE_SUFFIXES,
    DATE_FIELDS,
    DEFAULT_PAGE_SIZE,
    IMAGE_SUFFIXES,
    INVALID_LABELS_SHOWN,
    MASTER_APPS,
    NOTICE_EVENT,
    NUMERIC_FIELDS,
    Notice,
    OVERRIDABLE,
    PAGE_SIZES,
    REFUSAL_HEAD_BY_SERVICE,
    REFUSAL_HEAD_CANCEL,
    REFUSAL_HEAD_DELETE,
    REFUSAL_HEAD_SAVE,
    SEARCH_DASHES,
    SEARCH_VARIANTS_MAX,
    ScreenMixin,
    _dash_variant,
    _kana_switch_variants,
    add_notice_trigger,
    display_value,
    file_info,
    file_url_for,
    hidden_detail_fields,
    invalid_field_labels,
    invalid_form_message,
    is_master,
    is_negative,
    load_model,
    lookup_label,
    model_url,
    refusal_head,
    screen_name_of,
    search_q,
    search_variants,
    url_or_none,
)
from .list_specs import (
    ColumnSpec,
    FilterSpec,
    _takes_values,
    field_at,
)
from .lists import (
    BaseListView,
    BaseSelectView,
)
from .details import (
    BaseDetailView,
    BaseModalView,
    _tab_specs,
    related_tab,
)
from .forms import (
    BaseFormView,
    _accepts_instance,
    use_clearable_file_widget,
    use_decimal_input,
    use_label_required,
    widget_css,
    with_saved_note,
)
from .home import (
    COMPARE_HELP,
    HomeView,
    count_or_none,
    dashboard_stats,
    recent_orders,
)
from .rules import (
    RULES_MD,
    RULE_CODE,
    RULE_SETTING_EFFECTS,
    rule_help,
    rule_line,
    rule_setting_line,
)
from .misc import (
    BELL_NOT_HTMX,
    RememberLoginView,
    bell,
    decimal_or_none,
)

# 元の module の頭で読んでいた module のうち、試験が属性で差し替える物 (views.checks.setting_key_for・views.timezone.localdate)
from django.utils import timezone

from seizou.core import checks

__all__ = [
    "checks",
    "timezone",
    "CODE_NAMES",
    "CODE_SUFFIXES",
    "DATE_FIELDS",
    "DEFAULT_PAGE_SIZE",
    "IMAGE_SUFFIXES",
    "INVALID_LABELS_SHOWN",
    "MASTER_APPS",
    "NOTICE_EVENT",
    "NUMERIC_FIELDS",
    "Notice",
    "OVERRIDABLE",
    "PAGE_SIZES",
    "REFUSAL_HEAD_BY_SERVICE",
    "REFUSAL_HEAD_CANCEL",
    "REFUSAL_HEAD_DELETE",
    "REFUSAL_HEAD_SAVE",
    "SEARCH_DASHES",
    "SEARCH_VARIANTS_MAX",
    "ScreenMixin",
    "_dash_variant",
    "_kana_switch_variants",
    "add_notice_trigger",
    "display_value",
    "file_info",
    "file_url_for",
    "hidden_detail_fields",
    "invalid_field_labels",
    "invalid_form_message",
    "is_master",
    "is_negative",
    "load_model",
    "lookup_label",
    "model_url",
    "refusal_head",
    "screen_name_of",
    "search_q",
    "search_variants",
    "url_or_none",
    "ColumnSpec",
    "FilterSpec",
    "_takes_values",
    "field_at",
    "BaseListView",
    "BaseSelectView",
    "BaseDetailView",
    "BaseModalView",
    "_tab_specs",
    "related_tab",
    "BaseFormView",
    "_accepts_instance",
    "use_clearable_file_widget",
    "use_decimal_input",
    "use_label_required",
    "widget_css",
    "with_saved_note",
    "COMPARE_HELP",
    "HomeView",
    "count_or_none",
    "dashboard_stats",
    "recent_orders",
    "RULES_MD",
    "RULE_CODE",
    "RULE_SETTING_EFFECTS",
    "rule_help",
    "rule_line",
    "rule_setting_line",
    "BELL_NOT_HTMX",
    "RememberLoginView",
    "bell",
    "decimal_or_none",
]
