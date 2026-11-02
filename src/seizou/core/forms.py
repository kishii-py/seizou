"""入力の form の土台。

ServiceModelForm: ModelForm だが、表に関わる判断 (一意など) は form で見ず services に任せる (R13 の線)。
form が見るのは欄の型と必須だけ。services の決まりの違反は日本語の文の ValueError か RuleError で返り、
画面の土台が赤の知らせにする。
"""

from django import forms


class ServiceModelForm(forms.ModelForm):
    def validate_unique(self):
        # 一意は services が見る (同じ文を 2 か所に持たない)。
        return
