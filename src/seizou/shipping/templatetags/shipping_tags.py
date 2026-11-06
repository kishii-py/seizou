"""shipping のテンプレートの tag。読むだけで、表に書かない。

- deliverynote_billing: 納品書を取り消せない理由の文 (services.cancel_refusal と同じ文) と、請求済みの出荷とその請求書の行
  (取消の確かめのカード shipping/_deliverynote_cancel_card.html が読む。初見テスト R5 の候補 C03。依頼書 N196)。
  請求書ごとにまとめた並びも返す (納品書の詳細 shipping/deliverynote_detail.html のタブの上の行が読む)。
"""

from django import template
from django.urls import reverse

from seizou.core import access
from seizou.core.views import model_url

from .. import services

register = template.Library()


@register.simple_tag(takes_context=True)
def deliverynote_billing(context, note):
    """{% deliverynote_billing object as billing %}。billing.refusal = 取り消せない理由の文 (取り消せるなら空の字)。

    billing.rows = 発行の請求書に入った出荷の行 (出荷の pk の順)。1 行は shipment_no・shipment_url (出荷の詳細)・
    invoice_no・invoice_url (請求書の詳細)。invoice_url は、billing を部署で開けない人と、請求書の URL が引けない会社では None
    (押すと部署の 403 になる link を出さない。判定は core の can_write と同じ access.can_open の record=False)。
    billing.invoices = 請求書ごとにまとめた並び (rows の並びで最初に出た順)。1 つは invoice_no・invoice_url・count
    (この納品書の出荷のうち、その請求書に入った数)。
    請求書は出荷の逆の関係で引く (shipping は billing を import しない。services.invoiced_shipments の 1 回)。
    文は請求済みの出荷があるときだけ引く (services.cancel_refusal の 1 回)。
    """
    request = context.get("request")
    can_bill = access.can_open(getattr(request, "user", None), "billing", record=False)
    rows = []
    for shipment, invoice in services.invoiced_shipments(note):
        rows.append(
            {
                "shipment_no": shipment.shipment_no,
                "shipment_url": reverse("shipping:shipment_detail", args=[shipment.pk]),
                "invoice_no": invoice.invoice_no,
                "invoice_url": model_url(type(invoice), "detail", invoice.pk) if can_bill else None,
            }
        )
    invoices = {}
    for row in rows:
        group = invoices.setdefault(
            row["invoice_no"], {"invoice_no": row["invoice_no"], "invoice_url": row["invoice_url"], "count": 0}
        )
        group["count"] += 1
    return {"refusal": services.cancel_refusal(note) if rows else "", "rows": rows, "invoices": list(invoices.values())}
