"""feedback の画面が使う tag。読むだけ (書くのは services の関数)。

部品の画面は R15 で get_context_data を上書きできないので、画面に足す物はここで渡す。
{% feedback_detail object as info %}   詳細の Issue の文 (info.issue_text) と、世話役の印 (info.can_manage) と、削除の印 (info.can_delete) と、GitHub の状態 (info.github)
{% feedback_github as github %}        GitHub の口の状態と世話役の印と、皆の Issue を探した結果 (一覧と「GitHub とつなぐ」の画面。依頼書 N185)
                                       と、GitHub でログインしてつなぐの待ち・Client ID があるか・入力の形 (依頼書 N320)
{% feedback_panel as panel %}          入力の画面の AI の段の状態 (鍵があるか・会話・開いていた画面)
{% feedback_filter as filter %}        一覧の絞り込みの選択肢と今の値
{% feedback_fields fields as fields %}  詳細の基本情報の項目の並び (影響する画面の見出しを入力の画面と同じ字にした写し。block.super の前に置く)
"""

from django import template

from seizou.feedback import chat, github, services
from seizou.feedback.forms import SCREENS_LABEL, GithubDeviceForm
from seizou.feedback.models import Feedback

register = template.Library()


@register.simple_tag(takes_context=True)
def feedback_panel(context):
    """AI の段の状態。{"available", "turns", "screen", "no_key_message", "max_questions"}。例外を出さない。

    screen は POST の screen、無ければ GET の screen (保存で止まった画面の描き直しでも、開いていた画面を失わない)。
    request が無ければ会話は空。
    """
    request = context.get("request")
    session = getattr(request, "session", None) if request is not None else None
    screen = ""
    if request is not None:
        screen = request.POST.get("screen") or request.GET.get("screen") or ""
    return {
        "available": chat.is_available(),
        "turns": chat.chat(session)["turns"],
        "screen": screen,
        "no_key_message": chat.MESSAGE_NO_KEY,
        "max_questions": chat.MAX_QUESTIONS,
    }


@register.simple_tag(takes_context=True)
def feedback_detail(context, feedback):
    """詳細に足す物。{"issue_text": GitHub の Issue に写す文, "can_manage": 世話役か, "can_delete": 下書きを消せるか, "screenshots": 画面の絵 (画像の添付ファイル。付けた順),
    "github": GitHub とつないだ状態 (services.github_status。トークンの値は無い)}。

    request が無ければ世話役でなく、消せない。
    """
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    return {
        "issue_text": services.export_markdown(feedback),
        "screenshots": services.screenshots_of(feedback),
        "can_manage": user is not None and services.can_manage(user),
        "can_delete": user is not None and services.can_delete(feedback, user),
        "github": services.github_status(),
    }


@register.simple_tag(takes_context=True)
def feedback_github(context):
    """GitHub の口の状態。{"status": services.github_status() (トークンの値は無い), "can_manage": 世話役か,
    "searched": 皆の Issue を探したか, "q": 探した言葉, "search": services.search_issues の結果か None,
    "device": GitHub でログインしてつなぐの待ち (services.github_device_state。device_code は無い) か None,
    "client_id": 作品の Client ID があるか, "device_form": 「GitHub でログインしてつなぐ」の入力の形 (GithubDeviceForm)}。

    皆の Issue を探す (GitHub を読む) のは、GET に issue_q があるときだけ (「探す」を押したとき。空の言葉なら新しい順)。
    一覧を開くたびには GitHub を読まない。request が無ければ世話役でなく、探さず、待ちは None。
    入力の形をここで渡すのは、部品の画面が R15 で get_context_data を上書きできないため (依頼書 N320)。
    """
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    params = getattr(request, "GET", {}) if request is not None else {}
    session = getattr(request, "session", None) if request is not None else None
    searched = "issue_q" in params
    words = params.get("issue_q", "") if searched else ""
    return {
        "status": services.github_status(),
        "can_manage": user is not None and services.can_manage(user),
        "searched": searched,
        "q": words,
        "search": services.search_issues(words) if searched else None,
        "device": services.github_device_state(session) if session is not None else None,
        "client_id": bool(github.client_id()),
        "device_form": GithubDeviceForm(),
    }


@register.simple_tag(takes_context=True)
def feedback_filter(context):
    """一覧の絞り込みの選択肢と今の値。{"statuses", "kinds", "status", "kind"}。例外を出さない。

    今の値は services.list_filter が GET から読む (外の値は空の字)。request が無ければ空の字。
    """
    request = context.get("request")
    params = getattr(request, "GET", {}) if request is not None else {}
    return {"statuses": Feedback.STATUS_CHOICES, "kinds": Feedback.KIND_CHOICES, **services.list_filter(params)}


@register.simple_tag
def feedback_fields(fields):
    """詳細の基本情報の項目の並び (土台の context の fields) の写し。影響する画面の見出しだけを SCREENS_LABEL にする。

    土台の詳細は表の verbose_name を見出しにする (影響する画面は内部の字を含む)。部品の画面は R15 で get_context_data を
    上書きできないので、詳細のテンプレートが block.super の前に {% feedback_fields fields as fields %} で並びを替える。
    項目の辞書は欄の名前を持たないので、表の verbose_name と同じ見出しの項目を替える。元の並びと辞書は変えない。
    """
    stored = str(Feedback._meta.get_field("screens").verbose_name)
    return [{**item, "label": SCREENS_LABEL} if item["label"] == stored else item for item in fields or []]
