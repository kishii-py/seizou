"""feedback の入力の form。保存は form でなく services が行う (土台が ModelForm の save を呼ぶことは無い)。

form の service_kwargs() が、表の欄を data= (会社ごとの項目は土台と同じく ext=) に、画面の絵を screenshots= に分けて
services.create_feedback(data, user, screenshots=…) / update_feedback(feedback, data, user, screenshots=…) に渡す
(画面の絵は選んだときだけ渡す)。form が見るのは欄の型と必須だけ (値の決まりと画面の絵の形式・大きさ・枚数は services)。
「画面」の欄は画面の目録から選ぶ。左のメニューの「フィードバック」から開くと、土台の get_initial が GET の screen を初期値にする。
"""

from django import forms

from seizou.core import fields as company_fields
from seizou.core.forms import ServiceModelForm

from . import services
from .models import Feedback


class FileWidget(forms.FileInput):
    """複数のファイルを選べる欄 (Django の FileInput は allow_multiple_selected が真のときだけ multiple を受ける)。

    class の名前の末尾を Widget にして、土台の入力の画面 (core/form.html) が欄の種類 (widget_type) を file と読むようにする
    (保存が止まったときに、欄の下に「ファイルは選び直してください」が出る)。
    """

    allow_multiple_selected = True


class ScreenshotsField(forms.FileField):
    """画面の絵の欄。選んだファイルの並びを返す (選ばなければ空の並び)。形式・大きさ・枚数の決まりは services が見る。"""

    widget = FileWidget

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        cleaned = []
        for file in files:
            if file:
                cleaned.append(super().clean(file, initial))
        return cleaned


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
        self.fields["screens"].help_text = "画面の名前を「、」で区切って書く (例 受注、注文残)。分からなければ空でよい。"
        if services.screenshots_enabled():
            self.fields["screenshots"] = ScreenshotsField(
                label="画面の絵",
                required=False,
                widget=FileWidget(attrs={"accept": services.SCREENSHOT_ACCEPT}),
                help_text=(
                    f"困った画面の絵 (PNG・JPEG・WebP) を、1 枚 {services.SCREENSHOT_MAX_MB}MB まで、"
                    f"1 回に {services.SCREENSHOT_MAX_COUNT} 枚まで付けられます。ファイルをこの欄へドラッグしても入ります。"
                    "付けた絵は詳細の「画面の絵」に並びます。"
                ),
            )

    def service_kwargs(self) -> dict:
        """services の引数。表の欄は data (会社ごとの項目は土台と同じく ext)、画面の絵は選んだときだけ screenshots。"""
        cleaned = dict(self.cleaned_data)
        screenshots = list(cleaned.pop("screenshots", None) or [])
        data, ext = company_fields.split_ext(cleaned)
        kwargs = {"data": data}
        if ext is not None:
            kwargs["ext"] = ext
        if screenshots:
            kwargs["screenshots"] = screenshots
        return kwargs
