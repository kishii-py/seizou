"""フィードバックの GitHub の口 (依頼書 N185。決め 11-06 21:2x)。GitHub の API の薄い口と、会社の置き場の .env の読み書き。

約束:
- 鍵 (トークン) の置き場は会社の置き場の .env だけ。書く関数は同じフォルダの一時ファイルに 0o600 で書いてから置き換える。
  書いたら os.environ も同じ値にする (起動し直さずに今の process で使えるように)。
- トークンの値を、返す字・例外の文・記録 (logging) に入れない。
- requests は関数の中で import する (試験が差し替える。connect の _deliver と同じ形)。待ちは 10 秒。
  呼び手 (feedback の services) は transaction.atomic の外から呼ぶ (R17)。
- Django の settings を読むのは env_path の中だけ。端末の seizou github-token は Django を起こさず、.env の道を path で渡して呼ぶ。
- 表に書かない (表に書くのは feedback の services だけ = R13)。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

__all__ = [
    "BODY_CHARS",
    "DEFAULT_REPO",
    "GithubError",
    "LOGIN_ENV",
    "REPO_ENV",
    "TOKEN_ENV",
    "check_repo",
    "check_token",
    "create_issue",
    "env_path",
    "issue_state",
    "list_issues",
    "read_env_value",
    "remove_env_value",
    "whoami",
    "write_env_value",
]

#: トークンの置き場 (.env の鍵の名前)
TOKEN_ENV = "SEIZOU_GITHUB_TOKEN"
#: 送り先のリポジトリ (owner/name) の置き場
REPO_ENV = "SEIZOU_GITHUB_ISSUES_REPO"
#: つないだときの口座の名前の置き場 (画面に出す用。鍵ではない)
LOGIN_ENV = "SEIZOU_GITHUB_LOGIN"
#: 送り先の既定 (作品の公開のリポジトリ)
DEFAULT_REPO = "kishii-py/seizou"

#: API の頭
API = "https://api.github.com"
#: 待ちの秒
TIMEOUT = 10
#: トークンの頭 (classic と fine-grained)
TOKEN_PREFIXES = ("ghp_", "github_pat_")
#: 皆の Issue を探すときの本文の字数 (先頭から)
BODY_CHARS = 300

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

MESSAGE_ENV_WRITE = (
    "置き場の .env に書けませんでした。画面の process が置き場のフォルダに書けない置き場では、"
    "置き場の持ち主が端末で seizou github-token を打ってください。"
)
MESSAGE_TOKEN_SHAPE = (
    "トークンの形が違います。GitHub の「Generate token」の後に出た字を、前後の空白を入れずに、そのまま貼ってください。"
)
MESSAGE_REPO_SHAPE = "送り先のリポジトリは owner/name の形 (英数字と - _ . だけ) で書いてください。"
MESSAGE_UNAUTHORIZED = "トークンが違うか、期限が切れています。GitHub で作り直して、貼り直してください。"
MESSAGE_FORBIDDEN = "このトークンには権限がありません。送り先が公開のリポジトリなら public_repo、非公開なら repo に印を付けて作り直してください。"
MESSAGE_NOT_FOUND = "送り先のリポジトリが見つかりません。owner/name の綴りと、非公開のリポジトリなら repo の権限を確かめてください。"
MESSAGE_UNPROCESSABLE = "GitHub が内容を受け付けませんでした。題と本文を確かめて、もう一度押してください。"
MESSAGE_UNREACHABLE = "GitHub につながりませんでした。ネットにつながっているかを確かめて、少し待ってからもう一度押してください。"


class GithubError(Exception):
    """GitHub とのやり取りと .env の読み書きの失敗。文は日本語で、トークンの値を入れない。"""


# ---- .env ----


def env_path() -> Path:
    """会社の置き場の .env の道。settings の SEIZOU_ENV_FILE があればそれ、無ければ BASE_DIR / ".env"。"""
    from django.conf import settings

    path = getattr(settings, "SEIZOU_ENV_FILE", None)
    return Path(path) if path else Path(settings.BASE_DIR) / ".env"


def _key_of(line: str) -> str:
    """.env の 1 行の鍵の名前 (KEY=VALUE の行でなければ空の字。settings の _read_env と同じ読み方)。"""
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return ""
    return text.split("=", 1)[0].strip()


def read_env_value(key: str, path: Path | None = None) -> str:
    """.env の KEY=VALUE の行の値。ファイルに無ければ os.environ、どちらにも無ければ空の字。読めないファイルは無い物とみなす。"""
    target = Path(path) if path is not None else env_path()
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if _key_of(line) == key:
            return line.split("=", 1)[1].strip()
    return os.environ.get(key, "").strip()


def _replace_lines(target: Path, key: str, line: str | None) -> None:
    """key の行を line に置き換える (無ければ末尾に足す。line が None なら消す)。ほかの行と順はそのまま。

    同じフォルダの一時ファイルに 0o600 で書いて os.replace で置き換える。書けなければ GithubError。
    """
    try:
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        kept: list[str] = []
        done = False
        for old in lines:
            if _key_of(old) == key:
                if line is not None and not done:
                    kept.append(line)
                done = True
                continue
            kept.append(old)
        if line is not None and not done:
            kept.append(line)
        fd, temp = tempfile.mkstemp(prefix=".env.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("".join(f"{text}\n" for text in kept))
            os.chmod(temp, 0o600)
            os.replace(temp, target)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        # PermissionError も OSError の子。文に道と値を入れない。
        raise GithubError(MESSAGE_ENV_WRITE) from exc


def write_env_value(key: str, value: str, path: Path | None = None) -> None:
    """.env の key の行を value にする (無ければ末尾に足す)。os.environ も同じ値にする。書けなければ GithubError。"""
    target = Path(path) if path is not None else env_path()
    _replace_lines(target, key, f"{key}={value}")
    os.environ[key] = value


def remove_env_value(key: str, path: Path | None = None) -> None:
    """.env から key の行を消し、os.environ からも消す。ファイルが無ければ os.environ だけ。書けなければ GithubError。"""
    target = Path(path) if path is not None else env_path()
    if target.exists():
        _replace_lines(target, key, None)
    os.environ.pop(key, None)


# ---- 形の確かめ ----


def check_token(token) -> str:
    """トークンの形を見る (空でなく、空白を含まず、ghp_ か github_pat_ で始まる)。通れば字のまま返し、違えば GithubError。"""
    text = token if isinstance(token, str) else ""
    if not text or any(ch.isspace() for ch in text) or not text.startswith(TOKEN_PREFIXES):
        raise GithubError(MESSAGE_TOKEN_SHAPE)
    return text


def check_repo(repo) -> str:
    """送り先のリポジトリの形を見る (owner/name。英数字と - _ . だけ)。通れば前後の空白を外して返し、違えば GithubError。"""
    text = repo.strip() if isinstance(repo, str) else ""
    if not _REPO.fullmatch(text):
        raise GithubError(MESSAGE_REPO_SHAPE)
    return text


# ---- API ----


def _headers(token: str = "") -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _refused(status: int) -> GithubError:
    """失敗の HTTP の番号を日本語の文の GithubError にする。"""
    if status == 401:
        return GithubError(MESSAGE_UNAUTHORIZED)
    if status == 403:
        return GithubError(MESSAGE_FORBIDDEN)
    if status == 404:
        return GithubError(MESSAGE_NOT_FOUND)
    if status == 422:
        return GithubError(MESSAGE_UNPROCESSABLE)
    return GithubError(f"GitHub が受け付けませんでした (HTTP {status})。少し待ってからもう一度押してください。")


def _json(answer):
    try:
        return answer.json()
    except ValueError as exc:
        raise GithubError("GitHub の返事が読めませんでした。少し待ってからもう一度押してください。") from exc


def whoami(token: str) -> str:
    """トークンの口座の名前 (GET /user の login)。"""
    # 試験が requests.get を差し替えるため、関数の中で import する。
    import requests

    try:
        answer = requests.get(f"{API}/user", headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise GithubError(MESSAGE_UNREACHABLE) from exc
    if answer.status_code != 200:
        raise _refused(answer.status_code)
    data = _json(answer)
    login = data.get("login") if isinstance(data, dict) else None
    if not isinstance(login, str) or not login:
        raise GithubError("GitHub の返事に口座の名前がありませんでした。トークンを作り直して、貼り直してください。")
    return login


def create_issue(token: str, repo: str, title: str, body: str, labels) -> dict:
    """Issue を 1 件作る (POST /repos/{owner}/{repo}/issues)。返す物は {"number": 番号, "html_url": URL}。

    協力者でない口座が付けた labels は GitHub が黙って外す (付けてよい)。
    """
    import requests

    try:
        answer = requests.post(
            f"{API}/repos/{check_repo(repo)}/issues",
            json={"title": title, "body": body, "labels": list(labels)},
            headers=_headers(token),
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GithubError(MESSAGE_UNREACHABLE) from exc
    if answer.status_code != 201:
        raise _refused(answer.status_code)
    data = _json(answer)
    number = data.get("number") if isinstance(data, dict) else None
    url = data.get("html_url") if isinstance(data, dict) else None
    if not isinstance(number, int) or not isinstance(url, str) or not url:
        raise GithubError("GitHub の返事に Issue の番号と URL がありませんでした。GitHub で Issue ができたかを確かめてください。")
    return {"number": number, "html_url": url}


def issue_state(repo: str, number: int, token: str = "") -> dict:
    """Issue の状態 (GET /repos/{owner}/{repo}/issues/{number})。返す物は {"state", "closed_at", "html_url"}。トークンは任意。"""
    import requests

    try:
        answer = requests.get(f"{API}/repos/{check_repo(repo)}/issues/{int(number)}", headers=_headers(token), timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise GithubError(MESSAGE_UNREACHABLE) from exc
    if answer.status_code != 200:
        raise _refused(answer.status_code)
    data = _json(answer)
    if not isinstance(data, dict):
        raise GithubError("GitHub の返事が読めませんでした。少し待ってからもう一度押してください。")
    return {"state": data.get("state") or "", "closed_at": data.get("closed_at") or "", "html_url": data.get("html_url") or ""}


def list_issues(repo: str, token: str = "") -> list[dict]:
    """リポジトリの Issue の並び (GET /repos/{owner}/{repo}/issues?state=all&per_page=100&sort=updated を 1 回)。トークンは任意。

    pull request の行 (pull_request の鍵がある物) は外す。1 件 = {"number", "title", "body" (先頭 BODY_CHARS 字), "state",
    "html_url", "updated_at", "comments", "thumbs_up" (reactions の +1)}。
    """
    import requests

    try:
        answer = requests.get(
            f"{API}/repos/{check_repo(repo)}/issues",
            params={"state": "all", "per_page": 100, "sort": "updated"},
            headers=_headers(token),
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GithubError(MESSAGE_UNREACHABLE) from exc
    if answer.status_code != 200:
        raise _refused(answer.status_code)
    data = _json(answer)
    if not isinstance(data, list):
        raise GithubError("GitHub の返事が読めませんでした。少し待ってからもう一度押してください。")
    rows = []
    for item in data:
        if not isinstance(item, dict) or "pull_request" in item:
            continue
        reactions = item.get("reactions") if isinstance(item.get("reactions"), dict) else {}
        rows.append(
            {
                "number": item.get("number"),
                "title": item.get("title") or "",
                "body": (item.get("body") or "")[:BODY_CHARS],
                "state": item.get("state") or "",
                "html_url": item.get("html_url") or "",
                "updated_at": item.get("updated_at") or "",
                "comments": item.get("comments") or 0,
                "thumbs_up": reactions.get("+1") or 0,
            }
        )
    return rows
