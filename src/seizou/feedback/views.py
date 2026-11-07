"""feedback の画面と口 (依頼書 N59)。画面は core の土台を継承する (R15)。

- 画面 4 つ: 一覧・新規登録・詳細・編集。URL の名前は土台の約束 (<app>:<model>_list / _detail / _create / _update)。
  上書きするのは 13 の属性だけ。画面に足す物 (Issue の文・世話役の印) は template tag で渡す。
- 一覧と詳細は get_queryset で自分の分に絞る (世話役 = アカウント管理の権限は全部)。編集の画面も同じ絞りの中から
  対象を引く (土台の dispatch が部品の get_queryset を読む)。絞りの中で直せない物 (確認済みの後の書いた人) は
  services の update_feedback が断る (赤の知らせ)。
- 口 (状態の変更と削除) は画面でないので関数の view (login_required・require_POST)。書くのは services の関数だけ (R13)。
  断られたら messages.error、通れば messages.success で、どちらも詳細へ戻る (削除が通ったときだけ一覧へ)。
- AI の聞き取りの口 (ask・reset) は htmx の POST だけを受ける (htmx の見出しが無ければ 400)。失敗の文も吹き出しで 200 を返す
  (htmx は 4xx・5xx の返事を差し替えないため)。下書きができたら、中身の無い 200 で編集の画面へ移す (土台の form_valid と同じ形)。
- CSRF の見出しは土台の base.html が htmx の要求に付ける (R18)。
- GitHub の口 (依頼書 N185): 画面「GitHub とつなぐ」と、口の github_disconnect・github_send・github_sync。どれも世話役だけで、
  断るのは services (ValueError を赤の知らせ)。HTTP は services が transaction.atomic の外で送る (R17)。
- GitHub でログインしてつなぐ (依頼書 N320): 口の github_device_start・github_device_poll・github_device_cancel。どれも世話役だけで、
  断るのは services。途中の控えは利用者の session にだけ置く (表に書かない)。待ちの札 (_github_device_card.html) が
  interval 秒ごとに github_device_poll を htmx で呼び、つながったら中身の無い 200 の HX-Redirect で画面の全体を読み直す。
- タブの帯 (依頼書 N321): 送る画面と一覧の上に「送る」「送った一言」「みんなの Issue」(tag の feedback_tabs と feedback/_tabs.html)。
  新しい URL は足さず、「みんなの Issue」は一覧の ?tab=issues で、表を描かないので一覧の get_queryset は空を返す。
- 「うちも」の口 (依頼書 N323): me_too。GitHub の口の中でこの口だけはログインした全員が押せる (👍 には文が入らないため)。
  通ると、services が会社のトークンで Issue に 👍 を付け、押した人の件を残す。
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from seizou.core.views import BaseDetailView, BaseFormView, BaseListView

from . import chat, services
from .forms import FeedbackForm, GithubConnectForm, GithubDeviceForm
from .models import Feedback
from .models import FeedbackReply


def _visible_to(queryset, user):
    """世話役には全部、ほかの人には自分の書いた分だけ。"""
    queryset = queryset.select_related("created_by")
    if services.can_manage(user):
        return queryset
    return queryset.filter(created_by=user)


class FeedbackListView(BaseListView):
    """フィードバックの一覧。

    使う人: 全員 / いつ: 伝えた困りごとの行方を見るとき / 何のために: 自分の下書きと状態を見る (アカウント管理の担当者は全部) / 次にすること: 行から詳細へ。世話役は Issue の文を写す
    """

    model = Feedback
    columns = ("created_at", "kind", "title", "screen", "status", "fix_version", "created_by", "input_tokens", "output_tokens")
    search_fields = ("title", "problem", "situation", "wish")
    # 一覧の絞り (土台の FilterSpec。受注の一覧 OrderListView と同じ形。決め UX-FEEDBACK-1 11-07 09:0x「filterの部分が二箇所あって統一感がない」)。
    # 候補は表の choices の並びのまま (状態 = 下書き・確認済み・送った・直した・見送った、種類 = 6 つ)。GET の名前は土台の f_status・f_kind。
    filters = (
        {"name": "status", "label": "状態", "kind": "choice", "field": "status"},
        {"name": "kind", "label": "種類", "kind": "choice", "field": "kind"},
    )
    ordering = ("-created_at", "-pk")
    screen_name = "フィードバック"
    screen_help = "伝えた困りごと・要望・不具合の下書きと状態の一覧。自分の分が並び、アカウント管理の担当者には全部が並びます。"
    template_name = "feedback/feedback_list.html"

    def get_queryset(self):
        # 土台 (super) が検索と絞り (f_status・f_kind) を当てた後に、自分の分に絞る (世話役は全部)。
        queryset = _visible_to(super().get_queryset(), self.request.user)
        if self.request.GET.get("tab") == "issues":
            # 「みんなの Issue」のタブは表を描かないので数えない (依頼書 N321)
            return queryset.none()
        return queryset


class FeedbackCreateView(BaseFormView):
    """フィードバックの入力。

    使う人: 全員 / いつ: 画面で困ったとき・直してほしいとき・おかしい動きを見つけたとき / 何のために: 困りごと・要望・不具合を下書きにして会社の中に残す / 次にすること: 詳細で内容を確かめる。世話役が確認済みにして GitHub に写す
    """

    model = Feedback
    form_class = FeedbackForm
    service = staticmethod(services.create_feedback)
    template_name = "feedback/feedback_form.html"
    screen_name = "フィードバックを送る"
    screen_help = "今の画面で困ったこと・直してほしいこと・おかしい動きを、種類・画面・題・困りごとの形で下書きにします。下書きは会社の中に残ります。"


class FeedbackDetailView(BaseDetailView):
    """フィードバックの詳細。

    使う人: 全員 / いつ: 下書きの内容と状態を確かめるとき / 何のために: 会話の記録と Issue の文を見る / 次にすること: 世話役は「確認済みにする」、GitHub に写したら「送ったにする」
    """

    model = Feedback
    template_name = "feedback/feedback_detail.html"
    screen_name = "フィードバックの詳細"
    screen_help = "下書きの内容・会話の記録・GitHub の Issue に写す文を見ます。世話役は状態を確認済みと送ったに進めます。"

    def get_queryset(self):
        return _visible_to(super().get_queryset(), self.request.user)


class FeedbackUpdateView(BaseFormView):
    """フィードバックの編集の入力。

    使う人: 全員 / いつ: AI の下書きを直すとき・書き足すとき / 何のために: 下書きの字を直す / 次にすること: 保存して詳細で確かめる
    """

    model = Feedback
    form_class = FeedbackForm
    service = staticmethod(services.update_feedback)
    screen_name = "フィードバックの編集"
    screen_help = "下書きの種類・画面・題・困りごと・場面・望む形・影響する画面を直します。直せるのは書いた人 (下書きの間) とアカウント管理の担当者です。"

    def get_queryset(self):
        # 土台の dispatch がこの絞りの中から対象を引く (一覧と詳細と同じ。ほかの人の下書きは 404)。
        return _visible_to(Feedback.objects.all(), self.request.user)


class GithubSetupView(BaseFormView):
    """GitHub とつなぐ (依頼書 N185。決め 11-06 21:2x)。

    使う人: 世話役 (アカウント管理の担当者) / いつ: フィードバックを GitHub の Issue に直接送れるようにするとき / 何のために: GitHub の口座のトークンを置き、送り先を決める / 次にすること: 確認済みの下書きの詳細で「GitHub に送る」

    世話役だけの画面。部品の画面は R15 で dispatch を上書きできないので、世話役でない人には template が断りの文だけを出し
    (入力の形を出さない)、送られても services の connect_github が断る (赤の知らせ)。入口 (一覧と詳細の link) も世話役だけに出す。
    つないだ後は、form の隠しの next で同じ画面へ戻る (土台の next_url。success_url は R15 の 13 の外)。
    """

    form_class = GithubConnectForm
    service = staticmethod(services.connect_github)
    template_name = "feedback/github_setup.html"
    screen_name = "GitHub とつなぐ"
    screen_help = "フィードバックを GitHub の Issue に直接送るための設定。トークンの取得の手順を見て、トークンと送り先を入れて「つなぐ」を押す。トークンは会社の .env にだけ置き、画面には二度と出ない。世話役 (アカウント管理の担当者) だけが使う。"


def _back_to_detail(pk):
    return HttpResponseRedirect(reverse("feedback:feedback_detail", args=[pk]))


@login_required
@require_POST
def confirm(request, pk):
    """下書きを確認済みにする (世話役だけ。断られたら赤の知らせ)。詳細へ戻る。"""
    feedback = get_object_or_404(Feedback, pk=pk)
    try:
        services.mark_confirmed(feedback, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "確認済みにしました。")
    return _back_to_detail(pk)


@login_required
@require_POST
def sent(request, pk):
    """送った状態にする (世話役だけ。POST の sent_url は任意)。詳細へ戻る。"""
    feedback = get_object_or_404(Feedback, pk=pk)
    try:
        services.mark_sent(feedback, request.user, sent_url=request.POST.get("sent_url", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "送った状態にしました。")
    return _back_to_detail(pk)


@login_required
@require_POST
def fixed(request, pk):
    """直した状態にする (世話役だけ。POST の fixed_note は直した場所と版で任意)。詳細へ戻る。

    書いた人には、次にログインの画面からログインしたときに知らせが出る (signals.py)。
    """
    feedback = get_object_or_404(Feedback, pk=pk)
    try:
        services.mark_fixed(feedback, request.POST.get("fixed_note", ""), request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "直した状態にしました。書いた人が次にログインしたときに知らせます。")
    return _back_to_detail(pk)


@login_required
@require_POST
def delete(request, pk):
    """下書きを削除する (書いた人か世話役。下書きだけ)。消したら一覧へ、断られたら赤の知らせで詳細へ戻る。

    対象は一覧と同じ絞り (_visible_to) の中から引く (ほかの人の下書きは 404)。
    """
    feedback = get_object_or_404(_visible_to(Feedback.objects.all(), request.user), pk=pk)
    try:
        services.delete_feedback(feedback, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return _back_to_detail(pk)
    messages.success(request, "下書きを削除しました。")
    return HttpResponseRedirect(reverse("feedback:feedback_list"))


@login_required
@require_POST
def github_disconnect(request):
    """GitHub から外す (世話役だけ。断られたら赤の知らせ)。「GitHub とつなぐ」の画面へ戻る。"""
    try:
        services.disconnect_github(request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "GitHub から外しました。GitHub の側でも、トークンを「Delete」で無効にしてください。")
    return HttpResponseRedirect(reverse("feedback:github_setup"))


@login_required
@require_POST
def github_send(request, pk):
    """確認済みの下書きを GitHub の Issue にし、送ったにする (世話役だけ。断られたら赤の知らせ)。詳細へ戻る。

    POST の issue_text は、世話役が詳細の欄で伏せ字を戻した Issue の文 (依頼書 N323)。欄の無い要求は None で、今までどおり export_markdown の文を送る。
    """
    feedback = get_object_or_404(Feedback, pk=pk)
    try:
        url = services.send_to_github(feedback, request.user, text=request.POST.get("issue_text"))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"GitHub に送りました: {url}")
    return _back_to_detail(pk)


@login_required
@require_POST
def github_sync(request):
    """送った物の GitHub の Issue を読み、閉じた物を直した・見送った・版待ちに分ける (世話役だけ。断られたら赤の知らせ)。一覧へ戻る。"""
    try:
        counts = services.sync_github(request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"GitHub を見ました: 見た {counts['見た']} 件・直した {counts['直した']} 件・見送った {counts['見送った']} 件・"
            f"版待ち {counts['版待ち']} 件・版の印なし {counts['版の印なし']} 件・読めなかった {counts['読めなかった']} 件"
            f"・返事 {counts['返事']} 件",
        )
    return HttpResponseRedirect(reverse("feedback:feedback_list"))


_HTMX_ONLY = "フィードバックの画面からの要求だけを受けます。"


def _back_to_github_setup():
    return HttpResponseRedirect(reverse("feedback:github_setup"))


def _device_form_message(form) -> str:
    """「GitHub でログインしてつなぐ」の欄の誤りの赤の知らせの文 (画面へ戻すと欄の下の字は消えるので、欄の名前と誤りの字を入れる)。"""
    problems = " ".join(f"「{form.fields[name].label}」: {' '.join(str(error) for error in errors)}" for name, errors in form.errors.items())
    return f"保存できません。{problems} 直してから、もう一度「GitHub でログインしてつなぐ」を押してください。"


@login_required
@require_POST
def github_device_start(request):
    """GitHub でログインしてつなぐを始める (世話役だけ。断られたら赤の知らせ)。「GitHub とつなぐ」の画面へ戻す。

    戻った画面が session の待ちを見て、8 文字の符号と「GitHub を開く」の札を出す。
    """
    form = GithubDeviceForm(request.POST)
    if not form.is_valid():
        messages.error(request, _device_form_message(form))
        return _back_to_github_setup()
    try:
        services.start_github_device(request.session, form.cleaned_data, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    return _back_to_github_setup()


@login_required
@require_POST
def github_device_poll(request):
    """待ちの札が interval 秒ごとに呼ぶ口 (htmx だけ)。承認を 1 回聞き、札を描き直して返す。

    つながったら緑の知らせを積み、中身の無い 200 の HX-Redirect で「GitHub とつなぐ」の画面の全体を読み直させる。
    待ちが無い (ほかの窓でやめた・つないだ) ときも、画面の全体を読み直させる (札を空のまま残さない)。
    """
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest(_HTMX_ONLY)
    result = services.poll_github_device(request.session, request.user)
    if result["state"] in ("connected", "none"):
        if result["state"] == "connected":
            messages.success(request, f"{result['login']} の口座としてつながりました。")
        response = HttpResponse(status=200)
        response["HX-Redirect"] = reverse("feedback:github_setup")
        return response
    context = {
        "device": services.github_device_state(request.session),
        "state": result["state"],
        "message": result.get("message", ""),
    }
    return render(request, "feedback/_github_device_card.html", context)


@login_required
@require_POST
def github_device_cancel(request):
    """GitHub でログインしてつなぐの待ちをやめる (世話役だけ。断られたら赤の知らせ)。「GitHub とつなぐ」の画面へ戻す (知らせは無し)。"""
    try:
        services.cancel_github_device(request.session, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    return _back_to_github_setup()


def _me_too_notice(request, number, result, *, chat_text="") -> None:
    """「うちも」が通った後の知らせ。つないだ会社は緑、つないでいない会社は黄 (下書きで残り、世話役の手が要るため。CLAUDE.md の知らせの強さの 3)。"""
    if result["connected"]:
        messages.success(request, f"Issue #{number} に「うちも」を付けました。{chat_text}")
    else:
        messages.warning(request, f"Issue #{number} を同じ困りごととして下書きに残しました (GitHub とつないでいないので 👍 は付けていません)。")


@login_required
@require_POST
def me_too(request, number):
    """みんなの Issue の 1 件に「うちも」を付ける (ログインした全員。依頼書 N323)。通れば知らせ、断られたら赤の知らせで、一覧の Issue の節へ戻る。

    GitHub の口の中でこの口だけは世話役でなくても押せる。訳: 👍 には文が入らず、取引先の名前や金額が外へ出ないため。
    書くのは services の me_too だけ (押した本人の件を 1 つ作る。HTTP は services が transaction.atomic の外で送る)。
    戻り先は一覧の ?tab=issues と、POST の隠しの issue_q (あれば。探した結果を出し直し、行の「うちも済み」を見せる) と #issues。
    """
    try:
        result = services.me_too(number, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        _me_too_notice(request, number, result)
    params = {"tab": "issues"}
    if "issue_q" in request.POST:
        params["issue_q"] = request.POST["issue_q"]
    return HttpResponseRedirect(f"{reverse('feedback:feedback_list')}?{urlencode(params)}#issues")


@login_required
@require_POST
def ask(request):
    """AI の聞き取りの 1 往復。字の返事はその往復の吹き出しを返し、下書きができたら編集の画面へ移す。

    AI が似た Issue に「うちも」を付けたら (me_too_issue の横取り。依頼書 N323)、知らせを積んでその件の詳細へ移す。
    """
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest(_HTMX_ONLY)
    result = chat.answer(
        request.session,
        request.POST.get("message", ""),
        user=request.user,
        screen=request.POST.get("screen", ""),
    )
    if result.get("me_too"):
        # AI の聞き取りで「うちも」を付けた (依頼書 N323): 知らせを積み、中身の無い 200 でその件の詳細へ移す (下書きでないので編集の画面へは移さない)。
        _me_too_notice(request, result["number"], result, chat_text="あなたの件として残しました。")
        response = HttpResponse(status=200)
        response["HX-Redirect"] = reverse("feedback:feedback_detail", args=[result["feedback"].pk])
        return response
    feedback = result["feedback"]
    if feedback is not None:
        messages.info(request, chat.DRAFT_READY_MESSAGE)
        response = HttpResponse(status=200)
        response["HX-Redirect"] = reverse("feedback:feedback_update", args=[feedback.pk])
        return response
    turns = result["turns"] or [{"role": "assistant", "text": result["text"]}]
    return render(request, "feedback/_chat_messages.html", {"turns": turns})


@login_required
@require_POST
def reset(request):
    """AI の聞き取りの会話を消す (「最初から」)。返す物は中身の無い 200 (吹き出しの並びが空になる)。"""
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest(_HTMX_ONLY)
    chat.reset_chat(request.session)
    return HttpResponse("")


# ---- Issue の返事 (依頼書 N324。決め 11-07 09:1x〜09:4x) ----


def _visible_reply(request, reply_pk):
    """返事を、一覧と同じ絞り (_visible_to) の中の件から引く (ほかの人の件の返事は 404)。"""
    visible = _visible_to(Feedback.objects.all(), request.user)
    return get_object_or_404(FeedbackReply.objects.select_related("feedback"), pk=reply_pk, feedback__in=visible)


@login_required
@require_POST
def reply_add(request, pk):
    """送った件に返事の下書きを書く (書いた人か世話役。断られたら赤の知らせ)。詳細へ戻る。

    対象は一覧と同じ絞り (_visible_to) の中から引く (ほかの人の件は 404)。GitHub には送らない (送るのは世話役)。
    """
    feedback = get_object_or_404(_visible_to(Feedback.objects.all(), request.user), pk=pk)
    try:
        services.add_reply(feedback, request.POST.get("body", ""), request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "返事を書きました。世話役が伏せ字を確かめてから GitHub に送ります。")
    return _back_to_detail(pk)


@login_required
@require_POST
def reply_send(request, reply_pk):
    """世話役が直した返事の文 (POST の body) を、会社のトークンで Issue に送る (世話役だけ。断られたら赤の知らせ)。詳細へ戻る。"""
    reply = _visible_reply(request, reply_pk)
    try:
        url = services.send_reply(reply, request.POST.get("body", ""), request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"返事を GitHub に送りました: {url}")
    return _back_to_detail(reply.feedback_id)


@login_required
@require_POST
def reply_mark_sent(request, reply_pk):
    """つないでいない会社で、世話役が返事を GitHub に手で写した後に送ったにする (世話役だけ。断られたら赤の知らせ)。詳細へ戻る。"""
    reply = _visible_reply(request, reply_pk)
    try:
        services.mark_reply_sent(reply, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "返事を送った状態にしました。")
    return _back_to_detail(reply.feedback_id)
