"""送ったフィードバックの GitHub の Issue を読み、閉じた物を直したにする (依頼書 N185。cron 用)。

処理は feedback の services.sync_github に置き、この命令は呼んで結果を 1 行で書くだけ。
世話役の判定はしない (置き場の持ち主が打つ命令のため。画面の「GitHub の状態を取り込む」は世話役だけ)。
トークンがあれば使い、無くても公開のリポジトリの Issue は読める。読めなかった物は数えて飛ばす (止めない)。
"""

from django.core.management.base import BaseCommand

from seizou.feedback import services


class Command(BaseCommand):
    help = "送ったフィードバックの GitHub の Issue を読み、閉じた物を直したにする (cron 用。結果を 1 行で出す)。"

    def handle(self, *args, **options):
        counts = services.sync_github(None)
        self.stdout.write(
            f"GitHub を見ました: 見た {counts['見た']} 件・直した {counts['直した']} 件・読めなかった {counts['読めなかった']} 件"
        )
