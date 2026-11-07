"""設定の画面 (/settings/。右上の利用者のメニューの「設定」から開く)。

普段は触らない物を、左のメニューでなくここにまとめる (本人の指摘 11-03 昼「普段簡単に触らないものは、設定をいじれる所に」):
会社の選べる設定の今の値と出所 (この画面で変えた / 導入の時に決めた / 初期値。アカウント管理の権限がある人は行の「編集」から
1 つずつ変え、「答えの値に戻す」で画面の値を消す。conf.LOCKED_KEYS の 3 つは画面から変えず、導入の時に答えの YAML で書く)、取引先との接続 (つながり) とアカウント管理 (入っている会社だけ) の入口、フィードバックの一覧の入口 (入っている会社だけ)、システムの状態 (開発の設定・自動ログイン・
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
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.cache import add_never_cache_headers
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from seizou.core import access, conf, display, doctor, history, mail, services
from seizou.core.forms import CompanyForm, DisplaySettingsForm, SettingForm
from seizou.core.models import SettingOverride
from seizou.core.views import BaseFormView, ScreenMixin, url_or_none
from seizou.parts import PARTS

#: 選べる設定の見出しと、値の読み方 (README の「選べる設定」の表と同じ言葉)
SETTING_LABELS = {
    "order_form": ("受注の形", {"multi": "1 件に明細が複数", "single": "1 件 1 品"}),
    "partial_shipment": ("分納", {True: "許す", False: "許さない"}),
    "over_shipment": ("受注より多い出荷 (R01・R04)", {"warn": "知らせる", "stop": "止める", "off": "通す"}),
    "over_workorder": ("受注より多い指示書の数量と予定より多い実績 (R06・R07)", {"warn": "知らせる", "stop": "止める", "off": "通す"}),
    "receiving_required": ("支給品と預かり品の入荷が無い明細の指示書の発行と出荷", {"warn": "知らせる", "stop": "止める", "off": "通す"}),
    "over_payment": ("請求書の残を超える入金 (R24)", {"warn": "知らせる", "stop": "止める", "off": "通す"}),
    "order_number": ("受注番号の形", {"seq": "連番", "ym_seq": "年月 + 連番"}),
    "transcription_minutes_per_line": ("1 行の手入力にかかる分", {}),
    "production_plan": ("生産計画の画面", {True: "使う", False: "使わない"}),
    "weekly_holidays": ("定休日の曜日", {}),
    "hidden_fields": ("使わない注文の項目", {}),
    "access_control": ("権限の判定 (部署・編集の権限・金額の閲覧)", {"off": "使わない", "log": "記録だけ", "on": "有効"}),
    "assistant_model": ("AI チャットが使う AI の種類", {}),
    "intake_model": ("注文書の読み取りが使う AI の種類", {}),
    "feedback_model": ("フィードバックの AI の聞き取りが使う AI の種類", {}),
    "menu": ("左のメニューの括り", {}),
    "brand_color": ("画面の主色", {}),
    "industry": ("業種", {}),
    "mail": ("メールの送り方", {}),
}

#: 設定の名前の下に小さな字で出す 2 行目 (名前の括弧の説明を分けた物。初見テスト R5 の V99・U101。候補 C14)。
#: 設定の画面の表の「設定」の列と、設定の変更の画面の欄の注記 (forms の SettingForm の help_text) に出す。
#: SETTING_LABELS の (名前, 値の日本語) の 2 つ組の形は変えない (2 つに分けて読む所がそのまま動くように)
SETTING_NOTES = {
    "transcription_minutes_per_line": "手入力せずに取り込んだ明細の「入力を省けた時間 ≒ m 分」",
    "weekly_holidays": "0 = 月 … 5 = 土・6 = 日。祝日と会社の休みはマスターの「休日」",
    "menu": "空なら初期の並び",
    "brand_color": "# と 16 進の 6 桁。空なら土台の色",
    "industry": "左上の会社の名前の下に出る。空なら seizou",
    "mail": "取引先から会話が届いたときの知らせのメール。パスワードは画面に入れず .env に置く",
}

#: 画面から変えない設定 (conf.LOCKED_KEYS) の 2 行目の終わりに足す字。「編集」の列は空のまま (折れない列に字を足すと列が広がるため)
LOCKED_NOTE = "画面からは変えない (導入の時に決める)"

#: 辞書の設定の中の鍵の日本語 (権限の判定の 3 つとメールの送り方の 7 つ。知らない鍵はそのまま出す)
SUB_LABELS = {
    "department": "部署",
    "write": "編集の権限",
    "money": "金額の閲覧",
    "host": "サーバー",
    "port": "ポート",
    "security": "暗号化の方式",
    "user": "アカウント",
    "from": "差出人",
    "from_name": "差出人の名前",
    "site_url": "seizou の URL の頭",
}

#: メールの送り方の暗号化の方式 (conf.MAIL_SECURITY) の日本語 (設定の画面の表と、設定の変更の画面の丸の字)
MAIL_SECURITY_LABELS = {"starttls": "STARTTLS", "ssl": "SSL/TLS", "none": "暗号化しない"}


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
    """値の日本語。選べる値は表の日本語だけ (英字の値は添えない。答えの YAML と見比べるのは表の「鍵」の列)、一覧は「・」で、辞書は「中の鍵: 値」を「・」で、空は「なし」。"""
    _label, words = SETTING_LABELS.get(key, (key, {}))
    if isinstance(value, (list, tuple)):
        return "・".join(str(v) for v in value) if value else "なし"
    if isinstance(value, dict):
        return "・".join(_dict_parts(key, value)) if value else "なし"
    if value in words:
        return words[value]
    return str(value)


def _dict_parts(key: str, value: dict) -> list[str]:
    """辞書の設定の「中の鍵: 値」の字の並び (例: 権限の判定 access_control = {"department": "log", ...} は「部署: 記録だけ」など 3 つ)。

    中の値は同じ鍵の表で日本語にする。並びは SUB_LABELS の順に固定する (PostgreSQL の jsonb は鍵を並べ替えて返すので、
    保存した並びに頼らない)。知らない鍵はその後ろ。_display はこれを「・」でつなぎ、設定の画面の表は塊ごとに出す
    (640px 以上の幅では塊の途中で折らない。初見テスト R5 の a-blind-08。候補 C14)。
    メールの送り方 (mail) の中の値は _display の表を通さない (サーバーの名前やアカウントが選べる値の字に化けないように)。
    暗号化の方式だけを MAIL_SECURITY_LABELS で引き、ほかは字のまま、空の字は「なし」にする。
    """
    keys = [k for k in SUB_LABELS if k in value] + [k for k in value if k not in SUB_LABELS]
    if key == "mail":
        return [f"{SUB_LABELS.get(k, k)}: {_mail_value(k, value[k])}" for k in keys]
    return [f"{SUB_LABELS.get(k, k)}: {_display(key, value[k])}" for k in keys]


def _mail_value(sub_key: str, value) -> str:
    """メールの送り方の中の値 1 つの字。暗号化の方式は日本語の字、空の字は「なし」、ほかは字のまま。"""
    if sub_key == "security":
        return MAIL_SECURITY_LABELS.get(value, str(value))
    if value == "":
        return "なし"
    return str(value)


def setting_note(key: str) -> str:
    """設定の名前の下の 2 行目の字 (SETTING_NOTES)。画面から変えない鍵 (conf.LOCKED_KEYS) は終わりに LOCKED_NOTE を足す。無ければ空の字。

    画面から変えない鍵は conf.LOCKED_KEYS で分ける (row の editable はシステム管理者でない人の権限の判定の行も偽にするため)。
    """
    note = SETTING_NOTES.get(key, "")
    if key in conf.LOCKED_KEYS:
        return f"{note}。{LOCKED_NOTE}" if note else LOCKED_NOTE
    return note


def company_settings() -> list[dict]:
    """選べる設定の今の値の並び (鍵・見出し・2 行目の字・値の字・値の塊・出所の字・画面から変えられるか)。

    値の塊 (value_parts) は辞書の設定 (権限の判定・メールの送り方) だけの「中の鍵: 値」の並び。ほかの設定と空の辞書は空の並びで、表は値の字を出す。
    メールの送り方は conf.mail_settings() で読む (答えに一部だけ書いた会社でも 7 つの塊で出す)。
    """
    rows = []
    for key in conf.DEFAULTS:
        label, _words = SETTING_LABELS.get(key, (key, {}))
        value = conf.mail_settings() if key == "mail" else conf.setting(key)
        rows.append(
            {
                "key": key,
                "label": label,
                "note": setting_note(key),
                "value": _display(key, value),
                "value_parts": _dict_parts(key, value) if isinstance(value, dict) and value else [],
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
        # app = 入口の先の部品。部署でその部品を開けない人には入口を出さない (押すと 403 になる入口を出さない = 見つけ GR-03。
        # 判定は左のメニューと同じ access.can_open。記録は書かない)。無い物は部署を見ない (core の画面と、いつも開ける部品)。
        context["links"] = [
            {
                "label": "取引先との接続 (つながり)",
                "help": "取引先ごとの受け口の URL と鍵の置き場の名前。鍵の設定が済みかまだかを見る。",
                "url": url_or_none("connect:connection_list"),
                "icon": "inbox",
                "app": "connect",
            },
            {
                # 送り合った記録 (決め HIST-1 の edi・C106-1)。取引先との接続と同じく普段は触らないので、左のメニューでなくここから開く
                "label": "送り合った記録",
                "help": "取引先と送り合った注文・注文回答・出荷の知らせ・検収の中身を、送ったとおりに残した一覧。",
                "url": url_or_none("connect:exchange_list"),
                "icon": "inbox",
                "app": "connect",
            },
            {
                "label": "添付ファイルの一覧 (整理用)",
                "help": "各詳細の「添付ファイル」の枠から付けたファイルを全部まとめて探す。付けるのは各詳細から。",
                "url": url_or_none("attachments:attachment_list"),
                "icon": "paperclip",
            },
            {
                "label": "アカウント管理",
                "help": "アカウントと部署と権限。",
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
        for link in context["links"]:
            if link.get("app") and not access.can_open(self.request.user, link["app"], record=False):
                link["url"] = None
        context["system_rows"] = system_rows()
        context["parts"] = [part.rsplit(".", 1)[-1] for part in PARTS]
        # メールの知らせ: パスワードは置いてあるかだけ (値は渡さない)。送り方は 1 回だけ読む
        mail_values = conf.mail_settings()
        context["mail"] = {
            "has_password": mail.has_password(),
            "missing": mail.missing(),
            "from": mail_values["from"],
            "from_name": mail_values["from_name"],
        }
        # 設定の履歴 (決め HIST-1 の「会社の設定」。会社の表と設定の上書きの変更の履歴。会社の設定に詳細の画面は無いので、ここに同じ部品で出す)
        context["settings_history"] = history.settings_timeline(self.request.user)
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
        if key == "mail":
            # 入れる人が URL の頭を探さずに済むように、今この画面を開いている URL の頭を見せる (見せるだけで保存しない)
            context["site_url_hint"] = self.request.build_absolute_uri("/").rstrip("/")
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


@login_required
@require_POST
def mail_test(request):
    """「試しに送る」の口。差出人のアドレスへ試しのメールを送り、設定の画面へ戻る (何も保存しない)。門は設定の変更と同じ settings_denied (アカウント管理の権限)。"""
    denied = settings_denied(request)
    if denied is not None:
        return denied
    try:
        address = mail.send_test()
    except mail.MailNotSent as exc:
        messages.error(request, f"試しのメールを送れません。{exc}")
    else:
        messages.success(request, f"試しのメールを {address} へ送りました。メールが届いたかを確かめてください。")
    return HttpResponseRedirect(reverse("core:settings"))


class CompanyUpdateView(BaseFormView):
    """会社の情報の変更 (納品書と請求書の会社の欄に出す住所・電話・振込先・登録番号・ロゴ)。

    使う人: 総務 / いつ: 使い始めるときと、住所や振込先や登録番号が変わったとき / 何のために: 納品書と請求書の会社の欄に出す住所・電話・振込先・登録番号・ロゴを入れる / 次にすること: 設定の画面の「会社の情報」で確かめ、納品書か請求書の印刷で見る
    対象はいつも会社の 1 行 (services.company())。土台の call_service が update_company(会社, user=..., data=...) を呼ぶ。
    門は設定の変更と同じ settings_denied (アカウント管理の権限。3 状態の外)。form_valid の require_write も core.update_company (ACCOUNT_ACTIONS) で 2 重に断る。

    登録番号はインボイス制度 (適格請求書に発行する事業者の名前と登録番号を書く) のための欄で、請求書だけに出す (決め LAW-3)。形 (T と 13 桁の数字) の違う番号は止める (決め C95-2)。
    """

    screen_name = "会社の情報の変更"
    screen_help = "納品書と請求書の会社の欄に出す住所・電話・振込先・登録番号・ロゴを入れる。振込先と登録番号は請求書だけに出る。保存すると設定の画面へ戻る。"
    screen_icon = "settings"
    form_class = CompanyForm
    service = staticmethod(services.update_company)
    success_url = reverse_lazy("core:settings")

    def dispatch(self, request, *args, **kwargs):
        denied = settings_denied(request)
        if denied is not None:
            add_never_cache_headers(denied)
            return denied
        if request.user.is_authenticated:
            self.object = services.company()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 土台は model が無いと cancel_url を None にするので、設定の画面へ戻す
        context["cancel_url"] = reverse("core:settings")
        return context


class DisplaySettingsView(BaseFormView):
    """表示の設定 (人ごとの見た目。今は上の帯 = ヘッダーの色だけ。決め 11-05 21:2x。N167)。

    使う人: ログインする全員 / いつ: 使い始めるときと、ほかの人のアカウントでも入る端末を使うとき / 何のために: 上の帯を自分だけの色にし、ほかの人のアカウントで入ったままの画面に目で気づく / 次にすること: 見本を押すと保存され、画面が読み直されて上の帯の色が変わる

    右上の利用者のメニューの「表示の設定」から小窓で開く (data-modal-form)。小窓の中身は見本のカード (core/_display_settings_card.html)。
    画面の全体 (新しいタブ・URL の直打ち・JS の無い窓。template_name = core/display_settings.html) も同じカードで描き、見本を選んで「保存」で保存して同じ画面へ戻る (C53)。
    土台の call_service が set_user_preference(user=本人, key=..., value=...) を呼ぶ (本人の行だけ。権限の表では FREE_ACTIONS)。
    小窓の保存の後は、204 に HX-Refresh を付けて画面の全体を読み直す (上の帯の色は <html> の属性なので、seizou:refresh の取り直しでは変わらない)。
    """

    screen_name = "表示の設定"
    screen_help = "上の帯 (ヘッダー) の色を、自分の画面だけで選ぶ。見本を押すとすぐ保存され、画面を読み直す。ほかの人の画面の色は変わらない。"
    screen_icon = "eye"
    form_class = DisplaySettingsForm
    service = staticmethod(services.set_user_preference)
    success_message = "表示の設定を保存しました。"
    success_url = reverse_lazy("core:display_settings")
    template_name = "core/display_settings.html"  # 画面の全体も見本のカードで描く (C53)。小窓の形は土台の get_template_names のまま
    extra_context = {"form_card": "core/_display_settings_card.html"}

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault(display.HEADER_COLOR, services.user_preference(self.request.user, display.HEADER_COLOR, display.HEADER_COLOR_DEFAULT))
        return initial

    def saved_in_modal(self, text: str, level: str) -> HttpResponse:
        """小窓の保存の後: 本文の無い 204 に HX-Refresh を付け、htmx に画面の全体を読み直させる (知らせは出さない。色が変わるのが知らせ)。"""
        response = HttpResponse(status=204)
        response["HX-Refresh"] = "true"
        return response
