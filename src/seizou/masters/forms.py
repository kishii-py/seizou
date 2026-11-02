"""masters の入力の form。保存は form でなく services が行う (土台が ModelForm の save を呼ぶことは無い)。

form に service_kwargs() が無いので、土台は cleaned_data を data= (会社ごとの項目は ext=) に分けて
services.create_item(data, user, ext=ext) / update_item(item, data, user, ext=ext) を呼ぶ (01 の 10.4)。
締日と支払日の min・max (1〜31) は土台の ServiceModelForm が表の検査から付ける。ここは欄の下の案内だけ。
"""

from seizou.core.forms import ServiceModelForm

from .models import Item, Partner


class ItemForm(ServiceModelForm):
    class Meta:
        model = Item
        fields = ["part_no", "name", "drawing_no", "drawing", "unit", "standard_price", "note"]


class PartnerForm(ServiceModelForm):
    class Meta:
        model = Partner
        fields = [
            "code",
            "name",
            "short_name",
            "role",
            "closing_day",
            "payment_month",
            "payment_day",
            "rounding",
            "tax_mode",
            "note",
        ]
        help_texts = {
            "closing_day": "1〜31。月末締めは 31",
            "payment_day": "1〜31。月末は 31",
        }
