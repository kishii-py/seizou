"""画面の土台の共通の道具 (core.views の package の 1 つ目)。

上書きしてよい属性の並び (OVERRIDABLE)・知らせの字 (Notice・断りの頭・add_notice_trigger)・URL と値の字の道具・
ScreenMixin (全部の画面の親)・検索の揺れ (search_variants・search_q・lookup_label)・load_model。
ほかの module はここから読む。ここはほかの module を読まない。
"""

from __future__ import annotations

import json
import unicodedata
from decimal import Decimal
from importlib import import_module

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import Q
from django.urls import NoReverseMatch, Resolver404, get_script_prefix, resolve, reverse
from django.utils.cache import add_never_cache_headers

from seizou.core import access
from seizou.core import fields as company_fields
from seizou.core.templatetags.seizou_tags import rule_code


#: 継承した画面が上書きしてよい属性 (R15 の 2 文目。決まりの検査が見る)
OVERRIDABLE = (
    "model",
    "ordering",
    "columns",
    "search_fields",
    "filters",
    "presets",
    "tabs",
    "form_class",
    "service",
    "template_name",
    "get_queryset",
    "screen_name",
    "screen_help",
)

PAGE_SIZES = (20, 50, 100)
DEFAULT_PAGE_SIZE = 50
MASTER_APPS = ("masters",)

#: 一覧で右寄せにする項目の型 (IntegerField は Positive… と Small… と Big… を含む)。合計は列が選んだときだけ
NUMERIC_FIELDS = (models.DecimalField, models.IntegerField, models.FloatField)
#: 一覧で折り返さず、等幅の数字で出す項目の型 (DateTimeField は DateField の子。日時は別に見る)
DATE_FIELDS = (models.DateField,)
#: 一覧で等幅の字で出す識別子 (品番・番号・コード) の項目の名前と、名前の末尾
CODE_NAMES = ("code",)
CODE_SUFFIXES = ("_no", "_code")
#: 詳細で絵として出すファイルの拡張子
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


class Notice:
    """赤の知らせの中身 (例外でない物を同じ形で描くため)。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message


#: 入力の土台が欄の検査で止まった時の赤の知らせ (BaseFormView.form_invalid) の 1 文目。
#: 決め 11-06「保存されなかったら理由が何でも出す。欄の誤りなら 1 (欄の下の赤い字) と 2 (止まった知らせ) の両方」。
#: service の関数の名前で選ぶ (開発の記録 docs/nights/20261105/N156.md の表のうち入力の土台に当たる行)。字は core.manual.REFUSAL_HEADS の組の中だけ。
#: 名前が cancel_ で始まる物は「取り消せません。」、BaseFormView.DELETE_SERVICE_PREFIXES で始まる物は「削除できません。」、
#: 下の表の名前はその字、ほかの全部 (service の無い画面も) は「保存できません。」。
REFUSAL_HEAD_SAVE = "保存できません。"
REFUSAL_HEAD_CANCEL = "取り消せません。"
REFUSAL_HEAD_DELETE = "削除できません。"
REFUSAL_HEAD_BY_SERVICE = {
    "create_invoice": "発行できません。",
    "create_closing_invoices": "発行できません。",
    "create_delivery_note": "発行できません。",
    "issue_workorder": "発行できません。",
    "issue_account": "発行できません。",
    "record_payment": "記録できません。",
    "record_outside_payment": "記録できません。",
    "ship_lines": "出荷できません。",
    "receive": "受け入れられません。",
    "accept": "受け入れられません。",
    "accept_extracted": "受け入れられません。",
    "reject": "断れません。",
    "accept_with_conditions": "受け入れられません。",
    "hold": "保留できません。",
    "post_chat_message": "送れません。",
    "create_self_invoice": "発行できません。",
    "answer_self_invoice": "送れません。",
    "accept_self_invoice_proposals": "送れません。",
}
#: 欄の誤りの知らせに名前を並べる欄の数 (これより多い分は「ほか n つ」)
INVALID_LABELS_SHOWN = 3


def refusal_head(service_name: str, delete_prefixes: tuple[str, ...]) -> str:
    """service の関数の名前から、止まった知らせの 1 文目 (「<操作>できません。」) を選ぶ。空の名前は「保存できません。」。"""
    if service_name.startswith("cancel_"):
        return REFUSAL_HEAD_CANCEL
    if service_name.startswith(delete_prefixes):
        return REFUSAL_HEAD_DELETE
    return REFUSAL_HEAD_BY_SERVICE.get(service_name, REFUSAL_HEAD_SAVE)


def invalid_field_labels(form) -> str:
    """誤りのある見える欄の見出しを、form の欄の順に「」でくくって並べた字 (3 つまで。4 つ目からは「ほか n つ」)。無ければ空の字。

    隠しの欄と、見出しの空の欄は数えない (明細の行の誤りは行の中に出るので、ここには入らない)。
    """
    labels = [
        str(field.label)
        for name, field in form.fields.items()
        if name in form.errors and field.label and not form[name].is_hidden
    ]
    text = "".join(f"「{label}」" for label in labels[:INVALID_LABELS_SHOWN])
    if len(labels) > INVALID_LABELS_SHOWN:
        text += f"ほか {len(labels) - INVALID_LABELS_SHOWN} つ"
    return text


def invalid_form_message(form, head: str) -> str:
    """欄の検査で止まった時の赤の知らせの文。1 文目は head (refusal_head が選ぶ字)。

    欄の外の誤り (form.non_field_errors) が無い時は、誤りのある欄の名前と直し方。名前が 1 つも取れない時 (明細の行か隠しの欄だけ) は
    「入れた値に誤りがあります。…」。欄の外の誤りがある時は、その字を " " でつないで続け、名前の取れる欄の誤りもあれば
    「「品名」の欄も直してください。」を足す (欄の外の誤りの帯はこの知らせに移した。決め 11-06)。
    """
    labels = invalid_field_labels(form)
    errors = " ".join(str(error) for error in form.non_field_errors())
    if errors:
        return f"{head}{errors} {labels}の欄も直してください。" if labels else f"{head}{errors}"
    if labels:
        return f"{head}{labels}の欄に誤りがあります。欄の下の赤い字のとおりに直してから、もう一度押してください。"
    return f"{head}入れた値に誤りがあります。赤い字のとおりに直してから、もう一度押してください。"


#: 画面の一部だけを差し替える応答で、止まった知らせ (赤) と確かめ (黄) を右上に出す合図の名前 (core/static/js/seizou-modal.js が受ける。決め 11-07)
NOTICE_EVENT = "seizou:notice"


def add_notice_trigger(response, notice=None, warnings=()):
    """応答に、止まった知らせと確かめを右上の知らせに出す合図 (見出し HX-Trigger の seizou:notice) を付けて返す (決め 11-07)。

    画面の一部だけを差し替える応答では、右上の知らせ (core/_toasts.html) が差し替えに入らない。合図を受けた seizou-modal.js が、
    base.html の template#seizou-toast-template の赤 (stop) と黄 (check) の箱を写す (見た目と印は _toasts.html と同じ。段は CLAUDE.md の「知らせの強さ」)。
    中身は {"items": [...]}。1 つは level (stop / check)・message (文の全部。1 文目の太字は JS が最初の「。」で分ける)・
    rule (決まりの番号。R と 2 桁でなければ空)・help (赤で番号がある時だけ決まりの説明の道。ほかは空)。並びは赤が先、黄は warnings の順。
    notice も warnings も無ければ何も付けない。鍵に target を使わない (htmx は detail.target を合図を出す要素と読むため)。
    JSON は既定の ensure_ascii のまま (見出しは latin-1 の字だけ。saved_in_modal と同じ訳)。
    """
    items = []
    if notice is not None:
        rule = rule_code(notice.code)
        help_url = (url_or_none("core:rule_help", rule) or "") if rule else ""
        items.append({"level": "stop", "message": str(notice.message), "rule": rule, "help": help_url})
    for warning in warnings or ():
        items.append({"level": "check", "message": str(warning.message), "rule": rule_code(warning.code), "help": ""})
    if items:
        response["HX-Trigger"] = json.dumps({NOTICE_EVENT: {"items": items}})
    return response


def url_or_none(name: str, *args) -> str | None:
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def model_url(model, kind: str, *args) -> str | None:
    meta = model._meta
    return url_or_none(f"{meta.app_label}:{meta.model_name}_{kind}", *args)


def screen_name_of(url: str | None) -> str:
    """URL の画面の名前 (画面の class の screen_name)。詳細のパンくずの 1 段目に一覧の名前を出すため (開発の記録 docs/nights/20261105/N163.md)。

    URL が無いか、引けないか、画面の class が名前を持たなければ空。空なら詳細は「一覧に戻る」を出す。
    reverse は script の頭 (置き場の道) を付けた道を返し、resolve はそれを受けないので、頭を外してから引く。
    """
    if not url:
        return ""
    prefix = get_script_prefix()
    if prefix != "/" and url.startswith(prefix):
        url = "/" + url[len(prefix):]
    try:
        match = resolve(url)
    except Resolver404:
        return ""
    return getattr(getattr(match.func, "view_class", None), "screen_name", "") or ""


def is_master(value) -> bool:
    meta = getattr(value, "_meta", None)
    return meta is not None and meta.app_label in MASTER_APPS


def display_value(obj, name: str):
    """項目の表示の値。choices は表示名、FK は相手、拡張の表は ext__<列> で読む。"""
    if name.startswith(company_fields.EXT_PREFIX):
        return company_fields.ext_value(obj, name[len(company_fields.EXT_PREFIX):])
    getter = getattr(obj, f"get_{name}_display", None)
    if callable(getter):
        return getter()
    value = obj
    for part in name.split("__"):
        value = getattr(value, part, None)
        if value is None:
            return None
    return value() if callable(value) else value


def is_negative(value) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) and value < 0


def file_info(value, url: str | None = None) -> dict | None:
    """FileField の値から link の材料 (url・名前・絵かどうか) を作る。空なら None。

    url を渡せばその URL を使う (ログインした人にだけ返す口など)。無ければ storage の URL。
    """
    name = getattr(value, "name", "") or ""
    if not name:
        return None
    if url is None:
        try:
            url = value.url
        except ValueError:
            return None
    return {"url": url, "name": name.rsplit("/", 1)[-1], "is_image": name.lower().endswith(IMAGE_SUFFIXES)}


def file_url_for(obj, field_name: str) -> str | None:
    """model の file_url(field_name) があれば、その答え (空でない字) を返す。無ければ None (storage の URL を使う)。

    図面や添付のように、ログインした人にだけ返す口を持つ model が、core のテンプレートを触らずに
    詳細の画面のファイルの URL を差し替えるための約束。
    """
    hook = getattr(obj, "file_url", None)
    if not callable(hook):
        return None
    return hook(field_name) or None


def hidden_detail_fields(obj) -> frozenset:
    """詳細と小窓に出さない項目の名前。model の detail_hidden_fields (名前の並び) があればそれ、無ければ空。

    表示用の絵や変換の誤りの文のように、仕組みのために持つ列 (入力の画面に無い物) と、
    取引先の支払の月のように、別の項目の字に含まれて二重になる列を、
    利用者の目に触れる一覧から外すための約束。入力の form と services には関係しない。
    """
    return frozenset(getattr(obj, "detail_hidden_fields", ()) or ())


class ScreenMixin(LoginRequiredMixin):
    """画面の名前と 1 文。画面の目録 (manage.py seizou_screens)・説明書・MCP の一覧が読む。

    1 文 (screen_help) は画面の題の下には出さない (決め 11-03 14:1x: 画面はシンプルに。「?」で説明書の断片を見る)。
    """

    screen_name: str = ""
    screen_help: str = ""
    screen_icon: str = ""  # 題の横の絵の名前 (seizou_icons)。空なら app の絵。core 自身の画面 (検索など) が使う
    opens_by_department = True  # 一覧と入力と盤は部署で開けるかを見る。詳細・小窓・候補の口は見ない

    @classmethod
    def screen_app(cls) -> str:
        """画面の部品の app の名前 (module の名前から。seizou.masters.views → masters)。見出しの絵と画面の目録 (core.screens.all_screens) に使う。

        views が package の app (seizou.core.views.home など) は、views の 1 つ前の名前 (core)。
        """
        parts = cls.__module__.split(".")
        if len(parts) >= 3 and parts[-2] == "views":
            return parts[-3]
        return parts[-2] if len(parts) >= 2 else parts[-1]

    def dispatch(self, request, *args, **kwargs):
        denied = access.screen_denied(request, self.screen_app(), by_department=self.opens_by_department)
        if denied is not None:
            add_never_cache_headers(denied)
            return denied
        response = super().dispatch(request, *args, **kwargs)
        # ログアウトの後にブラウザの戻るで画面の中身が見えないように、画面の応答は保存させない (never_cache と同じ)。
        # 静的なファイル (static) は画面でないので、ここを通らない。
        add_never_cache_headers(response)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["screen_name"] = self.screen_name
        context["screen_help"] = self.screen_help
        context["screen_app"] = self.screen_app()
        context["screen_icon"] = self.screen_icon
        context["screen_anchor"] = (getattr(self.request.resolver_match, "view_name", "") or "").replace(":", "_") if hasattr(self, "request") else ""
        return context


#: 検索で - と同じに扱うダッシュ (‐ ‑ ‒ – — ― −。NFKC で - にならない物。全角の「－」は NFKC で - になる)
SEARCH_DASHES = "‐‑‒–—―−"
#: 1 語の揺れの数の上限 (1 語の Q は lookups の数 × この数の icontains の OR まで)
SEARCH_VARIANTS_MAX = 24


def _dash_variant(text: str) -> str:
    """SEARCH_DASHES の字を全部 - にし、長音の「ー」は直前の字が ASCII の英数字のときだけ - にした字
    (品番の「WKー1001」を「WK-1001」に当てる。「メーター」の「ー」は変えない)。"""
    chars = []
    for index, ch in enumerate(text):
        prev = text[index - 1] if index else ""
        if ch in SEARCH_DASHES or (ch == "ー" and prev.isascii() and prev.isalnum()):
            chars.append("-")
        else:
            chars.append(ch)
    return "".join(chars)


def _kana_switch_variants(text: str) -> list[str]:
    """1 語の中の仮名の 1 か所で字の種類を切り替えた揺れ (「ニッケルメッキ」から「ニッケルめっき」を作る。作者の決め)。

    隣り合う 2 字がどちらも仮名 (ひらがな・カタカナ・長音の「ー」) の所 k ごとに、カタカナ (text[:k]) + ひらがな (text[k:]) と
    ひらがな (text[:k]) + カタカナ (text[k:]) の 2 つを、左の k から順に返す。仮名の外の字は変えない。2 か所以上の切り替えは作らない。
    """

    def is_kana(ch: str) -> bool:
        return "ぁ" <= ch <= "ゖ" or "ァ" <= ch <= "ヶ" or ch == "ー"

    def kata(part: str) -> str:
        return "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in part)

    def hira(part: str) -> str:
        return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in part)

    variants = []
    for k in range(1, len(text)):
        if is_kana(text[k - 1]) and is_kana(text[k]):
            variants += [kata(text[:k]) + hira(text[k:]), hira(text[:k]) + kata(text[k:])]
    return variants


def search_variants(term: str) -> list[str]:
    """検索の語の揺れ。全角と半角 (NFKC)・ひらがなとカタカナを同じに扱う (作者の決め)。

    返す物は重ねない並び (打った字・NFKC・長音とダッシュ (英数字の後の「ー」・‐・− など) を - にした形・カタカナ・ひらがな・
    1 語の中の仮名の 1 か所で字の種類を切り替えた形)。DB の側は変えず、OR で引く。揺れの数は SEARCH_VARIANTS_MAX まで
    (仮名が長く続く語は、右の方の切り替えを落とす)。
    """
    folded = unicodedata.normalize("NFKC", term)
    kata = "".join(chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in folded)
    hira = "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in folded)
    variants = [term, folded, _dash_variant(folded), kata, hira, *_kana_switch_variants(folded)]
    return list(dict.fromkeys(variants))[:SEARCH_VARIANTS_MAX]


def search_q(lookups, term: str) -> Q:
    """lookups のどれかが term (の揺れ) を含む Q。"""
    q = Q()
    for lookup in lookups:
        for variant in search_variants(term):
            q |= Q(**{f"{lookup}__icontains": variant})
    return q


def lookup_label(model, lookup: str) -> str:
    """検索の案内に出す欄の名前。`customer__name` のように辿る物は末尾の欄の名前 (相手の表の verbose_name)。"""
    current = model
    field = None
    for part in lookup.split("__"):
        try:
            field = current._meta.get_field(part)
        except FieldDoesNotExist:
            return part
        related = getattr(field, "related_model", None)
        if related is None:
            break
        current = related
    return str(getattr(field, "verbose_name", lookup))


def load_model(path: str):
    """"seizou.masters.models.Item" の形の名前から model を返す。部品が無ければ None。"""
    module_name, _sep, attr = path.rpartition(".")
    try:
        return getattr(import_module(module_name), attr)
    except (ImportError, AttributeError):
        return None
