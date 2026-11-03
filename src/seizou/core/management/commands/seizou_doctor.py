"""置き場を点検して、良い / 直す を 1 行ずつ出す (立ち上げの確かめ)。

処理は seizou.core.doctor に置き、この命令は行を書くだけ。何も書き換えない。鍵の値は出さない (有るか無いかだけ)。
直す物が 1 つでもあれば、終わりの番号 1 で終わる。
"""

from django.core.management.base import BaseCommand, CommandError

from seizou.core import doctor


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
            raise CommandError(
                f"直す物が {fixes} 件あります。上の行の直し方のとおりに直して、もう 1 度 seizou_doctor を打ってください。",
                returncode=1,
            )
