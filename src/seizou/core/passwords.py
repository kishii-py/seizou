"""パスワードの決まり (4 つの確かめ) の表。Django の AUTH_PASSWORD_VALIDATORS と同じ形。

訳: 管理者の仮のパスワードを手引きが勧める changepassword で変えたときも、アカウント管理の画面と同じ 4 つの確かめ
(8 字以上・よくある語でない・数字だけでない・ID や名前に似ていない) を通すため。表を 1 か所に置き、
雛形の settings.py と accounts.services と点検が同じ表を読む (決まりを 2 か所に書かない)。
出所 = 作者の決め (accounts.services.password_validators の 4 つ) と、エンタープライズ化の調べ 第一弾が挙げた
OWASP ASVS 5.0 の 6.2 (作業場に写しが無く、条の中身は未確認)。
変えてよい条件 = 本人がパスワードの決まりを決め直したとき (ここを直せば雛形・accounts・点検にそろって効く)。

雛形の settings.py の頭で読むので、このファイルは Django を import しない (NAME は字で書く)。
並びは accounts の画面がこれまで出してきた断りの文の順 (長さ・よくある語・数字だけ・ID や名前に似ている) にそろえる。
"""

VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
]
