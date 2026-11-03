"""設定の画面 (/settings/。右上の利用者のメニューの「設定」から開く)。

普段は触らない物を、左のメニューでなくここにまとめる (本人の指摘 11-03 昼「普段簡単に触らないものは、設定をいじれる所に」):
会社の選べる設定の今の値と出所 (画面 / 答え (settings.py) / 既定。アカウント管理の権限がある人は行の「編集」から
1 つずつ変え、「答えの値に戻す」で画面の値を消す。menu は答えの YAML で書く)、取引先との接続 (つながり) とアカウント管理 (入っている会社だけ) の入口、フィードバックの一覧の入口 (入っている会社だけ)、システムの状態 (開発の設定・自動ログイン・
ファイルの配り方・DB・AI の鍵の有無・MCP の利用者の有無・入っている部品・点検 (ボタンで走らせる))。鍵の値は出さない (有るか無いかだけ)。
"""

from __future__ import annotations

import os
from importlib import import_module

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from seizou.core import access, conf, doctor, services
from seizou.core.forms import SettingForm
from seizou.core.models import SettingOverride
from seizou.core.views import BaseFormView, ScreenMixin, url_or_none
from seizou.parts import PARTS

#: 選べる設定の見出しと、値の読み方 (README の「選べる設定」の表と同じ言葉)
SETTING_LABELS = {
    "order_form": ("受注の形", {"multi": "1 件に明細が複数", "single": "1 件 1 品"}),
    "partial_shipment": ("分納", {True: "許す", False: "許さない"}),
    "over_shipment": ("受注より多い出荷 (R01・R04)", {"warn": "知らせる", "stop": "止める", "off": "通す"}),
    "over_workorder": ("受注より多い指示書の数量と予定より多い実績 (R06・R07)", {"warn": "知らせる", "stop": "止める", "off": "通す"}),
    "order_number": ("受注番号の形", {"seq": "連番", "ym_seq": "年月 + 連番"}),
    "production_plan": ("生産計画の画面", {True: "使う", False: "使わない"}),
    "weekly_holidays": ("定休日の曜日 (0 = 月 … 5 = 土・6 = 日。祝日と会社の休みはマスターの「休日」)", {}),
    "hidden_fields": ("使わない注文の項目", {}),
    "access_control": ("権限の判定 (部署・編集の権限・金額の閲覧)", {"off": "使わない", "log": "記録だけ", "on": "有効"}),
    "assistant_model": ("AI チャットが使う model", {}),
    "intake_model": ("注文書の読み取りが使う model", {}),
    "feedback_model": ("フィードバックの AI の聞き取りが使う model", {}),
    "menu": ("左のメニューの括り (空なら既定の並び)", {}),
    "brand_color": ("画面の主色 (# と 16 進の 6 桁。空なら土台の色)", {}),
    "industry": ("業種 (左上の会社の名前の下)", {}),
}

#: 辞書の設定の中の鍵の日本語 (権限の判定の 3 つ。知らない鍵はそのまま出す)
SUB_LABELS = {"department": "部署", "write": "編集の権限", "money": "金額の閲覧"}


def hideable_fields() -> tuple:
    """受注の入力から外せる項目の候補 ((名前, 見出し) の並び)。hidden_fields の欄の choices。

    orders の forms.hideable_fields() を importlib で引く (core は部品を import しない。core/search.py の providers と同じ形)。
    orders が入っていない会社と、関数の無い orders では空の並び。module の import の時には引かない (呼ばれた時だけ)。
    """
    if not apps.is_installed("seizou.orders"):
        return ()
    try:
        provider = getattr(import_module("seizou.orders.forms"), "hideable_fields")
    except (ImportError, AttributeError):
        return ()
    return tuple(provider())


#: 設定の変更を断る 403 の画面の文
SETTINGS_DENIED_TEXT = "「設定の変更」の画面です。アカウント管理の権限が要ります。" + access.CONTACT
#: 画面から変えるのはシステム管理者だけの鍵。アカウント管理の権限 1 つで 3 つの判定を外せないように。
#: LOCKED_KEYS と違い、鍵は画面にあり、システム管理者は変えられる (精査 A2 の S5)
SUPERUSER_ONLY_KEYS = ("access_control",)
#: システム管理者だけの鍵を変えようとした人に出す 403 の画面の文
SUPERUSER_ONLY_TEXT = "「権限の判定」は、システム管理者だけが画面から変えられます。変えるときはシステム管理者に頼んでください。"

ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
MCP_USER_ENV = "SEIZOU_MCP_USER"

#: 画面に出す「直す」の行の数 (点検の表の順の最初から。残りは命令 seizou_doctor で見る)
DOCTOR_SHOWN = 3


def _display(key: str, value) -> str:
    """値の日本語。選べる値は表から、一覧は「・」で、辞書は「中の鍵: 値」を「・」で、空は「なし」。"""
    _label, words = SETTING_LABELS.get(key, (key, {}))
    if isinstance(value, (list, tuple)):
        return "・".join(str(v) for v in value) if value else "なし"
    if isinstance(value, dict):
        # 辞書の設定 (例: 権限の判定 access_control = {"department": "log", ...})。中の値は同じ鍵の表で日本語にする。
        return "・".join(f"{SUB_LABELS.get(k, k)}: {_display(key, v)}" for k, v in value.items()) if value else "なし"
    if value in words:
        return f"{words[value]} ({value})"
    return str(value)


def company_settings() -> list[dict]:
    """選べる設定の今の値の並び (鍵・見出し・値の字・出所の字・画面から変えられるか)。"""
    rows = []
    for key in conf.DEFAULTS:
        label, _words = SETTING_LABELS.get(key, (key, {}))
        rows.append(
            {
                "key": key,
                "label": label,
                "value": _display(key, conf.setting(key)),
                "source": conf.setting_source(key),
                "editable": key not in conf.LOCKED_KEYS,
            }
        )
    return rows


def _database_name() -> str:
    engine = settings.DATABASES.get("default", {}).get("ENGINE", "")
    if "postgresql" in engine:
        return "PostgreSQL"
    if "sqlite" in engine:
        return "SQLite"
    return engine.rsplit(".", 1)[-1] or "不明"


def _override_count() -> str:
    """画面で上書きした設定の数の字。表が読めないとき (migrate の前の置き場・DB を止めた試験) は「—」。"""
    try:
        count = SettingOverride.objects.count()
    except (DatabaseError, RuntimeError):
        # RuntimeError は pytest-django が db の無い試験で DB を止めた時の例外 (conf.overrides と同じ扱い)
        return "—"
    return f"{count} 件"


def system_rows() -> list[dict]:
    """システムの状態 (読むだけ。鍵の値は出さない)。"""
    from seizou.core import media, middleware

    delivery = media.delivery()
    rows = [
        {"label": "開発の設定 (DEBUG)", "value": "開発 (1)" if settings.DEBUG else "本番 (0)"},
        {
            "label": "開発機の自動ログイン",
            "value": "動いている (ログインの画面を飛ばして管理者として開く)" if middleware.dev_auto_login_enabled() else "動いていない",
        },
        {
            "label": "上げたファイルの配り方",
            "value": "nginx に渡す (X-Accel-Redirect)" if delivery == media.DELIVERY_NGINX else "Django が送る",
        },
        {"label": "データベース", "value": _database_name()},
        {"label": "画面で上書きした設定", "value": _override_count()},
        {
            "label": "AI の鍵 (注文書の読み取り・AI チャット)",
            "value": "ある" if os.environ.get(ANTHROPIC_KEY_ENV, "").strip() else "ない (読み取りとチャットは使えない)",
        },
        {
            "label": "MCP の書く道具の利用者",
            "value": "決めてある" if os.environ.get(MCP_USER_ENV, "").strip() else "決めていない (書く道具は断る)",
        },
    ]
    values = getattr(settings, "SEIZOU", {}) or {}
    if values.get("id"):
        rows.insert(0, {"label": "会社の id とポート", "value": f"{values.get('id')} / {values.get('port', '—')}"})
    return rows


def doctor_summary(rows) -> dict:
    """点検の結果の要点。rows は doctor.run() の並び (Row)。shown は直す物の最初の DOCTOR_SHOWN 件 (点検の表の順)。

    読むだけで、結果を保存しない (表にも session にも書かない)。production は本番の条件か (DEBUG が偽)。
    """
    fixes = [row for row in rows if not row.ok]
    return {"rows": list(rows), "fixes": fixes, "shown": fixes[:DOCTOR_SHOWN], "production": doctor.is_production()}


class SettingsView(ScreenMixin, TemplateView):
    """設定の画面。選べる設定を変えるのは、行の「編集」から開く設定の変更の画面 (SettingUpdateView)。

    使う人: 総務 / いつ: 会社の動きや取引先との接続を見直すとき / 何のために: 選べる設定の今の値と出所、普段は触らない物の入口、
    システムの状態を 1 か所で見る (アカウント管理の担当者は「点検を走らせる」で直す物も見る) / 次にすること: 行の「編集」で値を変えるか、取引先との接続やアカウント管理へ進む
    """

    screen_name = "設定"
    screen_help = "会社の選べる設定の今の値と、普段は触らない物 (取引先との接続・アカウント管理) の入口、システムの状態。アカウント管理の担当者は「点検を走らせる」で、命令 seizou_doctor と同じ点検の直す物を見る。"
    screen_icon = "settings"
    template_name = "core/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("doctor", None)  # GET では None (まだ走らせていない)。POST は doctor= で渡す
        context["company_settings"] = company_settings()
        if not self.request.user.is_superuser:
            # システム管理者だけの鍵は、ほかの人に「編集」の link を出さない (押すと 403 になるため)
            for row in context["company_settings"]:
                if row["key"] in SUPERUSER_ONLY_KEYS:
                    row["editable"] = False
        context["links"] = [
            {
                "label": "取引先との接続 (つながり)",
                "help": "取引先ごとの受け口の URL と鍵の置き場の名前。鍵の設定が済みかまだかを見る。",
                "url": url_or_none("connect:connection_list"),
                "icon": "inbox",
            },
            {
                "label": "添付ファイルの一覧 (整理用)",
                "help": "各詳細の「添付ファイル」の枠から付けたファイルを全部まとめて探す。付けるのは各詳細から。",
                "url": url_or_none("attachments:attachment_list"),
                "icon": "paperclip",
            },
            {
                "label": "アカウント管理",
                "help": "アカウントと部署と権限。権限管理の部品が入っている会社だけ。",
                "url": url_or_none("accounts:access_list"),
                "icon": "users",
            },
        ]
        if apps.is_installed("seizou.feedback"):
            context["links"].append(
                {
                    "label": "フィードバックの一覧",
                    "help": "現場の人が伝えた困りごと・要望・不具合の下書き。自分の分は全員、全部はアカウント管理の担当者が見る。伝えるのは左のメニューの下の「フィードバック」から。",
                    "url": url_or_none("feedback:feedback_list"),
                    "icon": "pencil",
                }
            )
        from seizou.core import access

        context["can_edit"] = access.can_manage_accounts(self.request.user)
        # アカウント管理の入口は、アカウント管理の権限がある人と管理者にだけ出す (無い人が押すと 403 になるため)
        if not access.can_manage_accounts(self.request.user):
            context["links"] = [link for link in context["links"] if link["label"] != "アカウント管理"]
        context["system_rows"] = system_rows()
        context["parts"] = [part.rsplit(".", 1)[-1] for part in PARTS]
        return context

    def post(self, request, *args, **kwargs):
        """「点検を走らせる」。core/doctor.run をその場で呼び、結果を同じ画面に出す (保存しない)。アカウント管理の担当者だけ。"""
        denied = settings_denied(request)
        if denied is not None:
            return denied
        return self.render_to_response(self.get_context_data(doctor=doctor_summary(doctor.run())))


def settings_denied(request):
    """設定の変更の門。変えられない人には 403 の応答、変えられる人と未ログインは None。

    変えられるのはアカウント管理の権限かフルコントロールかシステム管理者 (access.can_manage_accounts。3 状態の外で、
    いつも効く)。権限の部品が無い会社ではシステム管理者だけ。未ログインをログインへ移すのは土台 (LoginRequiredMixin)。
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    if access.can_manage_accounts(user):
        return None
    return access.denied_response(request, access.DENIED_HEADING, SETTINGS_DENIED_TEXT)


def superuser_only_denied(request, key):
    """システム管理者だけが変える鍵 (SUPERUSER_ONLY_KEYS) の門。変えられない人には 403 の応答、ほかと未ログインは None。"""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    if key in SUPERUSER_ONLY_KEYS and not user.is_superuser:
        return access.denied_response(request, access.DENIED_HEADING, SUPERUSER_ONLY_TEXT)
    return None


class SettingUpdateView(BaseFormView):
    """設定の変更 (選べる設定 1 つの入力。URL の key が鍵)。

    使う人: アカウント管理の担当者 / いつ: 会社の動きを変えると決めたとき / 何のために: 答えの YAML を直さずに設定を変える /
    次にすること: 設定の画面で出所が「画面」になった事を見る。
    model は持たない (self.object は None のまま)。土台の call_service が set_setting(user=..., key=..., value=...) を呼ぶ。
    """

    screen_name = "設定の変更"
    screen_help = "会社の選べる設定を 1 つ変える。保存した値は答えの YAML より優先され、「答えの値に戻す」で答えの値に戻る。"
    screen_icon = "settings"
    form_class = SettingForm
    service = staticmethod(services.set_setting)
    template_name = "core/settings_form.html"
    success_url = reverse_lazy("core:settings")

    def dispatch(self, request, *args, **kwargs):
        denied = settings_denied(request)
        if denied is not None:
            add_never_cache_headers(denied)
            return denied
        # 知らない鍵と画面から変えない鍵は 404 (土台の form_valid は KeyError を知らせにしないので、ここで止める)
        key = kwargs.get("key")
        if key not in conf.DEFAULTS or key in conf.LOCKED_KEYS:
            raise Http404("画面から変えられる設定の鍵ではありません。")
        denied = superuser_only_denied(request, key)
        if denied is not None:
            add_never_cache_headers(denied)
            return denied
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["key"] = self.kwargs["key"]
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        key = self.kwargs["key"]
        context["setting_key"] = key
        context["setting_label"] = SETTING_LABELS.get(key, (key, {}))[0]
        context["source"] = conf.setting_source(key)
        context["can_clear"] = context["source"] == conf.SOURCE_SCREEN
        # 土台は model が無いと cancel_url を None にするので、設定の画面へ戻す
        context["cancel_url"] = reverse("core:settings")
        return context


@login_required
@require_POST
def setting_clear(request, key):
    """「答えの値に戻す」の口。画面の上書きを消し、設定の画面へ戻る。"""
    denied = settings_denied(request)
    if denied is not None:
        return denied
    if key not in conf.DEFAULTS or key in conf.LOCKED_KEYS:
        raise Http404("画面から変えられる設定の鍵ではありません。")
    denied = superuser_only_denied(request, key)
    if denied is not None:
        add_never_cache_headers(denied)
        return denied
    try:
        services.clear_setting(key, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "答えの値に戻しました。")
    return HttpResponseRedirect(reverse("core:settings"))
