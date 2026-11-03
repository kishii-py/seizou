"""feedback の入力の form。保存は form でなく services が行う (土台が ModelForm の save を呼ぶことは無い)。

form に service_kwargs() が無いので、土台は cleaned_data を data= で services.create_feedback(data, user) /
update_feedback(feedback, data, user) に渡す。form が見るのは欄の型と必須だけ (値の決まりは services)。
「画面」の欄は画面の目録から選ぶ。左のメニューの「フィードバック」から開くと、土台の get_initial が GET の screen を初期値にする。
"""

from django import forms

from seizou.core.forms import ServiceModelForm

from . import services
from .models import Feedback


class FeedbackForm(ServiceModelForm):
    class Meta:
        model = Feedback
        fields = ["kind", "screen", "title", "problem", "situation", "wish", "screens"]
        widgets = {"screens": forms.TextInput}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["screen"] = forms.ChoiceField(
            label="画面",
            required=False,
            choices=[("", "指定なし"), *services.screen_choices()],
            help_text="困りごとが起きた画面。左のメニューの「フィードバック」から開くと、開いていた画面が入っています。",
        )
        self.fields["screens"].help_text = "URL の名前 (例 orders:order_list) を空白で区切って書く。分からなければ空でよい。"
