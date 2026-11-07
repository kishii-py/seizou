"""intake の入力の form (読み取ると受注にする)。保存は form でなく services が行う。

土台 (BaseFormView) は form の service_kwargs() の辞書を検査の層に渡し、
services.extract_order(file=..., customer=..., user=...) / services.accept_extracted(extraction, item_mapping=..., user=...)
を呼ぶ。form が見るのは欄の型と必須だけ。決まり (鍵・形式・大きさ・2 回目の受注にする) は services が見る。
"""

from django import forms

from seizou.masters.services import active_items, active_partners, resolve_partner_item

from . import services
from .models import Extraction


def _customer_queryset():
    """得意先の候補 (取引先の種類が得意先か両方で、使用中の物。N135)。取引先の表は Extraction の外部キーの先から引く。読むだけ。"""
    partner = Extraction._meta.get_field("customer").related_model
    return active_partners().filter(role__in=(partner.ROLE_CUSTOMER, partner.ROLE_BOTH))


def _customer_parts(extraction):
    """読み取った明細にある得意先の品番と品名の組を、出てきた順に重ねずに返す。形の違う行は飛ばす。"""
    data = extraction.extracted if extraction is not None and isinstance(extraction.extracted, dict) else {}
    lines = data.get("lines")
    parts = {}
    for line in lines if isinstance(lines, list) else []:
        part_no = line.get("part_no") if isinstance(line, dict) else None
        if not isinstance(part_no, str) or not part_no.strip() or part_no.strip() in parts:
            continue
        part_name = line.get("part_name")
        parts[part_no.strip()] = part_name if isinstance(part_name, str) else ""
    return list(parts.items())


def _lines_by_part(extraction):
    """読み取りを受注にする画面の枠の左に並べる行 (intake/_extraction_accept_card.html。候補 C91)。読むだけ。

    読み取った明細 (extracted の lines の dict) を、得意先の品番 (前後の空白を落とした字) ごとに、読み取った順のまま並べた辞書で返す。
    行の選び方は _customer_parts と同じ (dict でない行・品番が字でない行・品番が空の字の行を飛ばす)。extracted が dict でなければ空の辞書。
    """
    data = extraction.extracted if extraction is not None and isinstance(extraction.extracted, dict) else {}
    lines = data.get("lines")
    grouped = {}
    for line in lines if isinstance(lines, list) else []:
        part_no = line.get("part_no") if isinstance(line, dict) else None
        if not isinstance(part_no, str) or not part_no.strip():
            continue
        grouped.setdefault(part_no.strip(), []).append(line)
    return grouped


class UploadForm(forms.Form):
    """注文書を読み取るの form。得意先と注文書のファイル。

    ready は読み取りが使えるか (鍵があるか)。偽ならテンプレートが黄の知らせだけを出し、入力のカード (欄とボタン) は描かない。
    no_key_manager_message は、鍵が無いときにアカウント管理の権限がある人にだけ黄の知らせに出す鍵の置き場の 1 文。
    """

    submit_label = "読み取る"

    customer = forms.ModelChoiceField(queryset=None, label="得意先")
    file = forms.FileField(label="注文書のファイル", help_text="PDF・PNG・JPEG。20MB まで。")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ready = services.is_available()
        self.no_key_manager_message = services.MESSAGE_NO_KEY_MANAGER
        self.fields["customer"].queryset = _customer_queryset()

    def service_kwargs(self):
        return {"file": self.cleaned_data["file"], "customer": self.cleaned_data["customer"]}


class AcceptForm(forms.Form):
    """受注にするの form。得意先の品番ごとに、自社の品目を選ぶ欄を 1 つ置く。

    欄の見出しに得意先の品番と品名を出す。初めの値は部品の取引先の表の対応 (無ければ空)。
    同じ品番が 2 行にあっても欄は 1 つ (item_mapping は品番 → 品目の 1 対 1 のため)。
    受注にする画面は欄 1 つに枠 1 つで描き、枠の左にその品番の読み取った明細の行を並べる (part_rows。intake/_extraction_accept_card.html)。
    """

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.part_fields = []
        # instance の無い form (言葉の試験など) でも part_rows が落ちないよう、早く抜ける前に置く (候補 C91)。
        self._lines_by_part = {}
        if instance is None:
            return
        self._lines_by_part = _lines_by_part(instance)
        items = active_items()
        for number, (part_no, part_name) in enumerate(_customer_parts(instance), start=1):
            name = f"item_{number}"
            label = f"得意先の品番 {part_no} ({part_name})" if part_name else f"得意先の品番 {part_no}"
            self.fields[name] = forms.ModelChoiceField(
                queryset=items,
                label=label,
                initial=resolve_partner_item(instance.customer, part_no),
                help_text="自社の品目を選びます。部品の取引先の表に無い対応は、受注にすると表に足します。",
            )
            self.part_fields.append((name, part_no))

    @property
    def part_rows(self):
        """受注にする画面の枠の並び (intake/_extraction_accept_card.html。候補 C91)。1 つ = {field・lines}。

        part_fields の並びで、field は欄 (self[name])、lines はその品番の読み取った明細の行 (_lines_by_part。ふつうは 1 行)。
        最初に読んだ時に作って持つ (土台の get_form が __init__ の後に欄の widget と見出しを触るので、__init__ の中で作らない)。
        """
        if getattr(self, "_part_rows", None) is None:
            self._part_rows = [
                {"field": self[name], "lines": self._lines_by_part.get(part_no, [])} for name, part_no in self.part_fields
            ]
        return self._part_rows

    @property
    def other_fields(self):
        """枠に入らない見える欄 (土台の get_form が足す会社ごとの項目)。受注にする画面は枠の後ろに今の格子で出す (候補 C91)。"""
        names = {name for name, _part_no in self.part_fields}
        return [field for field in self.visible_fields() if field.name not in names]

    def service_kwargs(self):
        mapping = {part_no: self.cleaned_data[name] for name, part_no in self.part_fields}
        return {"item_mapping": mapping}
