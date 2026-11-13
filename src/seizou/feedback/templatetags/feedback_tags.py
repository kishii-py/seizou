"""feedback の画面が使う tag。読むだけ (書くのは services の関数)。

部品の画面は R15 で get_context_data を上書きできないので、画面に足す物はここで渡す。
{% feedback_detail object as info %}   詳細の Issue の文 (info.issue_text) と、アカウント管理の担当者の印 (info.can_manage) と、削除の印 (info.can_delete) と、GitHub の状態 (info.github)
{% feedback_github as github %}        GitHub の口の状態とアカウント管理の担当者の印と、皆の Issue を探した結果 (一覧と「GitHub とつなぐ」の画面。開発の記録 docs/nights/20261106/N185.md。search=False で探さない)
                                       と、GitHub でログインしてつなぐの待ち・Client ID があるか・入力の形 (開発の記録 docs/nights/20261107/N320.md)
                                       と、みんなの Issue の行の自社の印 (marks と、詳細へ移れる件の pk の openable。開発の記録 docs/nights/20261107/N323.md)
{{ github.marks|issue_mark:issue.number }}  みんなの Issue の行の自社の印 ({"sent", "me_too"} か None。開発の記録 docs/nights/20261107/N323.md)
{% feedback_panel as panel %}          入力の画面の AI の段の状態 (鍵があるか・会話・開いていた画面)
{% feedback_fields fields as fields %}  詳細の基本情報の項目の並び (影響する画面の見出しを入力の画面と同じ字にした写し。block.super の前に置く)
{% feedback_tabs as tabs %}            送る画面と一覧の上のタブの帯 (送る・送った件 (件数)・みんなの Issue。開発の記録 docs/nights/20261107/N321.md。feedback/_tabs.html)
{% feedback_reply_draft reply as text %}  返事の下書きの、アカウント管理の担当者が直す前の初めの字 (伏せ字の入った文。開発の記録 docs/nights/20261107/N324.md)
{% feedback_board as board %}          みんなの Issue の盤と一覧の形の材料と、並べ替えと切り替えの link (開発の記録 docs/nights/20261107/N325.md。GET の tab が issues のときだけ GitHub を読む)
{% feedback_steps object as steps %}   詳細の「この件の進み具合」の段の帯の材料 (feedback/_steps.html。受け渡しの seizou-フィードバックの段と送る画面-20261107/指示書.md)
info の replies・can_reply・company_login は詳細の「返事」の節 (_replies.html) が使う (開発の記録 docs/nights/20261107/N324.md)。
"""

from django import template
from django.urls import reverse
from django.utils.http import urlencode

from seizou.core.status_colors import tone_for
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
    """詳細に足す物。{"issue_text": GitHub の Issue に写す文, "can_manage": アカウント管理の担当者か, "can_delete": 下書きを消せるか, "screenshots": 画面の絵 (画像の添付ファイル。付けた順),
    "github": GitHub とつないだ状態 (services.github_status。トークンの値は無い),
    "version": 会社が入れている版 (manual.package_version。入っていなければ空の字。版待ちの件の直る版と並べてアカウント管理の担当者に見せる。開発の記録 docs/nights/20261107/N322.md)}。

    request が無ければアカウント管理の担当者でなく、消せない。
    """
    # 版の口 (core の manual) はこの tag だけが使うので、ここで import する。
    from seizou.core import manual

    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    return {
        "issue_text": services.export_markdown(feedback),
        "screenshots": services.screenshots_of(feedback),
        "can_manage": user is not None and services.can_manage(user),
        "can_delete": user is not None and services.can_delete(feedback, user),
        "github": services.github_status(),
        "version": manual.package_version(),
        # 「返事」の節 (開発の記録 docs/nights/20261107/N324.md): 下書き (送った日時が空) は返事を書ける人 (書いた人かアカウント管理の担当者) にだけ並べる
        "replies": [reply for reply in services.replies_of(feedback) if reply.sent_at or (user is not None and services.can_reply(feedback, user))],
        "can_reply": user is not None and services.can_reply(feedback, user),
        "company_login": services.company_login(),
    }


@register.simple_tag(takes_context=True)
def feedback_github(context, search=True):
    """GitHub の口の状態。{"status": services.github_status() (トークンの値は無い), "can_manage": アカウント管理の担当者か,
    "searched": 皆の Issue を探したか, "q": 探した言葉, "search": services.search_issues の結果か None,
    "device": GitHub でログインしてつなぐの待ち (services.github_device_state。device_code は無い) か None,
    "client_id": 作品の Client ID があるか, "device_form": 「GitHub でログインしてつなぐ」の入力の形 (GithubDeviceForm),
    "marks": みんなの Issue の行の自社の印 (services.our_issue_marks。探したときだけ。ほかは空の辞書),
    "openable": 自社の印の件のうち、見ている人が詳細を開ける件の pk の集まり (アカウント管理の担当者は全部、ほかの人は自分の書いた件)}。

    皆の Issue を探す (GitHub を読む) のは、GET に issue_q があるときだけ (「探す」を押したとき。空の言葉なら新しい順)。
    一覧を開くたびには GitHub を読まない。request が無ければアカウント管理の担当者でなく、探さず、待ちは None。
    search が偽なら探さない (一覧の見出しの行のボタンが使う。同じ表示の下の枠が探すので、GitHub を 2 回読まない。読めなかった返事は控えに残らないため = services.search_issues)。
    入力の形をここで渡すのは、部品の画面が R15 で get_context_data を上書きできないため (開発の記録 docs/nights/20261107/N320.md)。
    自社の印 (開発の記録 docs/nights/20261107/N323.md) はアカウント管理の担当者でない人にも出す (印は会社の物)。詳細へ移る link は openable の件だけにする
    (詳細の画面はアカウント管理の担当者でない人に自分の書いた件だけを開くので、ほかの人の件の link は 404 になるため)。
    """
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    params = getattr(request, "GET", {}) if request is not None else {}
    session = getattr(request, "session", None) if request is not None else None
    searched = search and "issue_q" in params
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
        **_issue_marks(user, searched),
    }


def _issue_marks(user, searched) -> dict:
    """みんなの Issue の行の自社の印と、見ている人が詳細を開ける件の pk (開発の記録 docs/nights/20261107/N323.md)。探していなければ両方とも空。読むだけ。"""
    if not searched:
        return {"marks": {}, "openable": set()}
    marks = services.our_issue_marks()
    pks = {pk for mark in marks.values() for pk in mark.values() if pk is not None}
    if user is not None and services.can_manage(user):
        openable = pks
    elif getattr(user, "pk", None) is not None and pks:
        openable = set(Feedback.objects.filter(pk__in=pks, created_by_id=user.pk).values_list("pk", flat=True))
    else:
        openable = set()
    return {"marks": marks, "openable": openable}


@register.filter
def issue_mark(marks, number):
    """みんなの Issue の行の自社の印 ({"sent": pk か None, "me_too": pk か None})。marks に番号が無ければ None (開発の記録 docs/nights/20261107/N323.md)。"""
    return marks.get(number) if isinstance(marks, dict) else None


@register.simple_tag
def feedback_fields(fields):
    """詳細の基本情報の項目の並び (土台の context の fields) の写し。影響する画面の見出しだけを SCREENS_LABEL にする。

    土台の詳細は表の verbose_name を見出しにする (影響する画面は内部の字を含む)。部品の画面は R15 で get_context_data を
    上書きできないので、詳細のテンプレートが block.super の前に {% feedback_fields fields as fields %} で並びを替える。
    項目の辞書は欄の名前を持たないので、表の verbose_name と同じ見出しの項目を替える。元の並びと辞書は変えない。
    """
    stored = str(Feedback._meta.get_field("screens").verbose_name)
    return [{**item, "label": SCREENS_LABEL} if item["label"] == stored else item for item in fields or []]


#: タブの帯の 3 つ (鍵と字)。並びは画面の左から (開発の記録 docs/nights/20261107/N321.md。決め 11-07 09:1x〜09:2x)
TABS = (("send", "送る"), ("sent", "送った件"), ("issues", "みんなの Issue"))


@register.simple_tag(takes_context=True)
def feedback_tabs(context):
    """送る画面と一覧の上のタブの帯 (開発の記録 docs/nights/20261107/N321.md)。並び 3 つ [{"key", "label", "href", "count", "selected"}]。例外を出さない。

    href: 送る = 送る画面 (今の GET に screen があれば ?screen= で引き継ぐ。左のメニューの入口が渡した、開いていた画面を失わない)、
    送った件 = 一覧、みんなの Issue = 一覧の ?tab=issues (新しい URL は足さない。部品の画面は R15 で土台の 4 つのまま)。
    count: 送った件だけ見える件数 (services.visible_count。一覧と同じ絞り)、ほかは None。
    selected: 送る画面なら送る、一覧で GET の tab が issues ならみんなの Issue、ほかの一覧なら送った件 (詳細と編集の画面は帯を出さない)。
    GitHub は読まない (帯を描くたびに HTTP を送らない)。request が無ければ全部 False で、count は 0。
    """
    request = context.get("request")
    params = getattr(request, "GET", {}) if request is not None else {}
    user = getattr(request, "user", None) if request is not None else None
    match = getattr(request, "resolver_match", None) if request is not None else None
    view_name = getattr(match, "view_name", "") or ""
    send_href = reverse("feedback:feedback_create")
    screen = params.get("screen", "")
    if screen:
        send_href += "?" + urlencode({"screen": screen})
    list_href = reverse("feedback:feedback_list")
    hrefs = {"send": send_href, "sent": list_href, "issues": f"{list_href}?tab=issues"}
    chosen = ""
    if view_name == "feedback:feedback_create":
        chosen = "send"
    elif view_name == "feedback:feedback_list":
        chosen = "issues" if params.get("tab") == "issues" else "sent"
    count = services.visible_count(user) if user is not None else 0
    return [
        {"key": key, "label": label, "href": hrefs[key], "count": count if key == "sent" else None, "selected": key == chosen}
        for key, label in TABS
    ]


# ---- 詳細の「この件の進み具合」の段の帯 (受け渡しの seizou-フィードバックの段と送る画面-20261107/指示書.md の作る物 1。本人の決め 11-07 17:2x〜18:1x) ----

#: 段の帯の 4 つ (鍵と字。並びは画面の左から)。最後の段の字は状態で替わる (STEP_LAST_LABELS)
STEPS = (("written", "書いた"), ("confirmed", "確認済み"), ("sent", "GitHub に送った"), ("fixed", "直した"))
#: 最後の段の字を替える状態 (状態の値 → 字)。見送った件は最後の段を赤の「見送った」にする
STEP_LAST_LABELS = {"dismissed": "見送った"}
#: 状態の値 → 済んだ段の数 (下書き = 書いたまで、確認済み = 2 つ、送った = 3 つ、直した・見送った = 4 つ全部)
STEPS_DONE = {"draft": 1, "confirmed": 2, "sent": 3, "fixed": 4, "dismissed": 4}


@register.simple_tag
def feedback_steps(feedback):
    """詳細の「この件の進み具合」の段の帯の材料 (feedback/_steps.html)。並び 4 つ [{"key", "label", "done", "tone", "when", "note"}]。読むだけ。

    done: 済んだ段か。状態より前の段は全部済んだ印にする (アカウント管理の担当者が確認せずに送った件でも「確認済み」は済んだ印 = 指示書の「決めを待つ所」1 の勧め。
          下書きから直接「直した」にした件も、間の段は済んだ印)。
    tone: 済んだ段は "primary"、最後の段だけ状態の色の表 (core.status_colors.tone_for) の意味の色 (直した = success、見送った = error)。済んでいない段は空の字。
    when と note: 記録にある物だけ (書いた = 書いた日時と書いた人、GitHub に送った = Issue の番号 (sent_url の末尾。無ければ空)、直した = 直した日時、
          見送った = 見送った日時、直る版を待つ件 (送ったで fix_version がある) = 最後の段に「<版> で直る」)。確認済みと送ったの日時は表に無いので出さない
          (新しい項目と migration は足さない = 指示書の勧め)。Issue の番号の読み方は services の URL の形 (_ISSUE_URL) を借りる (同じ部品の中)。
    段の名前は GitHub につないでいない会社 (手で写して「送った」にした件) でも「GitHub に送った」のまま (指示書の「迷ったら」の勧め)。
    """
    done_count = STEPS_DONE.get(feedback.status, 1)
    issue = services._ISSUE_URL.fullmatch(feedback.sent_url or "")
    steps = []
    for index, (key, label) in enumerate(STEPS, start=1):
        done = index <= done_count
        last = index == len(STEPS)
        step = {"key": key, "label": label, "done": done, "tone": "primary" if done else "", "when": None, "note": ""}
        if last:
            step["label"] = STEP_LAST_LABELS.get(feedback.status, label)
            if done:
                step["tone"] = tone_for(feedback.status, "feedback")
        if key == "written":
            step["when"] = feedback.created_at
            step["note"] = str(feedback.created_by) if feedback.created_by_id else ""
        elif key == "sent" and done and issue is not None:
            step["note"] = f"Issue #{issue.group(3)}"
        elif last:
            if feedback.status == "fixed":
                step["when"] = feedback.fixed_at
            elif feedback.status == "dismissed":
                step["when"] = feedback.dismissed_at
            elif feedback.status == "sent" and feedback.fix_version:
                step["note"] = f"{feedback.fix_version} で直る"
        steps.append(step)
    return steps


@register.simple_tag
def feedback_reply_draft(reply):
    """返事の下書きの、アカウント管理の担当者が直す前の初めの字 (services.reply_draft_text = 取引先の名前と金額らしい数を伏せた本文。開発の記録 docs/nights/20261107/N324.md)。"""
    return services.reply_draft_text(reply)


#: みんなの Issue の並べ替えの link (鍵と字。開発の記録 docs/nights/20261107/N325.md)。初めは 👍 の多い順
BOARD_SORT_LINKS = (("top", "👍 の多い順"), ("new", "新しい順"))
#: みんなの Issue の形の切り替えの link (鍵と字)。初めは盤 (選びは覚えない = 毎回盤)
BOARD_VIEW_LINKS = (("board", "カンバン"), ("list", "一覧"))


def _board_href(q, view, sort) -> str:
    """みんなの Issue のタブの URL (tab=issues と、q が None でなければ issue_q、初めの値でない view と sort)。"""
    params = {"tab": "issues"}
    if q is not None:
        params["issue_q"] = q
    if view != "board":
        params["view"] = view
    if sort != "top":
        params["sort"] = sort
    return reverse("feedback:feedback_list") + "?" + urlencode(params)


@register.simple_tag(takes_context=True)
def feedback_board(context):
    """みんなの Issue のカンバンと一覧の形の材料 (開発の記録 docs/nights/20261107/N325.md。決め 11-07 09:3x〜10:1x。5 つの列をいつも出す形は本人の決め 11-07 16:4x)。

    返す物 {"view": "board" か "list" (GET の view が list なら list。ほかは board), "sort": "top" か "new" (GET の sort。ほかは top),
    "q": GET の issue_q, "keep_q": GET に issue_q があったか, "board": services.board_issues の結果か None,
    "columns": 5 つの列 (services.BOARD_COLUMNS の順。件数 0 の列も入る。board が None なら空),
    "sorts" と "views": 並べ替えと切り替えの link の並び [{"key", "label", "href", "selected"}]}。
    GitHub を読むのは GET の tab が issues のときだけ。ほかと request が無いときは読まず、board は None で列は空。
    link の href は tab と、今の issue_q (GET にあったときだけ) と、もう片方の選び (view か sort) を保つ。
    issue_q を GET にあったときだけ付けるのは、tag の feedback_github が GET に issue_q があると今までの探す (search_issues) も読むため。
    部品の画面は R15 で get_context_data を上書きできないので、材料をここで渡す。
    """
    request = context.get("request")
    params = getattr(request, "GET", {}) if request is not None else {}
    view = "list" if params.get("view") == "list" else "board"
    sort = "new" if params.get("sort") == "new" else "top"
    keep_q = "issue_q" in params
    q = params.get("issue_q", "") if keep_q else ""
    board = None
    if request is not None and params.get("tab") == "issues":
        board = services.board_issues(q, sort=sort, user=getattr(request, "user", None))
    columns = board["columns"] if board is not None else []
    kept = q if keep_q else None
    return {
        "view": view,
        "sort": sort,
        "q": q,
        "keep_q": keep_q,
        "board": board,
        "columns": columns,
        "sorts": [{"key": key, "label": label, "href": _board_href(kept, view, key), "selected": key == sort} for key, label in BOARD_SORT_LINKS],
        "views": [{"key": key, "label": label, "href": _board_href(kept, key, sort), "selected": key == view} for key, label in BOARD_VIEW_LINKS],
    }
