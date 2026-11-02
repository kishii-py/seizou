"""選ぶ欄の widget (05 の 2 節。枠は core)。

RemoteSelect: 候補をサーバーの候補の口 (BaseSelectView の子) から 1 ページずつ取る <select>。
描くときは選んである値の <option> だけを描き、全件を描かない (候補は static の js/seizou-select.js が
data-select-url の口から取る)。欄の見た目の class は入力の土台 (BaseFormView.get_form) が付ける。
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse


class RemoteSelect(forms.Select):
    """候補の口の URL の名前 (例 "masters:item_select") を持つ 1 つ選びの欄。"""

    def __init__(self, url_name: str, attrs=None):
        super().__init__(attrs=attrs)
        self.url_name = url_name

    def build_attrs(self, base_attrs, extra_attrs=None):
        attrs = super().build_attrs(base_attrs, extra_attrs)
        # URL は描くときに引く (import の時点では URL の設定が読めていないことがあるため)。
        attrs["data-select-url"] = reverse(self.url_name)
        return attrs

    def selected_choices(self, value) -> list[tuple]:
        """描く選択肢: 空の選択肢 (あれば) と、選んである値の物だけ。"""
        wanted = [v for v in value if v not in (None, "")]
        queryset = getattr(self.choices, "queryset", None)
        if queryset is None:
            # model でない選択肢 (固定の並び) は、空と選んである物だけを残す。
            return [(v, label) for v, label in self.choices if v == "" or str(v) in wanted]
        field = self.choices.field
        result = [("", field.empty_label)] if field.empty_label is not None else []
        if not wanted:
            return result
        key = field.to_field_name or "pk"
        try:
            rows = list(queryset.filter(**{f"{key}__in": wanted}))
        except (ValueError, TypeError, ValidationError):
            # 数でない値などが送られてきたときは、選んである物を描かない (欄の誤りは form が出す)。
            return result
        return result + [self.choices.choice(obj) for obj in rows]

    def optgroups(self, name, value, attrs=None):
        groups = []
        for index, (option_value, option_label) in enumerate(self.selected_choices(value)):
            selected = str(option_value) in value
            option = self.create_option(name, option_value, option_label, selected, index, attrs=attrs)
            groups.append((None, [option], index))
        return groups
