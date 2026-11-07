"""送ったフィードバックの GitHub の Issue を読み、閉じた物を直した・見送った・版待ちに分ける (依頼書 N185・N322。cron 用)。

処理は feedback の services.sync_github に置き、この命令は呼んで結果を 1 行で書くだけ。
世話役の判定はしない (置き場の持ち主が打つ命令のため。画面の「GitHub の状態を取り込む」は世話役だけ)。
トークンがあれば使い、無くても公開のリポジトリの Issue は読める。読めなかった物は数えて飛ばす (止めない)。
送った・直した・見送ったの件の Issue の返事も読み、新しく入れた数を末尾の「返事 n 件」に出す (依頼書 N324)。
"""

from django.core.management.base import BaseCommand

from seizou.feedback import services


class Command(BaseCommand):
    help = "送ったフィードバックの GitHub の Issue を読み、閉じた物を直した・見送った・版待ちに分ける (cron 用。結果を 1 行で出す)。"

    def handle(self, *args, **options):
        counts = services.sync_github(None)
        self.stdout.write(
            f"GitHub を見ました: 見た {counts['見た']} 件・直した {counts['直した']} 件・見送った {counts['見送った']} 件・"
            f"版待ち {counts['版待ち']} 件・版の印なし {counts['版の印なし']} 件・読めなかった {counts['読めなかった']} 件"
            f"・返事 {counts['返事']} 件"
        )
