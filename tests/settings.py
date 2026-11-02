"""試験用の最小の settings。

pytest と CI だけで使う。会社ごとの settings (組み立ての命令が書く物) とは別。
既定の DB は SQLite。環境変数 SEIZOU_TEST_DB が postgres のときは PostgreSQL に切り替え、
接続は PostgreSQL の標準の環境変数 (PGHOST・PGUSER・PGPASSWORD・PGDATABASE) から読む。
"""

import os
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 試験用と分かる平文。本物の鍵ではない。
SECRET_KEY = "seizou-tests-only-plain-words-not-a-real-key"

# 試験の中では決まりの検査を走らせないため、DEBUG は偽にする。
DEBUG = False

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

if os.environ.get("SEIZOU_TEST_DB") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("PGDATABASE", ""),
            "USER": os.environ.get("PGUSER", ""),
            "PASSWORD": os.environ.get("PGPASSWORD", ""),
            "HOST": os.environ.get("PGHOST", ""),
        }
    }
else:
    # 名前を持たないメモリの SQLite。migrate はいつも空の DB から始まる。
    # pytest-django は試験用の DB を別に作る。
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

USE_TZ = True
TIME_ZONE = "Asia/Tokyo"
LANGUAGE_CODE = "ja"

STATIC_URL = "static/"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(tempfile.gettempdir()) / "seizou-tests" / "media"
