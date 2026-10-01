#!/usr/bin/env python3
"""Get (or renew) a TikTok user access token for autoshorts and save it in .env.

autoshorts uploads with the token of YOUR OWN TikTok account, issued to your own
developer app (https://developers.tiktok.com: Login Kit + Content Posting API).
This helper runs TikTok's "Login Kit for Web" authorisation once:

1. It prints an authorisation link. Open it, log in with the TikTok account you post
   from and allow access.
2. TikTok sends your browser to your app's redirect URI with ``?code=...``. That page
   may show an error or "not found"; that is fine. Copy the whole address from the
   browser's address bar and paste it here.
3. The code is exchanged for an access token (valid 24 hours) and a refresh token
   (valid 365 days). Both are written to .env as TIKTOK_ACCESS_TOKEN and
   TIKTOK_REFRESH_TOKEN, next to TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET, so
   autoshorts can renew the access token by itself.

Usage (run it in the folder that holds .env and config.yaml)::

    python deploy/tiktok_token.py                   # inbox mode (scope video.upload)
    python deploy/tiktok_token.py --direct          # also video.publish (direct mode)
    python deploy/tiktok_token.py --refresh         # renew the tokens already in .env

Only the Python standard library is used. Tokens are never printed unless you pass
``--print`` (then nothing is written).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INBOX_SCOPES = ("user.info.basic", "video.upload")
DIRECT_SCOPES = INBOX_SCOPES + ("video.publish",)

ACCESS_ENV = "TIKTOK_ACCESS_TOKEN"
REFRESH_ENV = "TIKTOK_REFRESH_TOKEN"
KEY_ENV = "TIKTOK_CLIENT_KEY"
SECRET_ENV = "TIKTOK_CLIENT_SECRET"
REDIRECT_ENV = "TIKTOK_REDIRECT_URI"


class TokenError(Exception):
    """Something the user has to fix; printed without a traceback."""


# --------------------------------------------------------------------------- .env file


def read_env(path: Path) -> dict[str, str]:
    """KEY=value pairs of a .env file (comments, blanks and 'export ' prefixes handled)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def update_env(path: Path, updates: dict[str, str]) -> None:
    """Set KEY=value lines in .env, keeping every other line; owner-only permissions."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    pending = dict(updates)
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        key = stripped.partition("=")[0].strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not stripped.startswith("#") and "=" in stripped and key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(raw)
    out.extend(f"{key}={value}" for key, value in pending.items())
    text = "\n".join(out) + "\n"

    folder = path.resolve().parent
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - e.g. some network filesystems
            pass
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- OAuth


def authorize_url(client_key: str, redirect_uri: str, scopes: tuple[str, ...], state: str) -> str:
    query = urllib.parse.urlencode({
        "client_key": client_key,
        "scope": ",".join(scopes),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{query}"


def parse_redirect(pasted: str, state: str) -> str:
    """The authorisation code from the pasted redirect URL (or a bare code)."""
    text = pasted.strip()
    if not text:
        raise TokenError("nothing was pasted")
    if "code=" not in text and "error=" not in text:
        return urllib.parse.unquote(text)  # a bare code
    query = urllib.parse.urlparse(text).query if "?" in text else text
    params = urllib.parse.parse_qs(query)
    if "error" in params:
        detail = params.get("error_description", [""])[0]
        raise TokenError(f"TikTok refused the authorisation: {params['error'][0]} {detail}".strip())
    returned_state = params.get("state", [""])[0]
    if returned_state and returned_state != state:
        raise TokenError("the 'state' in the pasted URL does not match this session; start again")
    code = params.get("code", [""])[0]
    if not code:
        raise TokenError("no ?code= found in the pasted URL")
    return code  # parse_qs already decoded it


def post_token(form: dict[str, str], timeout: float = 30) -> dict:
    """POST the token endpoint; returns the JSON reply or raises TokenError."""
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(form).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise TokenError(f"could not reach TikTok: {exc.reason}") from exc
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise TokenError(f"unexpected reply from TikTok: {body[:200]}") from exc
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict) and "access_token" not in payload:
        payload = payload["data"]
    if not isinstance(payload, dict) or not payload.get("access_token"):
        error = payload.get("error", "unknown_error") if isinstance(payload, dict) else "unknown_error"
        detail = payload.get("error_description", "") if isinstance(payload, dict) else ""
        raise TokenError(f"TikTok did not issue a token: {error} {detail}".strip())
    return payload


def token_updates(payload: dict, client_key: str, client_secret: str) -> dict[str, str]:
    updates = {ACCESS_ENV: str(payload["access_token"]), KEY_ENV: client_key, SECRET_ENV: client_secret}
    if payload.get("refresh_token"):
        updates[REFRESH_ENV] = str(payload["refresh_token"])
    return updates


def _duration(seconds: object) -> str:
    try:
        hours = int(seconds) / 3600  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "an unknown time"
    return f"{hours / 24:.0f} days" if hours >= 48 else f"{hours:.0f} hours"


def _ask(prompt: str, secret: bool = False) -> str:
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip()
    if not value:
        raise TokenError(f"no value entered for: {prompt.strip()}")
    return value


# --------------------------------------------------------------------------- commands


def _save(args: argparse.Namespace, payload: dict, client_key: str, client_secret: str) -> None:
    updates = token_updates(payload, client_key, client_secret)
    if args.print:
        for key, value in updates.items():
            print(f"{key}={value}")
        return
    update_env(args.env, updates)
    print(f"Saved {', '.join(updates)} to {args.env.resolve()}")
    print(f"  scopes granted: {payload.get('scope', '?')}")
    print(f"  access token valid for {_duration(payload.get('expires_in'))}, "
          f"refresh token for {_duration(payload.get('refresh_expires_in'))}")


def cmd_authorize(args: argparse.Namespace, env: dict[str, str]) -> None:
    client_key = args.client_key or os.environ.get(KEY_ENV) or env.get(KEY_ENV) or _ask("Client key: ")
    client_secret = (os.environ.get(SECRET_ENV) or env.get(SECRET_ENV)
                     or _ask("Client secret (hidden): ", secret=True))
    redirect_uri = (args.redirect_uri or os.environ.get(REDIRECT_ENV) or env.get(REDIRECT_ENV)
                    or _ask("Redirect URI (exactly as registered in Login Kit): "))
    scopes = tuple(s.strip() for s in args.scopes.split(",") if s.strip()) if args.scopes else (
        DIRECT_SCOPES if args.direct else INBOX_SCOPES)
    state = secrets.token_urlsafe(16)
    url = authorize_url(client_key, redirect_uri, scopes, state)

    print("\n1. Open this link and allow access with the TikTok account you post from:\n")
    print(f"   {url}\n")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # no browser on a server: the printed link is enough
            pass
    print("2. Your browser then opens your redirect URI (an error page there is fine).")
    pasted = _ask("   Paste the full address from the address bar here: ")
    code = parse_redirect(pasted, state)
    payload = post_token({
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    _save(args, payload, client_key, client_secret)


def cmd_refresh(args: argparse.Namespace, env: dict[str, str]) -> None:
    values = {name: os.environ.get(name) or env.get(name, "") for name in (REFRESH_ENV, KEY_ENV, SECRET_ENV)}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise TokenError(f"--refresh needs {', '.join(missing)} in {args.env} (run without --refresh first)")
    payload = post_token({
        "client_key": values[KEY_ENV],
        "client_secret": values[SECRET_ENV],
        "grant_type": "refresh_token",
        "refresh_token": values[REFRESH_ENV],
    })
    _save(args, payload, values[KEY_ENV], values[SECRET_ENV])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Get or renew a TikTok access token for autoshorts and store it in .env.",
        epilog="See README.md, section 'TikTok upload setup'.",
    )
    parser.add_argument("--env", type=Path, default=Path(".env"), help="the .env file to update (default: ./.env)")
    parser.add_argument("--refresh", action="store_true", help="renew the tokens already in .env")
    parser.add_argument("--direct", action="store_true", help="also request video.publish (upload.tiktok.mode: direct)")
    parser.add_argument("--scopes", help="comma-separated scopes (overrides --direct)")
    parser.add_argument("--client-key", help=f"app client key (default: ${KEY_ENV} or .env)")
    parser.add_argument("--redirect-uri", help=f"redirect URI registered in Login Kit (default: ${REDIRECT_ENV} or ask)")
    parser.add_argument("--no-browser", action="store_true", help="only print the link, do not open a browser")
    parser.add_argument("--print", action="store_true", help="print the KEY=value lines instead of writing .env")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = read_env(args.env)
    try:
        if args.refresh:
            cmd_refresh(args, env)
        else:
            cmd_authorize(args, env)
    except TokenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
