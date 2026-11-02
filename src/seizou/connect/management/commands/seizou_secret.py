"""取引先の鍵の値を 1 つ作り、`<鍵の置き場の名前>=<値>` の 1 行を標準出力に 1 度だけ出す。

打つのは会社の担当者で、自分の端末の窓で打つ。値が会話に残るので、AI の道具には打たせない。
命令はファイルを書かず、環境変数と表を読み書きしない (つながりを登録する前でも打てる)。
値は signing.new_secret が作る。案内は標準エラーに出し、標準出力には 1 行だけを書く。
"""

from django.core.management.base import BaseCommand, CommandError

from seizou.connect.services import _ENV_NAME
from seizou.connect.signing import new_secret

MESSAGE_BAD_NAME = (
    "鍵の置き場の名前は、環境変数の名前 (英大文字で始まり、英大文字と数字と _ だけ) にしてください。"
    "鍵の値は渡さないでください。"
)

GUIDE = (
    "この 1 行を自社の .env に足してください。"
    "相手の会社には = の後ろの値だけを人の手で渡し、相手の側の鍵の置き場の名前の後ろに置いてもらいます。",
    "値を会話・チャット・commit に貼らないでください。",
)


class Command(BaseCommand):
    help = "取引先の鍵の値を 1 つ作り、<鍵の置き場の名前>=<値> の 1 行を画面に 1 度だけ出す。ファイルには書かない。"

    def add_arguments(self, parser):
        parser.add_argument(
            "secret_env",
            help="鍵の置き場の名前 (環境変数の名前。例 SEIZOU_PARTNER_B_PLATING_SECRET)。",
        )

    def handle(self, *args, **options):
        name = options["secret_env"]
        if _ENV_NAME.fullmatch(name) is None:
            # 値が渡されたときに文へ出さないよう、受けた文字列は文に入れない。
            raise CommandError(MESSAGE_BAD_NAME)
        self.stdout.write(f"{name}={new_secret()}")
        for line in GUIDE:
            self.stderr.write(line, style_func=self.style.WARNING)
