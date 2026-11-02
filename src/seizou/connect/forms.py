"""connect の入力の form (受け入れると断る)。保存は form でなく services が行う。

土台 (BaseFormView) は form の service_kwargs() の辞書を検査の層に渡し、
services.accept(inbox, item_mapping=..., user=...) / services.reject(inbox, reason=..., user=...) を呼ぶ。
form が見るのは欄の型と必須だけ。決まり (2 回目の受け入れ・断った物) は services が見る。
"""

from django import forms
from django.apps import apps

from seizou.core.forms import ServiceModelForm
from seizou.core.widgets import RemoteSelect
from seizou.masters.services import resolve_partner_item

from .models import Connection, Inbox


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
            # 字を打って探す欄 (受注の入力と同じ候補の口)。標準の選択欄だと部品が増えたときに探せない (初見テスト 2 回目の所見)。
            self.fields[name] = forms.ModelChoiceField(
                queryset=items,
                widget=RemoteSelect("masters:item_select"),
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


class ConnectionForm(ServiceModelForm):
    """つながりの新規登録と編集の form。鍵の値の欄は持たない (値は .env にだけ置く)。

    受け口の URL は Meta.fields に入れず、form の欄として書く。表の URLField の検査は点の無い PC の名前を断るので、
    URL の確かめを services (_check_connection) の 1 か所にするため。
    service_kwargs() は新規登録 (register_connection) と編集 (update_connection) で同じ辞書を返す。
    """

    url = forms.CharField(
        label="受け口の URL",
        max_length=200,
        required=False,
        help_text="送る向きのときだけ入れます。例 http://127.0.0.1:8002/connect/receive/order/ 。PC の名前 (点の無い名前) も使えます。",
    )

    field_order = ["partner", "direction", "url", "secret_env"]

    class Meta:
        model = Connection
        fields = ["partner", "direction", "secret_env"]
        help_texts = {
            "direction": "送る = この会社が発注を送る相手。受ける = 相手から注文を受け取る。両方の向きでつなぐときは、2 行に分けて登録します。",
            "secret_env": "鍵の値ではなく、値を入れる環境変数の名前です (SEIZOU_PARTNER_<相手の会社の id を大文字にした物>_SECRET)。値は .env にだけ置きます。",
        }

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, instance=instance, **kwargs)
        if instance is not None:
            # url は Meta.fields の外なので、ModelForm は今の値を入れない。編集の画面で空にならないよう入れる。
            self.initial["url"] = self.instance.url

    def service_kwargs(self):
        data = self.cleaned_data
        return {
            "partner": data["partner"],
            "url": data["url"].strip(),
            "secret_env": data["secret_env"].strip(),
            "direction": data["direction"],
        }
