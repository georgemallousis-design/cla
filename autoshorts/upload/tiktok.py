"""TikTok Content Posting API uploader (FILE_UPLOAD, chunked PUT, status polling).

Two modes (``upload.tiktok.mode``):

* ``inbox``  - POST /v2/post/publish/inbox/video/init/ (scope ``video.upload``). The video
  lands in the TikTok app's inbox as a draft; you add the caption (see tiktok.txt) and
  post it by hand. Works for any developer app, no audit needed. TikTok allows at most
  5 pending drafts per 24 hours.
* ``direct`` - POST /v2/post/publish/creator_info/query/ then /v2/post/publish/video/init/
  (scope ``video.publish``). Posts immediately. Until your app passes TikTok's audit,
  posts are restricted to private (SELF_ONLY) visibility.

Authentication: this module does not run TikTok's OAuth flow. Create an app at
https://developers.tiktok.com, add the Content Posting API product, authorise your own
account through your app's redirect and put the user access token in the env var named by
``upload.tiktok.access_token_env`` (default TIKTOK_ACCESS_TOKEN). Access tokens last 24 h;
set TIKTOK_REFRESH_TOKEN, TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET as well and an expired
token is refreshed automatically. The new access token (and the new refresh token, when
TikTok rotates it) is written back to the .env next to config.yaml (atomically, mode
0600), so the next scheduled run starts from valid tokens.

Chunk rules (media transfer guide): chunks are 5-64 MB and uploaded in order, the final
chunk may be bigger (up to 128 MB) to absorb the remainder, ``total_chunk_count`` is
``video_size // chunk_size``, files under 5 MB go up as one chunk with
``chunk_size == video_size``, files over 64 MB must be split, 1-1000 chunks in total.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Iterator

import requests

from ..config import Config
from ..models import UploadResult
from ..utils import AutoShortsError, http_session, log, media_duration, redact, update_env

API_BASE = "https://open.tiktokapis.com"
CREATOR_INFO_URL = f"{API_BASE}/v2/post/publish/creator_info/query/"
DIRECT_INIT_URL = f"{API_BASE}/v2/post/publish/video/init/"
INBOX_INIT_URL = f"{API_BASE}/v2/post/publish/inbox/video/init/"
STATUS_URL = f"{API_BASE}/v2/post/publish/status/fetch/"
TOKEN_URL = f"{API_BASE}/v2/oauth/token/"

# Conservative readings of "5 MB" / "64 MB" so either MB or MiB interpretation is satisfied.
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64_000_000
DEFAULT_CHUNK = 10_000_000
MAX_FINAL_CHUNK = 128_000_000
MAX_CHUNKS = 1000

POLL_INTERVAL = 5.0
POLL_TIMEOUT = 120.0
API_TIMEOUT = 30
PUT_TIMEOUT = (15, 300)
PUT_RETRIES = 3
COVER_TIMESTAMP_MS = 1000

REFRESH_TOKEN_ENV = "TIKTOK_REFRESH_TOKEN"
CLIENT_KEY_ENV = "TIKTOK_CLIENT_KEY"
CLIENT_SECRET_ENV = "TIKTOK_CLIENT_SECRET"

PRIVACY_MOST_PRIVATE_FIRST = ("SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE")
DONE_STATUSES = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"}

_sleep = time.sleep  # patched in tests

UNAUDITED_HINT = (
    "Unaudited TikTok apps can only post privately (privacy_level SELF_ONLY, and TikTok may "
    "require the account itself to be private). Use mode 'inbox' or get the app audited."
)

ERROR_HINTS = {
    "access_token_invalid": (
        f"TikTok access token is invalid or expired (they last 24 h). Put a fresh token in the env "
        f"var, or set {REFRESH_TOKEN_ENV}, {CLIENT_KEY_ENV} and {CLIENT_SECRET_ENV} for automatic refresh."
    ),
    "scope_not_authorized": (
        "The TikTok token lacks the needed scope: 'video.upload' for inbox mode, 'video.publish' "
        "for direct mode. Re-authorise your app with that scope."
    ),
    "rate_limit_exceeded": "TikTok API rate limit hit; try again in a minute.",
    "spam_risk_too_many_posts": "Daily TikTok post limit for this account reached; try again tomorrow.",
    "spam_risk_too_many_pending_share": (
        "Too many drafts waiting in the TikTok inbox (max 5 per 24 h). Post or delete them in the app."
    ),
    "spam_risk_user_banned_from_posting": "This TikTok account is currently banned from posting.",
    "reached_active_user_cap": "Your TikTok app reached its daily cap of active posting users.",
    "unaudited_client_can_only_post_to_private_accounts": UNAUDITED_HINT,
    "privacy_level_option_mismatch": "privacy_level is not one this creator can use. " + UNAUDITED_HINT,
    "invalid_file_upload": "TikTok rejected the file (format, size or chunk layout).",
    "invalid_params": "TikTok rejected the request parameters.",
    "invalid_param": "TikTok rejected the request parameters.",
}

FAIL_REASON_HINTS = {
    "file_format_check_failed": "unsupported video format",
    "duration_check_failed": "video too long or too short for this account",
    "frame_rate_check_failed": "unsupported frame rate",
    "picture_size_check_failed": "unsupported resolution",
    "internal": "TikTok internal error, try again later",
    "publish_cancelled": "the post was cancelled",
    "auth_removed": "the user revoked the app's access",
    "spam_risk_too_many_posts": "daily post limit reached",
    "spam_risk_user_banned_from_posting": "the account is banned from posting",
    "spam_risk_text": "the caption was flagged as spam",
    "spam_risk": "the post was flagged as spam",
}


class TikTokAPIError(Exception):
    """An error envelope (or HTTP failure) returned by the TikTok API."""

    def __init__(self, code: str, message: str = "", http_status: int | None = None, log_id: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.log_id = log_id


# --------------------------------------------------------------------------- chunk math


def chunk_plan(video_size: int, preferred: int = DEFAULT_CHUNK) -> tuple[int, int]:
    """Return (chunk_size, total_chunk_count) that satisfies TikTok's chunk rules.

    Files up to 64 MB that fit in fewer than two preferred chunks go up whole (always the
    case below 5 MB); bigger files use ``preferred``-sized chunks with the remainder
    folded into the final chunk.
    """
    if video_size <= 0:
        raise ValueError("video_size must be positive")
    preferred = max(MIN_CHUNK, min(MAX_CHUNK, int(preferred)))
    if video_size <= MAX_CHUNK and video_size < 2 * preferred:
        return video_size, 1
    chunk = preferred
    if video_size > MAX_CHUNK:
        chunk = min(chunk, video_size // 2)  # > 64 MB must be split into several chunks
    if video_size // chunk > MAX_CHUNKS:
        chunk = -(-video_size // MAX_CHUNKS)
    count = video_size // chunk
    if video_size - chunk * (count - 1) > MAX_FINAL_CHUNK or chunk > MAX_CHUNK:
        raise AutoShortsError(f"video of {video_size} bytes is too large for TikTok's upload API")
    return chunk, count


def chunk_ranges(video_size: int, chunk_size: int, count: int) -> Iterator[tuple[int, int]]:
    """Inclusive (first_byte, last_byte) per chunk; the last chunk takes the remainder."""
    for index in range(count):
        start = index * chunk_size
        end = video_size - 1 if index == count - 1 else start + chunk_size - 1
        yield start, end


# --------------------------------------------------------------------------- HTTP client


def _json_dict(resp: requests.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _refresh_credentials() -> tuple[str, str, str] | None:
    """(refresh_token, client_key, client_secret) from the environment, or None."""
    refresh, key, secret = (Config.secret(n) for n in (REFRESH_TOKEN_ENV, CLIENT_KEY_ENV, CLIENT_SECRET_ENV))
    return (refresh, key, secret) if refresh and key and secret else None


class TikTokClient:
    """Minimal JSON client; refreshes the access token once on 401 when it can."""

    def __init__(self, access_token: str | None, token_env: str, session: requests.Session | None = None,
                 env_file: Path | None = None):
        self.access_token = access_token
        self.token_env = token_env
        self.session = session or http_session()
        self.env_file = env_file  # refreshed tokens are saved here (when the file exists)
        self._refreshed = False

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json; charset=UTF-8"}

    def _post_once(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(url, json=body, headers=self._headers(), timeout=API_TIMEOUT)
        payload = _json_dict(resp)
        error = payload.get("error")
        error = error if isinstance(error, dict) else {}
        code = str(error.get("code") or "")
        if resp.status_code >= 400 or (code and code != "ok"):
            raise TikTokAPIError(
                code or f"http_{resp.status_code}",
                str(error.get("message") or resp.text[:200] or ""),
                resp.status_code,
                str(error.get("log_id") or ""),
            )
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._post_once(url, body)
        except TikTokAPIError as exc:
            expired = exc.code == "access_token_invalid" or (exc.http_status == 401 and exc.code != "scope_not_authorized")
            if not expired or self._refreshed or not _refresh_credentials():
                raise
            self.refresh()
            return self._post_once(url, body)

    def refresh(self) -> None:
        """Exchange the refresh token for a new access token; saved to os.environ and ``env_file``."""
        creds = _refresh_credentials()
        if not creds:
            raise AutoShortsError(f"set {REFRESH_TOKEN_ENV}, {CLIENT_KEY_ENV} and {CLIENT_SECRET_ENV} to refresh TikTok tokens")
        refresh_token, client_key, client_secret = creds
        self._refreshed = True
        resp = self.session.post(
            TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
            timeout=API_TIMEOUT,
        )
        payload = _json_dict(resp)
        if isinstance(payload.get("data"), dict) and "access_token" not in payload:
            payload = payload["data"]
        token = payload.get("access_token")
        if resp.status_code >= 400 or not token:
            raise TikTokAPIError(
                str(payload.get("error") or "token_refresh_failed"),
                f"TikTok token refresh failed: {payload.get('error_description') or f'HTTP {resp.status_code}'}",
                resp.status_code,
            )
        self.access_token = token
        os.environ[self.token_env] = token  # later uploads in this run reuse it
        updates = {self.token_env: str(token)}
        new_refresh = payload.get("refresh_token")
        if new_refresh and new_refresh != refresh_token:
            # TikTok: "You must use the newly-returned token if the value is different".
            os.environ[REFRESH_TOKEN_ENV] = new_refresh
            updates[REFRESH_TOKEN_ENV] = str(new_refresh)
        log.info("TikTok access token refreshed%s", " (TikTok also issued a new refresh token)"
                 if len(updates) > 1 else "")
        self._save(updates)

    def _save(self, updates: dict[str, str]) -> None:
        """Write refreshed tokens back to .env so the next run (a new process) uses them."""
        if self.env_file is None or not Path(self.env_file).is_file():
            if REFRESH_TOKEN_ENV in updates:
                log.warning("TikTok issued a new refresh token but there is no .env to save it in; "
                            "update %s by hand (python deploy/tiktok_token.py --refresh)", REFRESH_TOKEN_ENV)
            return
        try:
            update_env(self.env_file, updates)
        except OSError as exc:
            log.warning("could not save the refreshed TikTok token(s) %s to %s (%s); the next run may need "
                        "python deploy/tiktok_token.py --refresh", ", ".join(updates), self.env_file,
                        type(exc).__name__)
        else:
            log.info("saved the refreshed TikTok token(s) %s to %s", ", ".join(updates), self.env_file)


# --------------------------------------------------------------------------- steps


def choose_privacy(requested: str, options: list[str]) -> str:
    """``requested`` if the creator allows it, else the most private option offered."""
    if requested in options:
        return requested
    fallback = next((p for p in PRIVACY_MOST_PRIVATE_FIRST if p in options), "SELF_ONLY")
    log.warning("TikTok privacy %s is not available for this account (options: %s); using %s",
                requested, ", ".join(options) or "none", fallback)
    return fallback


def post_info(meta: dict[str, Any], creator: dict[str, Any], privacy: str, is_aigc: bool) -> dict[str, Any]:
    return {
        "title": str(meta.get("caption", ""))[:2200],
        "privacy_level": privacy,
        # Respect the creator's own interaction settings, otherwise allow everything.
        "disable_duet": bool(creator.get("duet_disabled", False)),
        "disable_comment": bool(creator.get("comment_disabled", False)),
        "disable_stitch": bool(creator.get("stitch_disabled", False)),
        "video_cover_timestamp_ms": COVER_TIMESTAMP_MS,
        "brand_content_toggle": False,  # not a paid partnership
        "brand_organic_toggle": False,
        "is_aigc": bool(meta.get("is_aigc", is_aigc)),
    }


def _put_chunk(session: requests.Session, url: str, data: bytes, headers: dict[str, str]) -> None:
    for attempt in range(1, PUT_RETRIES + 1):
        try:
            resp = session.put(url, data=data, headers=headers, timeout=PUT_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as exc:
            error = f"{type(exc).__name__}: {redact(exc)}"  # the URL carries upload_token
        else:
            if resp.status_code in (200, 201, 206):
                return
            error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code < 500 and resp.status_code != 429:
                raise TikTokAPIError("upload_failed", f"chunk {headers['Content-Range']} rejected: {error}", resp.status_code)
        if attempt == PUT_RETRIES:
            raise TikTokAPIError("upload_failed", f"chunk {headers['Content-Range']} failed: {error}")
        log.warning("TikTok chunk upload failed (%s); retrying", error)
        _sleep(2 ** attempt)


def upload_chunks(session: requests.Session, upload_url: str, path: Path, video_size: int,
                  chunk_size: int, count: int) -> None:
    """PUT the file to ``upload_url`` in order, one Content-Range per chunk."""
    with open(path, "rb") as fh:
        for index, (start, end) in enumerate(chunk_ranges(video_size, chunk_size, count), 1):
            fh.seek(start)
            data = fh.read(end - start + 1)
            headers = {
                "Content-Type": "video/mp4",
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {start}-{end}/{video_size}",
            }
            log.debug("TikTok chunk %d/%d: %s", index, count, headers["Content-Range"])
            _put_chunk(session, upload_url, data, headers)


def poll_status(client: TikTokClient, publish_id: str, *, interval: float = POLL_INTERVAL,
                timeout: float = POLL_TIMEOUT) -> dict[str, Any]:
    """Poll until a final status (or ``timeout``); returns the last status data."""
    data: dict[str, Any] = {}
    polls = max(1, int(timeout // interval) + 1)
    for attempt in range(polls):
        try:
            data = client.post(STATUS_URL, {"publish_id": publish_id})
        except TikTokAPIError as exc:
            if exc.code != "rate_limit_exceeded" and (exc.http_status or 0) < 500:
                raise
            log.debug("TikTok status check failed (%s), retrying", exc.code)
        except (requests.ConnectionError, requests.Timeout) as exc:
            log.debug("TikTok status check failed (%s), retrying", exc)
        if data.get("status") in DONE_STATUSES:
            return data
        if attempt < polls - 1:
            _sleep(interval)
    return data


def explain(exc: TikTokAPIError) -> str:
    hint = ERROR_HINTS.get(exc.code, "")
    detail = exc.code + (f" ({exc.message})" if exc.message else "")
    if exc.log_id:
        detail += f" [log_id {exc.log_id}]"
    return f"{hint} [{detail}]" if hint else f"TikTok API error: {detail}"


def _result_from_status(publish_id: str, status: dict[str, Any], username: str, mode: str) -> UploadResult:
    state = status.get("status", "")
    if state == "FAILED":
        reason = str(status.get("fail_reason") or "unknown")
        hint = FAIL_REASON_HINTS.get(reason, "")
        return UploadResult(platform="tiktok", ok=False, id=publish_id,
                            error=f"TikTok could not process the video: {reason}" + (f" ({hint})" if hint else ""))
    if state == "SEND_TO_USER_INBOX":
        log.info("TikTok: draft sent to your inbox; open the TikTok app, paste the caption from tiktok.txt and post.")
    elif state == "PUBLISH_COMPLETE":
        log.info("TikTok: posted.")
    else:
        log.warning("TikTok is still processing the upload (status %s); check the app in a few minutes.", state or "unknown")
    post_ids = status.get("publicaly_available_post_id") or []  # (sic) TikTok's field name
    if post_ids and mode == "direct":
        post_id = str(post_ids[0])
        url = f"https://www.tiktok.com/@{username}/video/{post_id}" if username else ""
        return UploadResult(platform="tiktok", ok=True, id=post_id, url=url)
    return UploadResult(platform="tiktok", ok=True, id=publish_id)


def _check_duration(video_path: Path, creator: dict[str, Any]) -> str:
    limit = creator.get("max_video_post_duration_sec")
    if not limit:
        return ""
    try:
        duration = media_duration(video_path)
    except (AutoShortsError, OSError, ValueError):
        return ""
    if duration > float(limit):
        return f"video is {duration:.0f}s but this TikTok account can post at most {int(limit)}s via the API"
    return ""


def _init_direct(client: TikTokClient, video_path: Path, meta: dict[str, Any], is_aigc: bool,
                 source_info: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """creator_info -> video/init. Returns (init data, creator username)."""
    creator = client.post(CREATOR_INFO_URL, {})
    too_long = _check_duration(video_path, creator)
    if too_long:
        raise TikTokAPIError("duration_check_failed", too_long)
    requested = str(meta.get("privacy_level") or "SELF_ONLY").upper()
    privacy = choose_privacy(requested, list(creator.get("privacy_level_options") or []))
    body = {"post_info": post_info(meta, creator, privacy, is_aigc), "source_info": source_info}
    try:
        data = client.post(DIRECT_INIT_URL, body)
    except TikTokAPIError as exc:
        if exc.code != "unaudited_client_can_only_post_to_private_accounts" or privacy == "SELF_ONLY":
            raise
        log.warning("TikTok: app not audited yet, retrying as SELF_ONLY (private)")
        body = {**body, "post_info": {**body["post_info"], "privacy_level": "SELF_ONLY"}}
        data = client.post(DIRECT_INIT_URL, body)
    return data, str(creator.get("creator_username") or "")


def upload(cfg: Config, video_path: str | Path, meta: dict[str, Any]) -> UploadResult:
    """Upload ``video_path`` to TikTok. API refusals come back as UploadResult(ok=False)."""
    if "tiktok" in meta and "caption" not in meta:  # accept the full build_metadata() dict too
        meta = meta["tiktok"]
    tcfg = cfg.upload.tiktok
    mode = str(tcfg.mode or "inbox").strip().lower()
    if mode not in ("inbox", "direct"):
        raise AutoShortsError(f"upload.tiktok.mode must be 'inbox' or 'direct', not {tcfg.mode!r}")
    video_path = Path(video_path)
    if not video_path.is_file():
        raise AutoShortsError(f"video not found: {video_path}")
    token = cfg.secret(tcfg.access_token_env)
    if not token and not _refresh_credentials():
        raise AutoShortsError(
            f"No TikTok access token in ${tcfg.access_token_env}. Create an app at https://developers.tiktok.com, "
            "add the Content Posting API (scope video.upload for inbox, video.publish for direct), authorise "
            f"your account and put the user access token in .env (optionally {REFRESH_TOKEN_ENV}, "
            f"{CLIENT_KEY_ENV} and {CLIENT_SECRET_ENV} for automatic refresh)."
        )

    client = TikTokClient(token, tcfg.access_token_env, env_file=cfg.base_dir / ".env")
    video_size = video_path.stat().st_size
    chunk_size, count = chunk_plan(video_size)
    source_info = {"source": "FILE_UPLOAD", "video_size": video_size, "chunk_size": chunk_size, "total_chunk_count": count}
    log.info("Uploading %s to TikTok (%s mode, %d chunk(s))", video_path.name, mode, count)
    try:
        if not token:
            client.refresh()
        username = ""
        if mode == "direct":
            data, username = _init_direct(client, video_path, meta, tcfg.is_aigc, source_info)
        else:
            data = client.post(INBOX_INIT_URL, {"source_info": source_info})
        publish_id, upload_url = str(data.get("publish_id") or ""), str(data.get("upload_url") or "")
        if not publish_id or not upload_url:
            raise TikTokAPIError("invalid_response", "init response had no publish_id/upload_url")
        upload_chunks(client.session, upload_url, video_path, video_size, chunk_size, count)
        status = poll_status(client, publish_id)
    except TikTokAPIError as exc:
        return UploadResult(platform="tiktok", ok=False, error=explain(exc))
    except requests.RequestException as exc:
        return UploadResult(platform="tiktok", ok=False, error=f"network error talking to TikTok: {redact(exc)}")
    return _result_from_status(publish_id, status, username, mode)
