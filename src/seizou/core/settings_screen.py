"""設定の画面 (/settings/。右上の利用者のメニューの「設定」から開く)。

普段は触らない物を、左のメニューでなくここにまとめる (本人の指摘 11-03 昼「普段簡単に触らないものは、設定をいじれる所に」):
会社の選べる設定の今の値 (読むだけ。変えるのは答えの YAML か settings.py。画面から変える形は次の版)、
取引先との接続 (つながり) とアカウント管理 (入っている会社だけ) の入口、システムの状態 (開発の設定・自動ログイン・
ファイルの配り方・DB・AI の鍵の有無・MCP の利用者の有無・入っている部品)。鍵の値は出さない (有るか無いかだけ)。
"""

from __future__ import annotations

import os

from django.conf import settings
from django.views.generic import TemplateView

from seizou.core import conf
from seizou.core.views import ScreenMixin, url_or_none
from seizou.parts import PARTS

#: 選べる設定の見出しと、値の読み方 (README の「選べる設定」の表と同じ言葉)
SETTING_LABELS = {
    "order_form": ("受注の形", {"multi": "1 件に明細が複数", "single": "1 件 1 品"}),
    "partial_shipment": ("分納", {True: "許す", False: "許さない"}),
    "over_shipment": ("受注より多い出荷 (R01・R04)", {"warn": "知らせる", "stop": "止める", "off": "通す"}),
    "over_workorder": ("受注より多い指示書の数量と予定より多い実績 (R06・R07)", {"warn": "知らせる", "stop": "止める", "off": "通す"}),
    "order_number": ("受注番号の形", {"seq": "連番", "ym_seq": "年月 + 連番"}),
    "production_plan": ("生産計画の画面", {True: "使う", False: "使わない"}),
    "hidden_fields": ("使わない注文の項目", {}),
    "access_control": ("権限の判定 (部署・編集の権限・金額の閲覧)", {"off": "使わない", "log": "記録だけ", "on": "有効"}),
}

#: 辞書の設定の中の鍵の日本語 (権限の判定の 3 つ。知らない鍵はそのまま出す)
SUB_LABELS = {"department": "部署", "write": "編集の権限", "money": "金額の閲覧"}

ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
MCP_USER_ENV = "SEIZOU_MCP_USER"


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
    """選べる設定の今の値の並び (鍵・見出し・値の字)。"""
    rows = []
    for key in conf.DEFAULTS:
        label, _words = SETTING_LABELS.get(key, (key, {}))
        rows.append({"key": key, "label": label, "value": _display(key, conf.setting(key))})
    return rows


def _database_name() -> str:
    engine = settings.DATABASES.get("default", {}).get("ENGINE", "")
    if "postgresql" in engine:
        return "PostgreSQL"
    if "sqlite" in engine:
        return "SQLite"
    return engine.rsplit(".", 1)[-1] or "不明"


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


class SettingsView(ScreenMixin, TemplateView):
    """設定の画面。読むだけ。"""

    screen_name = "設定"
    screen_help = "会社の選べる設定の今の値と、普段は触らない物 (取引先との接続・アカウント管理) の入口、システムの状態。"
    screen_icon = "settings"
    template_name = "core/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["company_settings"] = company_settings()
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
        context["system_rows"] = system_rows()
        context["parts"] = [part.rsplit(".", 1)[-1] for part in PARTS]
        return context
