"""connect の入力の form (受け入れると断る)。保存は form でなく services が行う。

土台 (BaseFormView) は form の service_kwargs() の辞書を検査の層に渡し、
services.accept(inbox, item_mapping=..., user=...) / services.reject(inbox, reason=..., user=...) を呼ぶ。
form が見るのは欄の型と必須だけ。決まり (2 回目の受け入れ・断った物) は services が見る。
"""

from django import forms
from django.apps import apps

from seizou.core import edi_codes
from seizou.core.forms import ServiceModelForm
from seizou.core.widgets import RemoteSelect
from seizou.masters.services import resolve_partner_item

from .models import Connection, Inbox


def _item_model():
    """自社の品目の表 (masters の Item)。ほかの部品の model を import せず、app の登録から引く。読むだけ。"""
    return apps.get_model("masters", "Item")


def _partner_parts(inbox):
    """注文のデータの明細にある相手の品番と品名の組を、出てきた順に重ねずに返す。形の違う行は飛ばす。

    変更と取消の版の行 (元の行 revises がある行) は、受注に無い行番号の明細 (取り消した明細 = 明細状態コード 2 を除く) の品番だけを返す
    (受注にある明細の部品は聞かない。聞くのは受け入れると新しく足す明細の部品だけ)。
    """
    payload = inbox.payload if inbox is not None and isinstance(inbox.payload, dict) else {}
    lines = payload.get("lines")
    if inbox is not None and inbox.revises_id is not None and isinstance(lines, list):
        order = inbox.revises.order
        known = set(order.lines.values_list("line_no", flat=True)) if order is not None else set()
        lines = [
            line
            for line in lines
            if isinstance(line, dict) and line.get("line_status_code") != edi_codes.LINE_DELETED and line.get("line_no") not in known
        ]
    parts = {}
    for line in lines if isinstance(lines, list) else []:
        part_no = line.get("part_no") if isinstance(line, dict) else None
        if not isinstance(part_no, str) or not part_no.strip() or part_no.strip() in parts:
            continue
        part_name = line.get("part_name")
        parts[part_no.strip()] = part_name if isinstance(part_name, str) else ""
    return list(parts.items())


# 取引先が空の行 (読み取りで差出人の確認待ち) の欄の説明。取引先ごとの説明の字が作れないので、前からの固定の字のまま。
_HELP_WITHOUT_PARTNER = "自社の部品を選びます。取引先ごとの品番の表に無い対応は、受け入れると表に足します。"


def _part_label(partner, part_no, part_name):
    """受け入れの欄の見出し (初見テスト R5 の候補 C08 の 5)。

    取引先があれば「<取引先の名前> の品番 <相手の品番> (<品名>) に当たる自社の部品」(取引先の名前は、画面の「届いた明細 (<取引先> の品番のまま)」と同じ字)。
    取引先が空の行は、前からの字「相手の品番 <相手の品番> (<品名>)」のまま (断る処理は足さない)。品名が無ければ括弧を付けない。
    """
    shown = f"{part_no} ({part_name})" if part_name else part_no
    if partner is None:
        return f"相手の品番 {shown}"
    return f"{partner.name} の品番 {shown} に当たる自社の部品"


def _part_help(partner, part_no, known):
    """受け入れの欄の説明 (初見テスト R5 の候補 C08 の 5)。services._item_for の今の動きを字にした物で、動きは変えない。

    known = 取引先ごとの品番の表で当たった自社の品目 (resolve_partner_item。無ければ None)。
    表に対応があれば最初から入れたことと、選び直してもこの受注だけで表は変わらないこと。無ければ、受け入れると表に足すこと。
    取引先が空の行は、前からの固定の字。
    """
    if partner is None:
        return _HELP_WITHOUT_PARTNER
    if known is not None:
        return (
            f"取引先ごとの品番の表で、{partner.name} の {part_no} は {known.part_no} に当たるので、最初から入れています。"
            "選び直すと、この受注だけ選んだ部品になります (表は変わりません)。"
        )
    return f"取引先ごとの品番の表に、{partner.name} の {part_no} の対応がまだありません。選んだ部品は、受け入れると表に足します。"


class AcceptForm(forms.Form):
    """受け入れの form。相手の品番ごとに、自社の品目を選ぶ欄を 1 つ置く。

    欄の見出しと説明は欄ごとに作る (_part_label・_part_help)。初めの値は取引先ごとの品番の表の対応 (無ければ空)。
    同じ品番が 2 行にあっても欄は 1 つ (item_mapping は品番 → 品目の 1 対 1 のため)。
    """

    submit_label = "受け入れる"

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.part_fields = []
        if instance is None:
            return
        # 変更と取消の版は、押した結果が分かる字にする (受注を新しく作らず、元の受注を変える)。
        if instance.revises_id is not None:
            self.submit_label = "取消を受け入れる" if instance.purpose_code == edi_codes.PURPOSE_CANCEL else "変更を受け入れる"
        items = _item_model()._default_manager.all()
        partner = instance.partner
        for number, (part_no, part_name) in enumerate(_partner_parts(instance), start=1):
            name = f"item_{number}"
            known = resolve_partner_item(partner, part_no)
            # 字を打って探す欄 (受注の入力と同じ候補の口)。標準の選択欄だと部品が増えたときに探せない (初見テスト 2 回目の所見)。
            self.fields[name] = forms.ModelChoiceField(
                queryset=items,
                widget=RemoteSelect("masters:item_select"),
                label=_part_label(partner, part_no, part_name),
                initial=known,
                help_text=_part_help(partner, part_no, known),
                # required の文は services._item_for と同じ字。invalid_choice は Django が % で値を差し込むので、品番の % を重ねる。
                error_messages={
                    "required": f"品番 {part_no} の自社の部品を選んでください。",
                    "invalid_choice": f"品番 {part_no.replace('%', '%%')} の自社の部品が候補にありません。選び直してください。",
                },
            )
            self.part_fields.append((name, part_no))

    def service_kwargs(self):
        mapping = {part_no: self.cleaned_data[name] for name, part_no in self.part_fields}
        return {"item_mapping": mapping}


class RejectForm(forms.Form):
    """断るの form。理由の欄 1 つ。

    書いた理由が注文回答として相手へ送られる行だけ、理由の欄の下にそのことを書く (初見テスト R5 の候補 C72 の 3)。
    説明はこの form の欄 (self.fields) にだけ入れ、class の欄 (base_fields) には書かない
    (題の横の「?」の「入れる物」の表は base_fields を読むので、表の字は変わらない)。
    """
    submit_label = "断る"

    reason = forms.CharField(
        label="断った理由",
        max_length=Inbox._meta.get_field("reason").max_length,
        widget=forms.Textarea(attrs={"rows": 3}),
        # services の MESSAGE_NO_REASON と reject の文と同じ字 (文に % を書かない = Django が値を差し込まない)。
        error_messages={
            "required": "断る理由がありません。理由を書いてから断ってください。",
            "max_length": f"断る理由が {Inbox._meta.get_field('reason').max_length} 字を超えています。{Inbox._meta.get_field('reason').max_length} 字までに縮めてください。",
        },
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        # instance が無い form (言葉の試験など) は DB を読まずに抜ける。
        if instance is None:
            return
        # 送るかの判定は services.send_reply と同じ読み方 (読み取りの行は送らない・向き「送る」のつながりが無ければ送らない)。
        if instance.source == Inbox.SOURCE_INTAKE or not instance.partner_id:
            return
        if not Connection.objects.filter(partner=instance.partner, direction=Connection.DIRECTION_SEND).exists():
            return
        self.fields["reason"].help_text = f"書いた理由は、注文回答として {instance.partner.name} へ送ります。"

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
            "direction": "送る = この会社から相手の seizou へ送る (発注・注文回答・出荷の知らせ・検収)。受ける = 相手から届く物を受ける。往復には 2 社とも両方の向きが要るので、2 行に分けて登録します。",
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


class SenderForm(forms.Form):
    """差出人を選ぶ form (受信した注文の詳細。入口が読み取りの行だけ)。得意先か両方の取引先から 1 つ。保存は services.choose_sender (form は欄の型と必須だけ)。"""

    partner = forms.ModelChoiceField(
        queryset=None,
        label="差出人の取引先",
        widget=forms.Select(attrs={"class": "select select-sm w-auto"}),
        error_messages={
            "required": "差出人の取引先を選んでください。",
            "invalid_choice": "選んだ取引先が候補にありません。選び直してください。",
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 取引先の表は Inbox の外部キーの先から引く (masters の model を import しない。_item_model と同じ考え。読むだけ)。
        partner = Inbox._meta.get_field("partner").related_model
        self.fields["partner"].queryset = partner._default_manager.filter(
            role__in=(partner.ROLE_CUSTOMER, partner.ROLE_BOTH)
        ).order_by("code")
