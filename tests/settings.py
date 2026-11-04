"""試験用の最小の settings。

pytest と CI だけで使う。会社ごとの settings (組み立ての命令が書く物) とは別。
既定の DB は SQLite。環境変数 SEIZOU_TEST_DB が postgres のときは PostgreSQL に切り替え、
接続は PostgreSQL の標準の環境変数 (PGHOST・PGUSER・PGPASSWORD・PGDATABASE) から読む。
"""

import os
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ログインの締め出しの数のファイルは、リポジトリの中でなく一時の置き場に (core.login_guard。既定は BASE_DIR / "cache" / "login")。
SEIZOU_LOGIN_CACHE_DIR = tempfile.mkdtemp(prefix="seizou-login-cache-")

# 試験用と分かる平文。本物の鍵ではない。
SECRET_KEY = "seizou-tests-only-plain-words-not-a-real-key"

# 試験の中では決まりの検査を走らせないため、DEBUG は偽にする。
DEBUG = False

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

from seizou.parts import PARTS  # noqa: E402  (存在する部品の一覧。部品を足すレーンは parts.py に足す)

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    *PARTS,
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "seizou.core.middleware.RuleNoticeMiddleware",
]

# 利用者は core の物。会社のプロジェクトも同じ (01 の 10.5)。
AUTH_USER_MODEL = "core.User"
ROOT_URLCONF = "seizou.core.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 知らせの置き場は session (知らせの cookie の名前は Django の中で固定のため)。
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGIN_URL = "core:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:login"

# 選べる設定 (seizou.core.conf)。試験は既定で動かし、テストが必要な値を settings fixture で上書きする。
SEIZOU = {}

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
                "seizou.core.context_processors.seizou",
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
