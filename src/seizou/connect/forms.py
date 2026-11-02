"""connect の入力の form (受け入れると断る)。保存は form でなく services が行う。

土台 (BaseFormView) は form の service_kwargs() の辞書を検査の層に渡し、
services.accept(inbox, item_mapping=..., user=...) / services.reject(inbox, reason=..., user=...) を呼ぶ。
form が見るのは欄の型と必須だけ。決まり (2 回目の受け入れ・断った物) は services が見る。
"""

from django import forms
from django.apps import apps

from seizou.masters.services import resolve_partner_item

from .models import Inbox


def _item_model():
    """自社の品目の表 (masters の Item)。ほかの部品の model を import せず、app の登録から引く。読むだけ。"""
    return apps.get_model("masters", "Item")


def _partner_parts(inbox):
    """注文のデータの明細にある相手の品番と品名の組を、出てきた順に重ねずに返す。形の違う行は飛ばす。"""
    payload = inbox.payload if inbox is not None and isinstance(inbox.payload, dict) else {}
    lines = payload.get("lines")
    parts = {}
    for line in lines if isinstance(lines, list) else []:
        part_no = line.get("part_no") if isinstance(line, dict) else None
        if not isinstance(part_no, str) or not part_no.strip() or part_no.strip() in parts:
            continue
        part_name = line.get("part_name")
        parts[part_no.strip()] = part_name if isinstance(part_name, str) else ""
    return list(parts.items())


class AcceptForm(forms.Form):
    """受け入れの form。相手の品番ごとに、自社の品目を選ぶ欄を 1 つ置く。

    欄の見出しに相手の品番と品名を出す。初めの値は取引先ごとの品番の表の対応 (無ければ空)。
    同じ品番が 2 行にあっても欄は 1 つ (item_mapping は品番 → 品目の 1 対 1 のため)。
    """

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.part_fields = []
        if instance is None:
            return
        items = _item_model()._default_manager.all()
        for number, (part_no, part_name) in enumerate(_partner_parts(instance), start=1):
            name = f"item_{number}"
            label = f"相手の品番 {part_no} ({part_name})" if part_name else f"相手の品番 {part_no}"
            self.fields[name] = forms.ModelChoiceField(
                queryset=items,
                label=label,
                initial=resolve_partner_item(instance.partner, part_no),
                help_text="自社の部品を選びます。取引先ごとの品番の表に無い対応は、受け入れると表に足します。",
            )
            self.part_fields.append((name, part_no))

    def service_kwargs(self):
        mapping = {part_no: self.cleaned_data[name] for name, part_no in self.part_fields}
        return {"item_mapping": mapping}


class RejectForm(forms.Form):
    """断るの form。理由の欄 1 つ。"""

    reason = forms.CharField(
        label="断った理由",
        max_length=Inbox._meta.get_field("reason").max_length,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def service_kwargs(self):
        return {"reason": self.cleaned_data["reason"]}
