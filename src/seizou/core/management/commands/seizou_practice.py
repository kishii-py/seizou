"""練習モード。今のデータの写しを使うサーバーを別のポートで立てる (start)・止めて写しを消す (stop)・立っているかを見る (status)。

処理は seizou.core.practice に置き、この命令は引数を読んで結果を書くだけ。表を変えない。
出すのは URL・道・pid・ポート・日時だけ (環境変数と鍵の値は出さない)。
"""

from django.core.management.base import BaseCommand, CommandError

from seizou.core import practice


class Command(BaseCommand):
    help = (
        "練習モード: 今のデータの写しを使うサーバーを別のポートで立てる (start)・止めて写しを消す (stop)・"
        "立っているかを見る (status)。ここでの登録は本番に残らない。SQLite の置き場だけ。"
    )

    # 練習は、ほかの設定に誤りがあっても立てられる (控えの命令と同じ。runserver の子は自分で検査を回す)。
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("start", "stop", "status"), help="start = 立てる・stop = 止めて写しを消す・status = 見る。")
        parser.add_argument("--port", type=int, help="練習のサーバーのポート (start だけ。既定は会社のポート + 100)。")

    def handle(self, *args, **options):
        try:
            if options["action"] == "start":
                self._start(options["port"])
            elif options["action"] == "stop":
                self._stop()
            else:
                self._status()
        except practice.PracticeError as exc:
            raise CommandError(str(exc)) from exc

    def _start(self, port):
        result = practice.start(port)
        self.stdout.write(f"練習のサーバーを立てました: {result['url']} (写し: {result['folder']}・記録: {result['log']})")
        self.stdout.write("ここでの登録は本番に残りません。全部の画面の上に黄色の帯が出ます。閉じるときは manage.py seizou_practice stop。")
        if not result["ready"]:
            self.stderr.write(
                f"{practice.START_WAIT_SECONDS} 秒たってもポート {result['port']} が答えません。"
                f"記録 {result['log']} を見てください。止めるときは manage.py seizou_practice stop。",
                style_func=self.style.WARNING,
            )

    def _stop(self):
        rows = practice.stop()
        if not rows:
            self.stdout.write(f"練習のサーバーはありません (写しが無い): {practice.practice_root()}")
            return
        left = False
        for row in rows:
            if row.get("skipped"):
                self.stdout.write(f"practice.json が無いので消していません: {row['folder']}")
            elif row.get("left"):
                left = True
                self.stderr.write(
                    f"port {row['port']} の process (pid {row['pid']}) が止まらないので、写しを消していません: {row['folder']}"
                )
            elif row.get("stopped"):
                self.stdout.write(f"止めて写しを消しました: port {row['port']} (pid {row['pid']})・{row['folder']}")
            else:
                self.stdout.write(f"写しを消しました (サーバーは既に止まっていました): {row['folder']}")
        if left:
            raise CommandError("止まらない練習のサーバーがあります。少し待ってからもう 1 度 seizou_practice stop を打ってください。")

    def _status(self):
        rows = practice.status()
        if not rows:
            self.stdout.write(f"練習のサーバーはありません: {practice.practice_root()}")
            return
        for row in rows:
            if row["running"]:
                self.stdout.write(
                    f"立っている: {row.get('url', '')} (pid {row['pid']}・立てた日時 {row.get('started_at', '')}・写し {row['folder']})"
                )
            else:
                self.stdout.write(f"止まっている: port {row['port']} (写し {row['folder']}。消すなら manage.py seizou_practice stop)")
