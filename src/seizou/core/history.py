"""変更の履歴を読む関数 (決め HIST-0・HIST-1・C40-1)。表 (core.models.ChangeLog) には書かない。書くのは core.services の
record_history と record_history_deleted だけ。

- keeps_history(model): model の class 属性 keeps_history が真か、history_owner があれば真 (印は model の class に置く。
  手本は core.views.hidden_detail_fields が読む detail_hidden_fields。class 属性なので migration は要らない)。
- owner_of(obj): 履歴を出す詳細の行。history_owner (点でつないだ道か、道の並び = 最初に行のある物) を順に辿る。無ければ自分。
- saved_state(obj): DB から取り直した行の値 ({名前: {"label", "type", "value"}})。渡された物の値は読まない。
- timeline(obj, user, *, limit=100): 詳細に出す行の並び (owner が obj の行を新しい順に)。開くまで問い合わせない。
- settings_timeline(user, *, limit=50): 会社の表と設定の上書きの行 (設定の画面の「設定の履歴」)。

値は「その時の字」で残す。型は money・number・date・bool・text の 5 つ (表示の時に型で描き分ける)。
- money = 名前が access.MONEY_FIELDS の物。number = ほかの数の列。どちらも丸めない数の字 (Decimal を normalize した字)
- date は「2026-11-06」(日時の列は「2026-11-06 14:20」)、bool は「true」「false」
- text は画面の字 (choices は表示名・FK は相手の字・ファイルは名前・get_<名前>_display があればその字・
  それの無い JSONField は鍵を並べた JSON の字)
- 会社ごとの項目は列の型 (core.fields.TYPES) で同じ 5 つに当てる (choice は text)

残さない列: 主キー・UUIDField (番号の uuid は画面に出さない決まり)・auto_now と auto_now_add の列 (保存のたびに動く)・
名前が password の列・名前が _sent_at と _response で終わる列 (送った日時と相手の返事。送り直しのたびに動く)・
model の history_skip に書いた列。model の history_hidden_values に書いた列は、直したことと見出しだけを残す
(前と後は HIDDEN_VALUE の字)。

core.views と core.services は module の頭で import しない (core.views と core.services がこの module を読むので輪になる)。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from functools import cached_property

from django.db import models
from django.utils import timezone

from seizou.core import access
from seizou.core import fields as company_fields

#: 直したことと見出しだけを残す列 (history_hidden_values) の前と後の字
HIDDEN_VALUE = "(残していません)"
#: 金額の閲覧の無い人に出す、金額の項目の前と後の字 (決め 権限の作り方 11-03 の金額の閲覧の行「変更の履歴の単価は (非表示)」)
MASKED_VALUE = "(非表示)"
#: 人の字の無い行 (人の無い処理で、呼び手が字を渡さなかった行) の人の字
SYSTEM_ACTOR = "システム"
#: 値の型
TYPES = ("money", "number", "date", "bool", "text")
#: 会社ごとの項目の型 (core.fields.TYPES) → 値の型
EXT_TYPES = {"text": "text", "number": "number", "date": "date", "choice": "text", "bool": "bool"}
#: 操作 → 点の絵 (core/templatetags/seizou_icons.py の PATHS の名前)
ACTION_ICONS = {
    "created": "plus",
    "updated": "pencil",
    "stopped": "archive",
    "resumed": "check",
    "cancelled": "x",
    "deleted": "x",
}
#: その時の値を畳んで出す操作 (作った・消した)
FOLDED_ACTIONS = ("created", "deleted")
#: 名前の末尾がこれの列は残さない (送った日時と相手の返事。送り直しのたびに動き、流れの枠と送り合った記録に出る)
SKIPPED_SUFFIXES = ("_sent_at", "_response")
#: 名前がこれの列は残さない
SKIPPED_NAMES = ("password",)
#: 設定の画面の「設定の履歴」に出す表
SETTINGS_MODELS = ("core.company", "core.settingoverride")


def keeps_history(model) -> bool:
    """model の変更の履歴を残すか。class 属性 keeps_history が真か、history_owner (出す詳細への道) があれば真。"""
    return bool(getattr(model, "keeps_history", False)) or bool(getattr(model, "history_owner", None))


def owner_of(obj):
    """履歴を出す詳細の行。model の history_owner (例 "order"・"workorder_step.workorder"・("invoice", "shipment")) を辿る。

    道の並びは、行のある最初の道。どの道も行が無いか、history_owner の無い表は自分。
    """
    paths = getattr(obj.__class__, "history_owner", None)
    if not paths:
        return obj
    if isinstance(paths, str):
        paths = (paths,)
    for path in paths:
        current = obj
        for part in path.split("."):
            current = getattr(current, part, None) if current is not None else None
        if current is not None:
            return current
    return obj


def skipped(model, field) -> bool:
    """列を残さないか (module の docstring の「残さない列」)。"""
    if field.primary_key or isinstance(field, models.UUIDField):
        return True
    if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
        return True
    if field.name in SKIPPED_NAMES or field.name.endswith(SKIPPED_SUFFIXES):
        return True
    return field.name in (getattr(model, "history_skip", ()) or ())


def value_type(field) -> str:
    """列の値の型 (TYPES のどれか)。"""
    if access.is_money_name(field.name):
        return "money"
    if isinstance(field, models.BooleanField):
        return "bool"
    if isinstance(field, (models.DateField, models.DateTimeField)):
        return "date"
    if field.choices or field.is_relation:
        return "text"
    if isinstance(field, (models.DecimalField, models.IntegerField, models.FloatField)):
        return "number"
    return "text"


def plain_text(value, kind: str = "text") -> str:
    """値のその時の字。None は空の字。数は丸めない字、日時は分まで、真偽は true / false、辞書と並びは鍵を並べた JSON の字。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        return "0" if text in ("-0", "0") else text
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _field_text(row, field) -> str:
    """行の列の画面の字 (choices は表示名・FK は相手の字・ファイルは名前・get_<名前>_display があればその字)。"""
    getter = getattr(row, f"get_{field.name}_display", None)
    if callable(getter):
        return plain_text(getter())
    if field.is_relation:
        related = getattr(row, field.name, None)
        return "" if related is None else str(related)
    value = getattr(row, field.attname)
    if isinstance(field, models.FileField):
        name = getattr(value, "name", "") or ""
        return name.rsplit("/", 1)[-1]
    return plain_text(value)


def ext_values(model, pk) -> dict:
    """会社ごとの項目の値を、登録の拡張の表 (registration_for(model).ext) から DB で読む ({列の名前: 値})。

    登録が無いか、拡張の表が読めないか、行が無ければ空の辞書 (行の無い部品は、全部の項目が空と同じ)。
    """
    reg = company_fields.registration_for(model)
    if reg is None:
        return {}
    from django.apps import apps

    try:
        ext_model = apps.get_model(reg.ext)
    except (LookupError, ValueError):
        return {}
    link = next(
        (f.name for f in ext_model._meta.get_fields() if getattr(f, "one_to_one", False) and f.related_model is model),
        None,
    )
    if link is None:
        return {}
    row = ext_model._base_manager.filter(**{link: pk}).first()
    if row is None:
        return {}
    return {col.name: getattr(row, col.name, None) for col in reg.columns}


def saved_state(obj) -> dict:
    """DB から取り直した行の値 ({列の名前: {"label", "type", "value"}}。choices の列は "code" に元の値の字も持つ)。

    渡された物の値は読まない。訳: 画面の ModelForm は検証の中で物の値を先に書き換える (core.services.update_company が
    同じ理由で表から取り直している)。会社ごとの項目は ext__<列の名前> の鍵で、拡張の表から DB で読む (ext_values)。
    行が表に無ければ空の辞書。
    """
    model = obj.__class__
    if obj.pk is None:
        return {}
    row = model._base_manager.filter(pk=obj.pk).first()
    if row is None:
        return {}
    state = {}
    for field in model._meta.concrete_fields:
        if skipped(model, field):
            continue
        entry = {"label": str(field.verbose_name), "type": value_type(field), "value": _field_text(row, field)}
        if field.choices:
            entry["code"] = plain_text(getattr(row, field.attname))
        state[field.name] = entry
    columns = company_fields.columns_for(model)
    if columns:
        values = ext_values(model, obj.pk)
        for col in columns:
            kind = EXT_TYPES.get(col.type, "text")
            state[company_fields.EXT_PREFIX + col.name] = {
                "label": col.label,
                "type": kind,
                "value": plain_text(values.get(col.name), kind),
            }
    return state


def _shown(kind: str, text: str, hide_money: bool) -> str:
    """変えた項目の前か後の、画面の字 (money は ¥ の字、number は区切り、bool は「はい」「いいえ」、空は「—」)。"""
    from seizou.core.templatetags.seizou_tags import comma, yen

    if text == HIDDEN_VALUE:
        return text
    if kind == "money" and hide_money:
        return MASKED_VALUE
    if text in ("", None):
        return "—"
    if kind in ("money", "number"):
        try:
            number = Decimal(text)
        except ArithmeticError:
            return text
        return yen(number) if kind == "money" else comma(number)
    if kind == "bool":
        return {"true": "はい", "false": "いいえ"}.get(text, text)
    return text


class Timeline:
    """詳細のタブと設定の画面に出す変更の履歴の行 (新しい順)。開くまで問い合わせない (rows・count・last は控える)。

    1 行 = {"when", "who", "title", "action", "icon", "folded", "items"}。items の 1 つ = {"label", "before", "after", "value"}
    (value は畳む行のその時の値 = 作ったは後、消したは前)。訳: 同じ部品を見る図面のビューア (masters の ItemDrawingViewerView も
    BaseDetailView) は履歴を描かないので、問い合わせを増やさないため。
    金額の項目 (型 money) は、金額の閲覧の無い人には前と後の両方を MASKED_VALUE にする。判定 (access.can_see_money) は
    money の項目を描く行がある時だけ 1 回呼ぶ (記録だけの状態では記録に 1 行。money の項目が無ければ記録しない)。
    訳: 決め (権限の作り方 11-03・金額の閲覧の行) が「変更の履歴の単価は (非表示)」と決めている。一覧と詳細の列と項目を
    出さない形 (決め 11-05 09:4x) と別に、変更の履歴だけは「(非表示)」と書かれているため。
    """

    def __init__(self, queryset, user, *, limit: int, owner=None):
        self._queryset = queryset
        self.user = user
        self.limit = limit
        self._owner = owner

    @cached_property
    def _entries(self) -> list:
        return list(self._queryset[: self.limit])

    @cached_property
    def count(self) -> int:
        """全部の行の数。上限より少なければ取った行の数 (数える問い合わせを出さない)。"""
        if len(self._entries) < self.limit:
            return len(self._entries)
        return self._queryset.count()

    @cached_property
    def rows(self) -> list[dict]:
        entries = self._entries
        hide_money = False
        if any(item.get("type") == "money" for entry in entries for item in (entry.changes or ())):
            hide_money = not access.can_see_money(self.user, what="変更の履歴の単価と金額を見た")
        return [self._row(entry, hide_money) for entry in entries]

    @cached_property
    def last(self) -> dict | None:
        """一番新しい行 (詳細の下の「最後に直した」)。行が無ければ None。"""
        return self.rows[0] if self.rows else None

    def _title(self, entry) -> str:
        label = entry.get_action_display()
        owner = self._owner
        own = owner is not None and entry.model == owner._meta.label_lower and entry.object_pk == owner.pk
        if own or owner is None and entry.model == entry.owner_model and entry.object_pk == entry.owner_pk:
            return label
        return f"{entry.object_text} を{label}"

    def _row(self, entry, hide_money: bool) -> dict:
        from seizou.core.templatetags.seizou_tags import without_yen_unit

        folded = entry.action in FOLDED_ACTIONS
        items = []
        for item in entry.changes or ():
            kind = item.get("type", "text")
            before = _shown(kind, item.get("before", ""), hide_money)
            after = _shown(kind, item.get("after", ""), hide_money)
            items.append(
                {
                    "label": without_yen_unit(item.get("label", item.get("field", ""))),
                    "before": before,
                    "after": after,
                    "value": before if entry.action == "deleted" else after,
                }
            )
        return {
            "when": entry.created_at,
            "who": entry.actor_label or SYSTEM_ACTOR,
            "title": self._title(entry),
            "action": entry.action,
            "icon": ACTION_ICONS.get(entry.action, "pencil"),
            "folded": folded,
            "items": items,
        }


def timeline(obj, user, *, limit: int = 100) -> Timeline:
    """詳細の「履歴」のタブの行 (owner が obj の行を新しい順に)。開くまで問い合わせない。"""
    from seizou.core.models import ChangeLog

    queryset = ChangeLog.objects.filter(owner_model=obj._meta.label_lower, owner_pk=obj.pk)
    return Timeline(queryset, user, limit=limit, owner=obj)


def settings_timeline(user, *, limit: int = 50) -> Timeline:
    """設定の画面の「設定の履歴」の行 (会社の表と設定の上書きの行を新しい順に)。開くまで問い合わせない。"""
    from seizou.core.models import ChangeLog

    return Timeline(ChangeLog.objects.filter(model__in=SETTINGS_MODELS), user, limit=limit)
