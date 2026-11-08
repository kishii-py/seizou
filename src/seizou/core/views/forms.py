"""入力の土台 (BaseFormView) と、欄の見た目と入力の形の道具 (core.views の package)。"""

from __future__ import annotations

import inspect

from django import forms as django_forms
from django.contrib import messages
from django.forms.utils import pretty_name
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils.cache import patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import FormView

from seizou.core import access
from seizou.core import checks
from seizou.core import fields as company_fields
from seizou.core.exceptions import CheckViolation, RuleError
from seizou.core.widgets import ClearableFileWidget, DecimalInput, IntegerInput, RemoteSelect

from .base import Notice, ScreenMixin, add_notice_trigger, invalid_form_message, model_url, refusal_head


class BaseFormView(ScreenMixin, FormView):
    """入力の土台。form の値が通ったら検査の層を通し、service を呼ぶ。土台が ModelForm の save を呼ぶことは無い。

    小窓の形 (決め 11-04 17:5x〜18:0x。一覧の行の操作はページを移らず小窓で入力する): htmx の要求で見出し HX-Target が
    seizou-modal-box (base.html の小窓の箱) のとき、同じ URL の入力を小窓の中身 (<app>/_form_modal.html か core/_form_modal.html)
    で返す。誤り・赤の知らせ・黄の知らせと「このまま保存」(confirmed=1) は、同じ形のカードが小窓の中に描き直される。
    保存したら移らず、本文の無い 204 に HX-Trigger の seizou:saved (知らせの字と色) を付けて返す (messages に残さない)。
    seizou-modal.js が小窓を閉じ、右上の知らせを出し、一覧の行を今の絞りのまま取り直す。
    それ以外の要求 (直の要求・htmx のほかの要求) は今までどおり画面の全体で、保存したら移る。

    開いた元の画面へ戻す口 (11-05。本人の指摘と作者の決め): 詳細などの入口が ?next=<開いた画面の道> を付けて開くと、
    「キャンセル」の先と、保存の後に一覧へ戻る形の移り先 (保存した物の詳細が無い物) をその道にする。保存した物の詳細へ移る形は今のまま。
    道は同じサイトの「/」で始まる物だけ (外・絶対 URL は捨てて今までどおり)。form の hidden の next で POST にも引き継ぐ。
    """

    model = None
    form_class = None
    service = None
    template_name = "core/form.html"
    success_message = "保存しました。"
    #: 削除の画面 (service の名前が DELETE_SERVICE_PREFIXES で始まる) の知らせ
    delete_message = "削除しました。"
    DELETE_SERVICE_PREFIXES = ("delete_", "remove_")
    #: 小窓の形の印 (htmx の要求の見出し HX-Target の値 = base.html の小窓の箱の id)
    MODAL_TARGET = "seizou-modal-box"
    #: 開いた元の画面の道を受ける GET の鍵 (form の hidden で POST にも同じ名前で送る)
    NEXT_PARAM = "next"
    object = None

    # --- 対象 (直すとき) ---
    def _load_object(self, kwargs) -> None:
        """URL の pk の対象を self.object に引く (model と pk のある直す画面だけ)。

        部品の画面が get_queryset を持つときは、その絞りの中から対象を引く (絞りの外は 404)。土台は get_queryset を持たない。
        訳: 対象は、ログインの門 (LoginRequiredMixin) と部署の門 (ScreenMixin の screen_denied) を通った後の get と post でだけ引く。
        何のため: 門の前に引くと、ログインしていない人にも、ある pk はログインへ移り無い pk は 404 になって、記録の有無と数が分かる
        (部署の外の人には 403 と 404 の違いで分かる)。門を先にすると、どちらの pk も同じ答え (ログインへ移す・403) になる。
        出所 = Claude Security の走査の指摘 (11-07) (入力の土台が門の前に記録を引く)。
        変えてよい条件 = 門より前に対象を引いても、答えが記録の有無で変わらないことを試験 (tests/test_core_access_gate_order.py) で示せるとき。
        """
        if self.model is not None and "pk" in kwargs:
            get_queryset = getattr(self, "get_queryset", None)
            source = get_queryset() if get_queryset is not None else self.model
            self.object = get_object_or_404(source, pk=kwargs["pk"])

    def get(self, request, *args, **kwargs):
        self._load_object(kwargs)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self._load_object(kwargs)
        return super().post(request, *args, **kwargs)

    # --- 小窓の形 ---
    @property
    def in_modal(self) -> bool:
        """小窓の形か。htmx の要求で、差し替える先 (HX-Target) が小窓の箱のときだけ (GET と POST の両方)。"""
        headers = self.request.headers
        return bool(headers.get("HX-Request")) and headers.get("HX-Target") == self.MODAL_TARGET

    def get_template_names(self):
        """小窓の形は <app>/_form_modal.html → core/_form_modal.html (部品が小窓の形だけ別に持てる口)。ほかは template_name。"""
        if self.in_modal:
            app = self.model._meta.app_label if self.model is not None else self.screen_app()
            return [f"{app}/_form_modal.html", "core/_form_modal.html"]
        return super().get_template_names()

    # --- 開いた元の画面 (?next=) ---
    @property
    def next_url(self) -> str | None:
        """開いた元の画面の道。GET の ?next= か、form の hidden の next。同じサイトの「/」で始まる道だけで、ほかは None。

        外の URL・絶対 URL (http://… は同じ host でも)・「//」で始まる物・「/」で始まらない物は捨てる (開いた元が分からない形 = 今までどおり)。
        """
        url = self.request.GET.get(self.NEXT_PARAM) or self.request.POST.get(self.NEXT_PARAM) or ""
        if not url.startswith("/") or url.startswith("//"):
            return None
        if not url_has_allowed_host_and_scheme(url, allowed_hosts={self.request.get_host()}, require_https=self.request.is_secure()):
            return None
        return url

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        # 同じ URL で小窓の中身と画面の全体を返すので、ブラウザの保存が取り違えないように見出しで分ける (一覧の HX-Request と同じ)。
        patch_vary_headers(response, ("HX-Target",))
        # htmx が画面の一部 (HX-Target の箱) だけを差し替える要求 (今はメモの書く枠 = notes/note_form.html の hx-select="#notes")。
        # 右上の知らせ (_toasts.html) が差し替えに入らないので、合図でも返す (決め 11-07)。小窓と画面の全体には付けない。
        headers = self.request.headers
        if headers.get("HX-Request") and headers.get("HX-Target") and not self.in_modal:
            add_notice_trigger(response, context.get("notice"), context.get("warnings") or ())
        return response

    def get_initial(self):
        """form の初期値。class の initial の写しに、新規登録の画面の GET の値を足す。

        受注の明細から出荷の新規登録・指示書の発行へ、明細を選んだ形で移る入口のため (例 ?order_line=<pk>)。
        写すのは GET の鍵の全部 (値は字のまま。同じ鍵が 2 つ以上あれば字の list = 注文残の盤で選んだ明細の ?line=<pk>&line=<pk>。開発の記録 docs/nights/20261104/N124.md)。form の欄に無い鍵も写すので、form の __init__ が
        self.initial.get("from_line") のように読める (注残の盤の「発注する」が明細の pk を渡す入口のため。決め 11-03)。
        写さない物: POST の要求 (送られた値が正)・編集の画面 (対象の値が正)・会社ごとの項目 (ext__)・開いた元の画面の道 (next。入力の値でない)。
        値の検査はしない。保存のときに form と検査の層と service が見る (数でない値や無い pk は、選ぶ欄が何も選ばないだけ)。
        """
        initial = super().get_initial()
        if self.request.method != "GET" or self.object is not None:
            return initial
        for name in self.request.GET:
            if not name.startswith(company_fields.EXT_PREFIX) and name != self.NEXT_PARAM:
                values = self.request.GET.getlist(name)
                initial[name] = values if len(values) > 1 else values[-1]
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.object is not None and _accepts_instance(self.form_class):
            kwargs["instance"] = self.object
        if access.takes_hide_money(self.form_class) and not access.can_see_money(self.request.user):
            kwargs["hide_money"] = True
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        model = self.model or getattr(getattr(form, "_meta", None), "model", None)
        # form の class の属性 adds_company_fields = False の form には、会社ごとの項目を足さない (作者の決め)。
        # 自分の欄だけを service_kwargs で渡す確かめの form (図面を版に戻す) に足すと、直しても保存されない欄が出るため。
        # 土台が form の属性を読む先例は submit_label (core/_form_card.html) と last_fields (下)。
        if model is not None and getattr(form, "adds_company_fields", True):
            for name, field in company_fields.form_fields_for(model).items():
                form.fields[name] = field
                if self.object is not None and not form.is_bound:
                    form.initial[name] = company_fields.ext_value(self.object, name[len(company_fields.EXT_PREFIX):])
        # form の class の属性 last_fields (欄の名前の並び) の欄を、会社ごとの項目を足した後の最後へ回す (作者の決め)。
        # Django の field_order は form の __init__ の中で並べるので、この後に足す会社ごとの項目より後ろへは回せない。
        # 土台が form の属性を読む先例は submit_label (core/_form_card.html)。使う所は masters の ItemForm・PartnerForm・ProcessForm。
        # 属性の無い form と、form に無い名前 (新規登録で外した使用中の欄など) は今の並びのまま。
        last = [name for name in getattr(form, "last_fields", ()) if name in form.fields]
        if last:
            # order_fields は渡した並びを先頭に置くので、全部の欄の並びを渡す。
            form.order_fields([name for name in form.fields if name not in last] + last)
        for name, field in form.fields.items():
            use_decimal_input(field)
            use_clearable_file_widget(field)
            field.widget.attrs.setdefault("class", widget_css(field.widget))
            use_label_required(name, field)
        return form

    # --- 検査の層と service ---
    def get_action(self) -> str:
        service = self.get_service()
        module = service.__module__.split(".")
        app = module[-2] if len(module) >= 2 else module[-1]
        return f"{app}.{service.__name__}"

    def get_service(self):
        service = self.service
        if service is None:
            raise RuntimeError(f"{type(self).__name__} に service が無い")
        return service

    def service_kwargs(self, form) -> dict:
        if hasattr(form, "service_kwargs"):
            return dict(form.service_kwargs())
        data, ext = company_fields.split_ext(form.cleaned_data)
        kwargs = {"data": data}
        if ext is not None:
            kwargs["ext"] = ext
        return kwargs

    def call_service(self, form):
        kwargs = self.service_kwargs(form)
        args = (self.object,) if self.object is not None else ()
        return self.get_service()(*args, user=self.request.user, **kwargs)

    def form_valid(self, form):
        try:
            access.require_write(self.request.user, self.get_action())
        except access.AccessDenied as exc:
            return self.render_to_response(self.get_context_data(form=form, notice=exc))
        kwargs = self.service_kwargs(form)
        check_data = {**form.cleaned_data, **{k: v for k, v in kwargs.items() if k != "data"}, **kwargs.get("data", {})}
        if self.object is not None:
            check_data.setdefault("object", self.object)
        try:
            warnings = checks.run(self.get_action(), check_data, self.request.user)
        except CheckViolation as exc:
            return self.render_to_response(self.get_context_data(form=form, notice=exc))
        if warnings and self.request.POST.get("confirmed") != "1":
            return self.render_to_response(self.get_context_data(form=form, warnings=warnings))
        try:
            result = self.call_service(form)
        except RuleError as exc:
            return self.render_to_response(self.get_context_data(form=form, notice=exc))
        except ValueError as exc:
            # 部品の決まり (番号の無い物。品番の一意など) は日本語の文の ValueError。赤の知らせにする。
            return self.render_to_response(self.get_context_data(form=form, notice=Notice("入力", str(exc))))
        deleted = self.get_service().__name__.startswith(self.DELETE_SERVICE_PREFIXES)
        text = with_saved_note(self.delete_message if deleted else self.success_message, result)
        if self.in_modal:
            return self.saved_in_modal(text, "warning" if getattr(result, "saved_level", "") == "warning" else "success")
        # service の返す物の saved_level が "warning" なら黄 (確認してください) の知らせ。ほかは緑 (成功しました)。
        if getattr(result, "saved_level", "") == "warning":
            messages.warning(self.request, text)
        else:
            messages.success(self.request, text)
        url = self.get_success_url_for(result)
        if self.request.headers.get("HX-Request"):
            response = HttpResponse(status=200)
            response["HX-Redirect"] = url
            return response
        return HttpResponseRedirect(url)

    def form_invalid(self, form):
        """欄の検査で止まった時も、止まった知らせ (赤) を出す (決め 11-06。欄の誤りなら欄の下の赤い字と知らせの両方)。

        画面の全体では右上の赤い知らせ (core/_toasts.html)、小窓では小窓の上の赤い帯 (core/_form_modal.html の core/_notices.html)。
        番号は「入力」(決まりの番号でない) なので、番号と「?」は出ない (services の ValueError の断りと同じ道)。
        """
        try:
            name = self.get_service().__name__
        except RuntimeError:
            name = ""
        message = invalid_form_message(form, refusal_head(name, self.DELETE_SERVICE_PREFIXES))
        return self.render_to_response(self.get_context_data(form=form, notice=Notice("入力", message)))

    def saved_in_modal(self, text: str, level: str) -> HttpResponse:
        """小窓の形の保存の後: 本文の無い 204 に、HX-Trigger の seizou:saved (知らせの字 message と色 level) を付ける。

        htmx は 204 では小窓の中身を差し替えず、form に seizou:saved を出す (body まで泡立つ)。messages には残さない
        (残すと、次に画面の全体を開いたときに古い知らせが出る)。JSON は既定の ensure_ascii (日本語は \\u の形) のまま
        (見出しの値は latin-1 の字だけ。日本語を生で入れると Django が =?utf-8?b?…?= の形に変え、htmx が読めない)。
        """
        import json

        response = HttpResponse(status=204)
        response["HX-Trigger"] = json.dumps({"seizou:saved": {"message": text, "level": level}})
        return response

    def get_success_url_for(self, result) -> str:
        """保存の後の移り先。success_url → 保存した物の詳細 → 開いた元の画面 (?next=) → 一覧 → /。

        ?next= は一覧へ戻る形 (保存した物の詳細が無い物) にだけ効く。受注の登録のように保存した物の詳細へ移る形は今のまま。
        """
        if getattr(self, "success_url", None):
            return str(self.success_url)
        if result is not None and hasattr(result, "_meta") and getattr(result, "pk", None) is not None:
            url = model_url(result.__class__, "detail", result.pk)
            if url:
                return url
        if self.next_url:
            return self.next_url
        if self.model is not None:
            url = model_url(self.model, "list")
            if url:
                return url
        return "/"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("notice", None)
        context.setdefault("warnings", [])
        context["object"] = self.object
        context["in_modal"] = self.in_modal
        # 小窓では「キャンセル」を出さない (閉じるボタンだけ)。開いた元の画面 (?next=) があればそこへ、無ければ一覧へ戻す。
        context["next_url"] = self.next_url
        if self.in_modal:
            context["cancel_url"] = None
        else:
            context["cancel_url"] = context["next_url"] or (model_url(self.model, "list") if self.model is not None else None)
        return context


def use_decimal_input(field) -> None:
    """数の欄の数の入力 (type="number") を、字の欄に替える (小数は DecimalInput、整数は IntegerInput)。

    type="number" はブラウザが「12e」のような字を空にして送り、空のまま保存されるため (整数の欄 = 締日なども同じ。
    作者の決め)。字の欄にして、誤りはサーバーの検査が日本語で出す (整数の下限と上限は
    ServiceModelForm が表の検査から form の検査に付けてある)。全角の数字と「1,200」は半角の数にそろえて通す。
    """
    if isinstance(field, (django_forms.DecimalField, django_forms.FloatField)):
        make = DecimalInput
    elif isinstance(field, django_forms.IntegerField):
        make = IntegerInput
    else:
        return
    old = field.widget
    if not isinstance(old, django_forms.NumberInput):
        return
    attrs = {k: v for k, v in old.attrs.items() if k not in ("step", "min", "max")}
    widget = make(attrs=attrs)
    widget.is_required = old.is_required
    widget.is_localized = old.is_localized
    field.widget = widget


def use_clearable_file_widget(field) -> None:
    """ファイルの欄の ClearableFileInput を、input だけを描く ClearableFileWidget に替える (作者の決め)。

    Django の ClearableFileInput は「現在: <link>」「クリア」「変更:」を input と同じ所に描き、破線の枠 (core/_file_field.html) の
    中に並んでいた。替えた後は、枠の中は新規登録と同じ (input・絵・「ファイルを選ぶ か、ここへドラッグ」) で、
    「現在:」の link と「クリア」の check は枠の上に出る (部品の編集の図面・会社の情報のロゴ)。
    元の widget の attrs (accept・id など) と is_required を写す (use_decimal_input と同じ形)。is_required を写さないと、
    必須の欄にも「クリア」が出て、送られた「クリア」を消す (False) と読む。
    替えるのは ClearableFileInput の欄だけ。FileInput の子 (feedback の FileWidget) は当たらない。
    新規の入力の欄 (注文書の取り込み・CSV の取り込み・添付ファイル) は今のファイルが無いので、画面は今と同じ。
    """
    old = field.widget
    if not isinstance(old, django_forms.ClearableFileInput) or isinstance(old, ClearableFileWidget):
        return
    widget = ClearableFileWidget(attrs=dict(old.attrs))
    widget.is_required = old.is_required
    widget.is_localized = old.is_localized
    field.widget = widget


def use_label_required(name: str, field) -> None:
    """空のまま保存したときの字 (required) を、見出し入りの字にする (作者の決め)。

    決め 11-06 18:3x「全部の画面の「空のまま」の字を、まとめて「<見出し>を入れてください。」
    (選ぶ欄は「<見出し>を選んでください。」) に替える」。「このフィールドは必須です。」では何を入れるか分からず、
    「フィールド」は事務の言葉でない (作者の決め)。欄を見つけるたびに直さずに済むよう、入力の土台でまとめて替える。
    替えるのは required の字が Django の既定のままの欄だけ。欄ごとに書いた字 (出荷日・休日・文字コード・受信した注文など) は
    そのまま残す。欄ごとに直したい字は、今までどおり forms.py の error_messages に書く。隠した欄は字が画面に出ないので替えない。
    字は欄の class で選ぶ (widget では選ばない): 1 つの checkbox (BooleanField) は「<見出し>に チェックを入れてください。」、
    選ぶ欄 (ChoiceField の子) とファイルの欄 (FileField の子) は「<見出し>を選んでください。」、ほか (字・数・日付) は
    「<見出し>を入れてください。」。見出しの無い欄は、画面の見出しと同じ pretty_name(name)。
    form ごとの写しの欄の error_messages を書き換えるので、class の欄 (base_fields) と次の要求には残らない。
    届かない所: 受注と発注の明細の行 (formset。get_form は親の form の欄だけを回す) と、BaseFormView を使わない form (ログインなど)。
    """
    if field.widget.is_hidden:
        return
    if str(field.error_messages.get("required", "")) != str(django_forms.Field.default_error_messages["required"]):
        return
    label = field.label if field.label is not None else pretty_name(name)
    if isinstance(field, django_forms.BooleanField):
        message = f"{label}に チェックを入れてください。"
    elif isinstance(field, (django_forms.ChoiceField, django_forms.FileField)):
        message = f"{label}を選んでください。"
    else:
        message = f"{label}を入れてください。"
    field.error_messages["required"] = message


def _accepts_instance(form_class) -> bool:
    """form が instance= を受けるか。ModelForm (ServiceModelForm を含む) か、__init__ が instance の引数を名指ししている物だけ。

    以前は form_class.__init__ の co_varnames を見ていたが、*args と **kwargs だけの __init__ (ServiceModelForm) を足したときに
    編集の画面が空で開く不具合になったので、形で判断する。
    """
    if issubclass(form_class, django_forms.ModelForm):
        return True
    try:
        parameters = inspect.signature(form_class.__init__).parameters
    except (TypeError, ValueError):
        return False
    return "instance" in parameters


def widget_css(widget) -> str:
    """欄の見た目の class (daisyUI)。form が class を持っていればそちらが勝つ (setdefault)。"""
    from django.forms import widgets

    if isinstance(widget, widgets.CheckboxInput):
        return "checkbox"
    if isinstance(widget, RemoteSelect):
        # 字を打って探す欄 (Tom Select) には daisyUI の select の class を付けない。Tom Select が包みに class を写し、
        # select の見た目 (overflow と矢印) が候補の一覧を隠して二重の枠と矢印になるため (作者の決め)。
        # 包みの見た目は scripts/css/input.css の .ts-wrapper の決まりで input にそろえる。
        return "w-full"
    if isinstance(widget, (widgets.Select, widgets.SelectMultiple)):
        return "select w-full"
    if isinstance(widget, widgets.Textarea):
        return "textarea w-full"
    if isinstance(widget, widgets.FileInput):
        return "file-input w-full"
    return "input w-full"


def with_saved_note(message: str, result) -> str:
    """入力の土台の知らせの字。service が返した物に saved_message (字) があれば「保存しました。」をその字に置き換え、
    saved_note (1 文の字) があれば知らせの後ろに付ける。

    例: 受信した注文と読み取りを受注にした直後の「明細 n 行を手入力せずに受注にしました。」(orders.services.create_order が付ける)。
    部品の画面は知らせの文を上書きできない (R15 の 11 に success_message が無い) ので、文と色は service の返す物が持つ
    (色は saved_level。"warning" なら form_valid が黄 (確認してください) の知らせにする)。
    """
    saved_message = getattr(result, "saved_message", "")
    if isinstance(saved_message, str) and saved_message:
        message = saved_message
    note = getattr(result, "saved_note", "")
    return f"{message}{note}" if isinstance(note, str) and note else message
