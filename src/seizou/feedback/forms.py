"""feedback の入力の form。保存は form でなく services が行う (土台が ModelForm の save を呼ぶことは無い)。

form の service_kwargs() が、表の欄を data= (会社ごとの項目は土台と同じく ext=) に、画面の絵を screenshots= に分けて
services.create_feedback(data, user, screenshots=…) / update_feedback(feedback, data, user, screenshots=…) に渡す
(画面の絵は選んだときだけ渡す)。form が見るのは欄の型と必須だけ (値の決まりと画面の絵の形式・大きさ・枚数は services)。
「画面」の欄は画面の目録から選ぶ。左のメニューの「フィードバック」から開くと、土台の get_initial が GET の screen を初期値にする。
「影響する画面」の欄の見出しは SCREENS_LABEL (表の verbose_name は内部の字を含み、変えると migration が要るので変えない。詳細も同じ字)。
GithubConnectForm は「GitHub とつなぐ」の画面の入力 (トークンと送り先。依頼書 N185)。保存は services.connect_github。
GithubDeviceForm は同じ画面の「GitHub でログインしてつなぐ」の入力 (送り先と非公開の印。依頼書 N320)。始めるのは services.start_github_device。
"""

from django import forms
from django.urls import reverse

from seizou.core import fields as company_fields
from seizou.core.forms import ServiceModelForm

from . import services
from .models import Feedback


#: 「影響する画面」の欄の見出し。表の verbose_name は内部の字を含むが、変えると migration が要るので変えない。
#: 入力の画面 (新規登録と編集) は FeedbackForm の Meta.labels が、詳細は feedback_tags の feedback_fields がこの字を出す。
SCREENS_LABEL = "影響する画面"


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
        labels = {"screens": SCREENS_LABEL}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["screen"] = forms.ChoiceField(
            label="画面",
            required=False,
            choices=[("", "指定なし"), *services.screen_choices()],
            help_text="困りごとが起きた画面。左のメニューの「フィードバック」から開くと、開いていた画面が入っています。",
        )
        self.fields["screens"].help_text = "画面の名前を「、」で区切って書く (例 受注、生産管理ボード)。分からなければ空でよい。"
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


class GithubConnectForm(forms.Form):
    """「GitHub とつなぐ」の入力 (トークンと送り先)。保存は services.connect_github (形の確かめも services)。

    トークンの欄は伏せ字で、止まって描き直しても値を出さない (render_value=False)。ブラウザに覚えさせない (autocomplete=off)。
    隠しの next は、土台の「開いた元の画面へ戻す口」(BaseFormView.next_url) に同じ画面の道を渡し、つないだ後も同じ画面へ戻す
    (部品の画面は R15 で success_url を上書きできないため)。next は services には渡さない。
    送り先の初期値は、今の送り先 (.env) か既定。class の initial でなく __init__ で読む (起動の後に .env が変わるため)。
    """

    submit_label = "つなぐ"

    token = forms.CharField(
        label="トークン",
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="貼ったら画面には二度と出ません。",
    )
    repo = forms.CharField(
        label="送り先のリポジトリ (owner/name)",
        required=False,
        max_length=200,
        help_text="既定は作品の公開のリポジトリです。試すときは、自分の非公開のリポジトリにします。",
    )
    next = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["repo"].initial = services.github_status()["repo"]
        self.fields["next"].initial = reverse("feedback:github_setup")

    def service_kwargs(self) -> dict:
        """services.connect_github の引数 (data にトークンと送り先だけ。next は渡さない)。"""
        return {"data": {"token": self.cleaned_data["token"], "repo": self.cleaned_data.get("repo") or ""}}


class GithubDeviceForm(forms.Form):
    """「GitHub でログインしてつなぐ」の入力 (送り先と非公開の印。依頼書 N320)。始めるのは services.start_github_device (形の確かめも services)。

    「GitHub とつなぐ」の画面にトークンを貼る形 (GithubConnectForm) と並んで描くので、欄の id の頭を id_device_ にする
    (同じ画面に id_repo が 2 つ出ないように。送る名前は repo と private のまま)。
    送り先の初期値は、今の送り先 (.env) か既定。class の initial でなく __init__ で読む (起動の後に .env が変わるため)。
    """

    repo = forms.CharField(
        label=GithubConnectForm.base_fields["repo"].label,
        required=False,
        max_length=200,
        help_text=GithubConnectForm.base_fields["repo"].help_text,
    )
    private = forms.BooleanField(
        required=False,
        label="送り先は非公開のリポジトリ",
        help_text="印を付けると repo の権限で、付けなければ public_repo の権限で承認を求めます。",
    )

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("auto_id", "id_device_%s")
        super().__init__(*args, **kwargs)
        self.fields["repo"].initial = services.github_status()["repo"]
