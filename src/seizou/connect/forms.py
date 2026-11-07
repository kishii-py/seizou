"""connect の入力の form (受け入れる・単価を直して受け入れる・保留する・断る)。保存は form でなく services が行う。

土台 (BaseFormView) は form の service_kwargs() の辞書を検査の層に渡し、
services.accept(inbox, item_mapping=..., user=...) / services.reject(inbox, reason=..., user=...) を呼ぶ。
単価を直して受け入れる (ConditionalAcceptForm) は services.accept_with_conditions(inbox, item_mapping=..., answers=..., user=...) を呼び、
明細ごとの回答単価・回答数量・回答納期の欄を持つ (中小企業共通 EDI の条件付受諾の項目を元にした)。
保留する (HoldForm) は欄の無い form で、services.hold(inbox, user=...) を呼ぶ。
form が見るのは欄の型と必須だけ。決まり (2 回目の受け入れ・断った物) は services が見る。
"""

from datetime import date
from decimal import Decimal

from django import forms
from django.apps import apps

from seizou.core import edi_codes
from seizou.core.forms import ServiceModelForm
from seizou.core.templatetags.seizou_tags import comma, yen
from seizou.core.widgets import IsoDateInput, RemoteSelect
from seizou.masters.services import resolve_partner_item

from .models import Connection, Inbox


def _item_model():
    """自社の品目の表 (masters の Item)。ほかの部品の model を import せず、app の登録から引く。読むだけ。"""
    return apps.get_model("masters", "Item")


def _asked_lines(inbox):
    """受け入れの欄を作る明細の行 (dict) を、届いた順のまま返す。読むだけ。

    形の違う行 (dict でない行・品番が字でない行・品番が空の字の行) は飛ばす。
    変更と取消の版の行 (元の行 revises がある行) は、受注に無い行番号の明細 (取り消した明細 = 明細状態コード 2 を除く) だけを返す
    (受注にある明細の部品は聞かない。聞くのは受け入れると新しく足す明細の部品だけ)。
    欄 (_partner_parts) と受け入れの画面の枠の左の行 (_lines_by_part) の両方がこの行を読む (枠に、欄の当たらない行を並べないため。候補 C91)。
    変更の版は受注の行番号を 1 回引くので、受け入れの form は __init__ で 1 回だけ呼んで両方に渡す。
    """
    payload = inbox.payload if inbox is not None and isinstance(inbox.payload, dict) else {}
    lines = payload.get("lines")
    if not isinstance(lines, list):
        return []
    if inbox.revises_id is not None:
        order = inbox.revises.order
        known = set(order.lines.values_list("line_no", flat=True)) if order is not None else set()
        lines = [
            line
            for line in lines
            if isinstance(line, dict) and line.get("line_status_code") != edi_codes.LINE_DELETED and line.get("line_no") not in known
        ]
    return [
        line
        for line in lines
        if isinstance(line, dict) and isinstance(line.get("part_no"), str) and line.get("part_no").strip()
    ]


def _partner_parts(inbox, lines=None):
    """注文のデータの明細にある相手の品番と品名の組を、出てきた順に重ねずに返す。形の違う行は飛ばす。

    拾う行は _asked_lines の行 (変更と取消の版は、受注に無い行番号の明細の品番だけ)。
    lines = _asked_lines(inbox) を既に引いてあれば、それを渡す (変更の版の受注の行番号を 2 回引かないため)。
    """
    parts = {}
    for line in _asked_lines(inbox) if lines is None else lines:
        part_no = line["part_no"].strip()
        if part_no in parts:
            continue
        part_name = line.get("part_name")
        parts[part_no] = part_name if isinstance(part_name, str) else ""
    return list(parts.items())


def _lines_by_part(inbox, lines=None):
    """受け入れの画面の枠の左に並べる行 (connect/_inbox_accept_card.html)。読むだけ。

    _asked_lines の行 (欄を作った行と同じ行) を、相手の品番 (前後の空白を落とした字) ごとに、届いた順のまま並べた辞書で返す。
    payload が dict でなければ空の辞書。lines は _partner_parts と同じ (引いてあれば渡す)。
    """
    grouped = {}
    for line in _asked_lines(inbox) if lines is None else lines:
        grouped.setdefault(line["part_no"].strip(), []).append(line)
    return grouped


# 取引先が空の行 (読み取りで差出人の確認待ち) の欄の説明。取引先ごとの説明の字が作れないので、前からの固定の字のまま。
_HELP_WITHOUT_PARTNER = "自社の部品を選びます。部品の取引先の表に無い対応は、受け入れると表に足します。"


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

    known = 部品の取引先の表で当たった自社の品目 (resolve_partner_item。無ければ None)。
    表に対応があれば最初から入れたことと、選び直してもこの受注だけで表は変わらないこと。無ければ、受け入れると表に足すこと。
    取引先が空の行は、前からの固定の字。
    """
    if partner is None:
        return _HELP_WITHOUT_PARTNER
    if known is not None:
        return (
            f"部品の取引先の表で、{partner.name} の {part_no} は {known.part_no} に当たるので、最初から入れています。"
            "選び直すと、この受注だけ選んだ部品になります (表は変わりません)。"
        )
    return f"部品の取引先の表に、{partner.name} の {part_no} の対応がまだありません。選んだ部品は、受け入れると表に足します。"


class AcceptForm(forms.Form):
    """受け入れの form。相手の品番ごとに、自社の品目を選ぶ欄を 1 つ置く。

    欄の見出しと説明は欄ごとに作る (_part_label・_part_help)。初めの値は部品の取引先の表の対応 (無ければ空)。
    同じ品番が 2 行にあっても欄は 1 つ (item_mapping は品番 → 品目の 1 対 1 のため)。
    受け入れの画面は欄 1 つに枠 1 つで描き、枠の左にその品番の届いた明細の行を並べる (part_rows。connect/_inbox_accept_card.html)。
    """

    submit_label = "受け入れる"

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.part_fields = []
        # instance の無い form (言葉の試験など) でも part_rows が落ちないよう、早く抜ける前に置く (候補 C91)。
        self._lines_by_part = {}
        if instance is None:
            return
        # 欄と枠の左の行は同じ行から作る (変更の版の受注の行番号を引くのは 1 回だけ)。
        asked = _asked_lines(instance)
        self._lines_by_part = _lines_by_part(instance, asked)
        # 変更と取消の版は、押した結果が分かる字にする (受注を新しく作らず、元の受注を変える)。
        if instance.revises_id is not None:
            self.submit_label = "取消を受け入れる" if instance.purpose_code == edi_codes.PURPOSE_CANCEL else "変更を受け入れる"
        items = _item_model()._default_manager.all()
        partner = instance.partner
        for number, (part_no, part_name) in enumerate(_partner_parts(instance, asked), start=1):
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

    @property
    def part_rows(self):
        """受け入れの画面の枠の並び (connect/_inbox_accept_card.html。候補 C91)。1 つ = {field・lines}。

        part_fields の並びで、field は欄 (self[name])、lines はその品番の届いた明細の行 (_lines_by_part。ふつうは 1 行)。
        最初に読んだ時に作って持つ (土台の get_form が __init__ の後に欄の widget と見出しを触るので、__init__ の中で作らない)。
        """
        if getattr(self, "_part_rows", None) is None:
            self._part_rows = [
                {"field": self[name], "lines": self._lines_by_part.get(part_no, [])} for name, part_no in self.part_fields
            ]
        return self._part_rows

    @property
    def other_fields(self):
        """枠に入らない見える欄 (土台の get_form が足す会社ごとの項目)。受け入れの画面は枠の後ろに今の格子で出す (候補 C91)。"""
        names = {name for name, _part_no in self.part_fields}
        return [field for field in self.visible_fields() if field.name not in names]

    def service_kwargs(self):
        mapping = {part_no: self.cleaned_data[name] for name, part_no in self.part_fields}
        return {"item_mapping": mapping}


def _payload_lines(inbox):
    """受信した注文の明細の並び (形の違う行は空の dict のまま残す。番号を enumerate の番号にそろえるため)。"""
    payload = inbox.payload if inbox is not None and isinstance(inbox.payload, dict) else {}
    lines = payload.get("lines")
    return [line if isinstance(line, dict) else {} for line in lines] if isinstance(lines, list) else []


def _figure(value):
    """届いた数の字を Decimal に読む (connect_tags.payload_number と同じ読み方)。読めなければ None。"""
    from .templatetags.connect_tags import payload_number

    number = payload_number(value)
    return number if isinstance(number, Decimal) else None


def _due(value):
    """届いた納期の字を date に読む。読めなければ None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class ConditionalAcceptForm(AcceptForm):
    """単価を直して受け入れるの form (中小企業共通 EDI の条件付受諾の項目を元にした)。

    受け入れの form の欄 (相手の品番ごとの自社の部品) の後に、明細ごとに回答単価・回答数量・回答納期の 3 つの欄を置く。
    初めの値は相手の注文の値。金額の閲覧の無い人 (hide_money) には回答単価の欄を置かない (services は相手の単価のまま作る)。
    形の違う明細は欄を飛ばすが、番号は明細の並びの番号のまま (受け入れの時に services が断る)。
    """

    submit_label = "単価を直して受け入れる"

    def __init__(self, *args, instance=None, hide_money=False, **kwargs):
        super().__init__(*args, instance=instance, **kwargs)
        self.answer_fields = []
        self.row_start_fields = []
        for number, raw in enumerate(_payload_lines(instance), start=1):
            part_no = raw.get("part_no")
            if not isinstance(part_no, str) or not part_no.strip():
                continue
            head = f"{number} 行目 {part_no.strip()}"
            price, qty, due = _figure(raw.get("unit_price")), _figure(raw.get("qty")), _due(raw.get("due_date"))
            names = {}
            if not hide_money:
                names["unit_price"] = f"line_{number}_unit_price"
                self.fields[names["unit_price"]] = forms.DecimalField(
                    label=f"{head} の回答単価",
                    max_digits=14,
                    decimal_places=2,
                    initial=price,
                    help_text=f"注文は {yen(price)}" if price is not None else "注文に単価はありません",
                    error_messages={"required": f"{number} 行目の回答単価を入れてください。"},
                )
            names["qty"] = f"line_{number}_qty"
            self.fields[names["qty"]] = forms.DecimalField(
                label=f"{head} の回答数量",
                max_digits=12,
                decimal_places=2,
                initial=qty,
                help_text=f"注文は {comma(qty)}{' ' + raw['unit'] if isinstance(raw.get('unit'), str) and raw.get('unit') else ''}"
                if qty is not None
                else "注文に数量はありません",
                error_messages={"required": f"{number} 行目の回答数量を入れてください。"},
            )
            names["due_date"] = f"line_{number}_due_date"
            self.fields[names["due_date"]] = forms.DateField(
                label=f"{head} の回答納期",
                required=False,
                initial=due,
                # 日付の欄は受注の明細の納期と同じ部品 (orders/forms.py の _date_input)
                widget=IsoDateInput(),
                help_text=f"注文は {due:%Y-%m-%d}。空なら注文の納期のまま" if due else "注文に納期はありません",
                error_messages={"invalid": f"{number} 行目の回答納期を日付 (YYYY-MM-DD) で入れてください。"},
            )
            self.row_start_fields.append(next(iter(names.values())))
            self.answer_fields.append((number, names))

    def service_kwargs(self):
        answers = {
            number: {
                "unit_price": self.cleaned_data[names["unit_price"]] if "unit_price" in names else None,
                "qty": self.cleaned_data[names["qty"]],
                "due_date": self.cleaned_data[names["due_date"]],
            }
            for number, names in self.answer_fields
        }
        return {**super().service_kwargs(), "answers": answers}


class HoldForm(forms.Form):
    """保留するの form。欄は無い (見える欄の無い form のカードは格子を描かない)。"""

    submit_label = "保留する"

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)

    def service_kwargs(self):
        return {}


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

    field_order = ["partner", "direction", "url", "secret_env", "contact_email"]

    class Meta:
        model = Connection
        fields = ["partner", "direction", "secret_env", "contact_email"]
        help_texts = {
            "direction": "送る = この会社から相手の seizou へ送る (発注・注文回答・出荷の知らせ・検収)。受ける = 相手から届く物を受ける。往復には 2 社とも両方の向きが要るので、2 行に分けて登録します。",
            "secret_env": "鍵の値ではなく、値を入れる環境変数の名前です (SEIZOU_PARTNER_<相手の会社の id を大文字にした物>_SECRET)。値は .env にだけ置きます。",
            "contact_email": "この取引先から会話が届いたら、このアドレスへメールで知らせます (会話の 1 行目と画面へのリンクだけ)。送る向きと受ける向きのどちらの行に入れても届きます。両方の行に違うアドレスを入れたときは、受ける向きの行のアドレスへ知らせます。取引先へは送りません。",
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
            "contact_email": (data.get("contact_email") or "").strip(),
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


class ChatMessageForm(forms.Form):
    """会話に書く form (受注・発注・受信した注文の「会話」の欄と、会話に書く画面)。保存と送るのは services.post_chat_message。

    書く先は hidden の 2 つ (target_kind = 発注か受信した注文・target_id)。受注の詳細から書くときは、受注の元の受信した注文を渡す
    (connect_tags の chat_panel が作る)。言葉の前後の空白は CharField の既定 (strip) が落とし、落とした後の字が送る字と残す字になる。
    form が見るのは欄の型と字数 (2000 字) と、書く先が引けるかだけ。言葉と添付の両方が空か・添付の形式と大きさ・会話が使えるか (CHAT-2) は services が見る。
    """

    TARGET_CHOICES = [("purchase_order", "発注"), ("inbox", "受信した注文")]

    target_kind = forms.ChoiceField(choices=TARGET_CHOICES, widget=forms.HiddenInput)
    target_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    body = forms.CharField(label="言葉", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
    # 添付は 1 回に 1 つ (本番の nginx が 1 回の要求を 25MB で切るので、20MB を 2 つ送ると届かない)。形式と大きさは services が今の添付の決まりで見る。
    file = forms.FileField(
        label="添付",
        required=False,
        help_text="PNG・JPEG・WebP・PDF・TIFF・CSV・テキスト・.xlsx・.docx のファイルを、20MB まで付けられます。",
    )
    submit_label = "送る"

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("target_kind")
        pk = cleaned.get("target_id")
        if kind and pk:
            # 発注の表は purchasing の物。connect は purchasing の model を頭で import しない (app の登録から引く。読むだけ)。
            model = apps.get_model("purchasing", "PurchaseOrder") if kind == "purchase_order" else Inbox
            target = model._default_manager.filter(pk=pk).first()
            if target is None:
                raise forms.ValidationError("書く先の注文が見つかりません。受注か発注の詳細の画面を開き直してください。")
            cleaned["target"] = target
        return cleaned

    def service_kwargs(self):
        return {"target": self.cleaned_data["target"], "body": self.cleaned_data["body"], "file": self.cleaned_data.get("file")}


class SelfInvoiceAnswerForm(forms.Form):
    """受けた仕入明細に答える form (候補 C111。条件付受諾の form と同じく「明細ごとに欄を足す」形)。

    答える明細 (答えが無い・提案・確認できない明細。承諾した明細は出さない = 決め C111-4) ごとに、答え (承諾 / 提案 / 確認できない。既定は承諾)・
    提案の数・提案の単価 (初めの値は仕入明細の値。金額の閲覧の無い人には置かない)・確認できない訳・書いておく字の欄を置く。
    見出しは「明細 n (受注 SO-…-m 品番)」。提案なら数と単価が 0 より大きく、どちらかが仕入明細の値と違うこと、確認できないなら訳を選ぶこと
    を確かめる (欄の下の赤い字)。テンプレートの小さな JS が、提案でない明細の数と単価、確認できないでない明細の訳を隠す。
    """

    submit_label = "答えを送る"
    ANSWER_CHOICES = (("accept", "承諾"), ("propose", "提案"), ("reject", "確認できない"))

    def __init__(self, *args, instance=None, hide_money=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        self.hide_money = hide_money
        self.answer_lines = []
        self.row_start_fields = []
        accepted = (edi_codes.SI_REPLY_ACCEPTED, edi_codes.SI_REPLY_ACKNOWLEDGED)
        lines = instance.lines.select_related("order_line__order", "order_line__item") if instance is not None else []
        for line in lines:
            if line.reply_type in accepted:
                continue
            number = line.line_no
            head = f"明細 {number} (受注 {line.order_line.order.order_no}-{line.order_line.line_no} {line.order_line.item.part_no})"
            names = {"answer": f"line_{number}_answer", "qty": f"line_{number}_qty", "reason": f"line_{number}_reason", "note": f"line_{number}_note"}
            initial = {"": "accept", edi_codes.SI_REPLY_PROPOSED: "propose", edi_codes.SI_REPLY_REJECTED: "reject"}.get(line.reply_type, "accept")
            self.fields[names["answer"]] = forms.ChoiceField(
                label=f"{head} の答え", choices=self.ANSWER_CHOICES, initial=initial,
                help_text=f"受領日 {line.received_on:%Y-%m-%d}・受領数量 {comma(line.qty)}" + ("" if hide_money else f"・単価 {yen(line.unit_price)}・金額 {yen(line.amount)}"),
            )
            self.fields[names["qty"]] = forms.DecimalField(
                label=f"明細 {number} の提案の数", required=False, max_digits=12, decimal_places=2,
                initial=line.reply_qty if line.reply_qty is not None else line.qty,
            )
            if not hide_money:
                names["unit_price"] = f"line_{number}_unit_price"
                self.fields[names["unit_price"]] = forms.DecimalField(
                    label=f"明細 {number} の提案の単価", required=False, max_digits=14, decimal_places=2,
                    initial=line.reply_unit_price if line.reply_unit_price is not None else line.unit_price,
                )
            self.fields[names["reason"]] = forms.ChoiceField(
                label=f"明細 {number} の確認できない訳", required=False,
                choices=(("", "—"), *edi_codes.SI_REASON_CHOICES), initial=line.reply_reason_code,
            )
            self.fields[names["note"]] = forms.CharField(
                label=f"明細 {number} の書いておく字", required=False, max_length=500, initial=line.reply_note,
            )
            self.row_start_fields.append(names["answer"])
            self.answer_lines.append((line, names))

    def clean(self):
        cleaned = super().clean()
        for line, names in self.answer_lines:
            answer = cleaned.get(names["answer"])
            number = line.line_no
            if answer == "propose":
                qty = cleaned.get(names["qty"])
                price = cleaned.get(names["unit_price"]) if "unit_price" in names else line.unit_price
                if qty is None or qty <= 0:
                    self.add_error(names["qty"], f"明細 {number} の提案の数は 0 より大きい数にしてください。")
                    continue
                if price is None or price <= 0:
                    self.add_error(names["unit_price"], f"明細 {number} の提案の単価は 0 より大きい数にしてください。")
                    continue
                if qty == line.qty and price == line.unit_price:
                    self.add_error(names["answer"], f"明細 {number} の提案が仕入明細と同じです。承諾にしてください。")
            elif answer == "reject" and not cleaned.get(names["reason"]):
                self.add_error(names["reason"], f"明細 {number} の確認できない訳を選んでください。")
        return cleaned

    def service_kwargs(self):
        answers = {}
        for line, names in self.answer_lines:
            data = self.cleaned_data
            answers[line.line_no] = {
                "answer": data[names["answer"]],
                "qty": data.get(names["qty"]),
                "unit_price": data.get(names["unit_price"]) if "unit_price" in names else None,
                "reason": data.get(names["reason"]) or "",
                "note": data.get(names["note"]) or "",
            }
        return {"answers": answers}
