"""社内の PC でサーバーを起こす。画面と、画面の CSS と JS の両方を返す (nginx と collectstatic が要らない)。

処理は seizou.core.serve に置き、この命令は引数を読んで案内を書くだけ。表を変えない。
出すのは番地・ポート・設定の名前だけ (環境変数と鍵の値は出さない)。
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from seizou.core import serve


class Command(BaseCommand):
    help = (
        "社内の PC でサーバーを起こす (Windows・Mac・Linux)。画面と、画面の CSS と JS を返す。"
        "止めるときは、この窓で Control を押しながら C を押す。"
    )

    def add_arguments(self, parser):
        parser.add_argument("--host", default=serve.DEFAULT_HOST, help="待ち受ける番地 (既定は 0.0.0.0 = PC の全部の番地。この PC だけなら 127.0.0.1)。")
        parser.add_argument("--port", type=int, help="ポート (既定は会社のポート = 答えの YAML の port)。")
        parser.add_argument("--threads", type=int, default=serve.DEFAULT_THREADS, help=f"同時にさばく要求の数 (既定は {serve.DEFAULT_THREADS})。")

    def handle(self, *args, **options):
        try:
            port = options["port"] if options["port"] is not None else serve.default_port()
        except serve.ServeError as exc:
            raise CommandError(str(exc)) from exc
        host = options["host"]
        self.stdout.write(f"サーバーを起こします: 番地 {host}・ポート {port} (止めるときは Control と C)。")
        self.stdout.write(f"この PC からは http://127.0.0.1:{port}/ 、社内のほかの端末からは http://<この PC の社内の番地>:{port}/ で開きます。")
        if settings.DEBUG:
            self.stdout.write("知らせ: 開発の設定 (.env の DEBUG=1) のままです。本番で使うときは DEBUG を 0 にして起こし直します。")
        elif serve.lan_http_unread():
            self.stdout.write(
                "知らせ: .env に SEIZOU_LAN_HTTP=1 がありますが、この置き場の settings.py に印を読む行がありません "
                f"(この命令より前に作った置き場)。{serve.LAN_HTTP_UNREAD_FIX}"
            )
        elif getattr(settings, "SESSION_COOKIE_SECURE", False) or getattr(settings, "CSRF_COOKIE_SECURE", False):
            self.stdout.write(
                "知らせ: ログインの Cookie を HTTPS の道でだけ送る設定です。LAN の中だけで http で開く置き場は、"
                ".env に SEIZOU_LAN_HTTP=1 の行を足して起こし直します (足さないと、ほかの端末からログインできません。"
                "seizou の docs/operations.md の「社内の PC で動かす」)。"
            )
        # 待ち受けは止めるまで戻らないので、上の行を先に書き出す (記録のファイルへ流しても、起こした時に行が出るように)
        self.stdout.flush()
        serve.run(host, port, options["threads"])
