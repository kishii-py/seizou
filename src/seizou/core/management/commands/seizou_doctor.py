"""置き場を点検して、良い / 直す を 1 行ずつ出す (立ち上げの確かめ)。

処理は seizou.core.doctor に置き、この命令は行を書くだけ。何も書き換えない。鍵の値は出さない (有るか無いかだけ)。
直す物が 1 つでもあれば、終わりの番号 1 で終わる。
--mail を付けると、直す物があるときだけ、直すの行を会社の知らせのメール (宛先は設定の差出人のアドレス) で送る。送れなくても終わりの番号は変えない。
"""

from django.core.management.base import BaseCommand, CommandError

from seizou.core import conf, doctor, mail


def send_fixes(rows) -> None:
    """直すの行を、会社の知らせのメールで差出人のアドレスへ送る。送れなければ mail.MailNotSent (送り方が欠けているときも)。

    本文は命令が書く直すの行と同じ字 (直す  <見た物>: <様子> → 直し方: <すること>)。点検の行は鍵の値を持たないので、本文に鍵の値は入らない。
    訳: 点検に行を足すだけでは、誰かが手で打つまで「直す」に気づかないため、cron で毎朝回し、直すがあれば会社の知らせのメールで届ける。宛先は試しに送ると同じ差出人のアドレス (新しい設定の鍵を作らない)。出所 = エンタープライズ化の調べ 第一弾の確かめ直しの (a)。変えてよい条件 = 会社の設定に点検の知らせの宛先ができたとき (その鍵を読む)。
    """
    fixes = [row for row in rows if not row.ok]
    address = conf.mail_settings()["from"]
    company = conf.company_id()
    subject = f"seizou の点検: 直す {len(fixes)} 件" + (f" ({company})" if company else "")
    body = "".join(f"直す  {row.label}: {row.detail} → 直し方: {row.fix}\n" for row in fixes)
    mail.send([address] if address else [], subject, body)


class Command(BaseCommand):
    help = "置き場を点検して、良い / 直す を 1 行ずつ出す (直す物があれば終わりの番号 1)。鍵の値は出さない。"

    # 点検は自分が検査する側なので、Django の起動の検査で先に止まらない (05 の 4 節の禁じること)。
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--production",
            action="store_true",
            help="本番の条件で点検する (DEBUG=1 のままでも、本番にする前に直す物を全部出す)。",
        )
        parser.add_argument(
            "--mail",
            action="store_true",
            help="直す物があれば、直すの行を会社の知らせのメール (宛先は設定の差出人のアドレス) で送る。良いだけのときは送らない。",
        )

    def handle(self, *args, **options):
        production = True if options["production"] else None
        rows = doctor.run(production=production)
        for row in rows:
            if row.ok:
                self.stdout.write(f"良い  {row.label}: {row.detail}")
            else:
                self.stdout.write(f"直す  {row.label}: {row.detail} → 直し方: {row.fix}")
        fixes = sum(1 for row in rows if not row.ok)
        self.stdout.write(f"点検の結果: 良い {len(rows) - fixes} 件・直す {fixes} 件")
        if not (production or doctor.is_production()):
            self.stdout.write(
                "本番の条件の点検 (自動ログイン・ALLOWED_HOSTS・https・collectstatic) は、DEBUG=0 のときか --production で見る。"
            )
        self.stdout.write(
            "本番の守りの細かい点検 (HTTPS の設定など) は manage.py check --deploy。LAN の中だけの置き場では当たらない物もある。"
        )
        if fixes:
            if options.get("mail"):
                try:
                    send_fixes(rows)
                except mail.MailNotSent as exc:
                    self.stderr.write(f"点検の直すをメールで送れませんでした: {exc}")
                else:
                    self.stdout.write("点検の直すをメールで送りました (宛先は設定の差出人のアドレス)。")
            raise CommandError(
                f"直す物が {fixes} 件あります。上の行の直し方のとおりに直して、もう 1 度 seizou_doctor を打ってください。",
                returncode=1,
            )
