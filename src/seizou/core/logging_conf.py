"""会社の settings.py の LOGGING (記録の行き先) の辞書を作る。

settings.py が `LOGGING = default_logging()` の 1 行で読む。module の頭で Django も seizou の部品も import しない
(settings.py を読む途中で import するため。seizou.parts と同じ形)。

訳: 本番 (DEBUG=0) の 500 の traceback・DisallowedHost・CSRF の断りと、seizou の記録 (設定の変更・送った注文の結果・
権限の判定・ログインの締め出し) を journald に常に残すため。Django の既定は django の記録をメールの handler にしか渡さず、
ADMINS が空だとどこにも残らない。handler は stderr の 1 つだけ。Django を読まない module にしたのは、版を上げた時に
中身の直しが既にある置き場にも届くように (parts.py と同じ形)。出所 = 本人の決め (11-08 00:20「journald は常に」) と
エンタープライズ化の調べ 第一弾の指摘 chk-ops-sre-1・ops-2 と確かめ直しの (b)。
変えてよい条件 = 本人が決め直したとき、または置き場が journald でない記録の置き場を使うと決めたとき。
"""

#: 記録の 1 行の形 (時刻・level・logger の名前・本文)。時刻は +0900 の形の時差を付ける (journald の時刻と並べて読めるように)
LINE_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S%z"


def default_logging() -> dict:
    """logging.config.dictConfig に渡す辞書を、呼ぶたびに新しく作って返す。

    行き先は stderr の handler の 1 つだけ (gunicorn が stderr を受け、systemd が journald に残す。runserver では窓に出る)。
    django は WARNING から、django.server (runserver の要求の行) と seizou は INFO から出す。
    django.request は ERROR からにする (404 などの 4xx の WARNING は出さない。CSRF の断りは django.security.csrf、
    DisallowedHost は django.security.DisallowedHost の logger で、どちらも django を通って出る)。
    django.request の行は propagate のまま django の stderr の handler で 1 回出る (stderr の handler を 2 つ作らない)。

    訳: 本番 (DEBUG=0) の 500 の traceback・DisallowedHost・CSRF の断りと、seizou の記録 (設定の変更・送った注文の結果・
    権限の判定・ログインの締め出し) を journald に常に残すため。Django の既定は django の記録をメールの handler にしか渡さず、
    ADMINS が空だとどこにも残らない。handler は stderr の 1 つだけ。Django を読まない module にしたのは、版を上げた時に
    中身の直しが既にある置き場にも届くように (parts.py と同じ形)。出所 = 本人の決め (11-08 00:20「journald は常に」) と
    エンタープライズ化の調べ 第一弾の指摘 chk-ops-sre-1・ops-2 と確かめ直しの (b)。
    変えてよい条件 = 本人が決め直したとき、または置き場が journald でない記録の置き場を使うと決めたとき。
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "line": {"format": LINE_FORMAT, "datefmt": DATE_FORMAT},
        },
        "handlers": {
            "stderr": {"class": "logging.StreamHandler", "stream": "ext://sys.stderr", "formatter": "line"},
        },
        "loggers": {
            "django": {"handlers": ["stderr"], "level": "WARNING"},
            "django.request": {"level": "ERROR"},
            "django.server": {"handlers": ["stderr"], "level": "INFO", "propagate": False},
            "seizou": {"handlers": ["stderr"], "level": "INFO", "propagate": False},
        },
    }
