"""YouTube Data API v3 uploader: OAuth desktop flow + resumable ``videos.insert``.

One-time setup (free):

1. https://console.cloud.google.com -> create a project -> "APIs & Services" -> enable
   "YouTube Data API v3".
2. "OAuth consent screen": External, add your own Google account as a test user.
3. "Credentials" -> "Create credentials" -> "OAuth client ID" -> type "Desktop app" ->
   download the JSON and save it as ``secrets/client_secret.json`` (``upload.youtube.client_secrets``).
4. Run ``autoshorts auth youtube`` once on a machine with a browser. The token is saved to
   ``upload.youtube.token_file`` and refreshed automatically afterwards. On a headless
   server, authorise on your PC and copy the token file over.

Things worth knowing:

* Only the ``youtube.upload`` scope is requested; it covers ``videos.insert`` and
  ``thumbnails.set``.
* Quota: uploads have their own daily bucket (default 100 ``videos.insert`` calls per
  project per day since June 2026; before that each upload cost ~100 of the 10,000 daily
  units). ``thumbnails.set`` costs ~50 units. Quota resets at midnight Pacific Time.
* Videos uploaded through an unaudited API project created after 28 July 2020 are locked
  to private. Request an audit (https://support.google.com/youtube/contact/yt_api_form)
  before relying on public uploads.
* While the OAuth consent screen is in "Testing" mode, Google expires refresh tokens after
  7 days; re-run ``autoshorts auth youtube`` or publish the consent screen.
* Custom thumbnails need a verified channel (https://www.youtube.com/verify); Shorts often
  ignore them anyway, so a failure there only logs a warning.
"""
from __future__ import annotations

import http.client
import json
import os
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..config import Config
from ..models import UploadResult
from ..utils import AutoShortsError, ensure_dir, log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CHUNK_SIZE = 8 * 1024 * 1024  # must be a multiple of 256 KiB
MAX_RETRIES = 5
RETRIABLE_STATUS = {500, 502, 503, 504}
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024
HEADLESS_PORT = 8765  # fixed so an SSH tunnel (ssh -L 8765:localhost:8765 server) can reach it

INSTALL_HINT = (
    "YouTube upload needs Google's API client libraries: pip install 'autoshorts[youtube]' "
    "(google-api-python-client, google-auth-oauthlib, google-auth-httplib2)"
)

_sleep = time.sleep  # patched in tests


# --------------------------------------------------------------------------- optional deps


def _google() -> SimpleNamespace:
    """Import the Google client libraries lazily (the core works without them)."""
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise AutoShortsError(INSTALL_HINT) from exc
    transport_errors: tuple[type[BaseException], ...] = ()
    try:
        import httplib2

        transport_errors = (httplib2.HttpLib2Error,)
    except ImportError:  # pragma: no cover - installed with google-api-python-client
        pass
    return SimpleNamespace(
        build=build,
        Credentials=Credentials,
        HttpError=HttpError,
        InstalledAppFlow=InstalledAppFlow,
        MediaFileUpload=MediaFileUpload,
        RefreshError=RefreshError,
        Request=Request,
        transport_errors=transport_errors,
    )


# --------------------------------------------------------------------------- credentials


def _client_secrets_path(cfg: Config) -> Path:
    path = cfg.path(cfg.upload.youtube.client_secrets)
    if not path.is_file():
        raise AutoShortsError(
            f"YouTube OAuth client file not found: {path}\n"
            "Create one (free): https://console.cloud.google.com -> enable 'YouTube Data API v3' -> "
            "OAuth consent screen (External, add yourself as a test user) -> Credentials -> "
            "Create OAuth client ID -> 'Desktop app' -> download JSON and save it at that path."
        )
    return path


def save_token(path: Path, creds: Any) -> Path:
    """Write the credentials JSON readable by the owner only (where the OS supports it)."""
    ensure_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    try:
        os.chmod(path, 0o600)  # also tighten a token file that already existed
    except OSError:  # pragma: no cover - e.g. some network filesystems
        pass
    return path


def _looks_headless() -> bool:
    if sys.platform.startswith("linux"):
        return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return False


def authorize(cfg: Config) -> None:
    """Run the interactive OAuth flow once and save the token for future uploads."""
    g = _google()
    secrets = _client_secrets_path(cfg)
    token_path = cfg.path(cfg.upload.youtube.token_file)
    flow = g.InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)

    headless = _looks_headless()
    if headless:
        log.warning(
            "No display found. Easiest fix: run 'autoshorts auth youtube' on a PC with a browser and "
            "copy %s to the same path on this server. Or keep this running, open an SSH tunnel "
            "from your PC (ssh -L %d:localhost:%d <this-server>) and open the URL below in your "
            "PC's browser.",
            token_path, HEADLESS_PORT, HEADLESS_PORT,
        )
    try:
        creds = flow.run_local_server(
            port=HEADLESS_PORT if headless else 0,
            open_browser=not headless,
            authorization_prompt_message="Open this URL in your browser to allow uploads:\n{url}",
            success_message="autoshorts can now upload to YouTube. You may close this tab.",
            prompt="consent",  # always return a refresh token, even on re-authorisation
        )
    except OSError as exc:
        raise AutoShortsError(f"could not start the local OAuth callback server: {exc}") from exc
    save_token(token_path, creds)
    log.info("YouTube token saved to %s (keep it private).", token_path)


def load_credentials(cfg: Config, g: SimpleNamespace | None = None) -> Any:
    """Load the saved token, refreshing (and re-saving) it when expired."""
    g = g or _google()
    token_path = cfg.path(cfg.upload.youtube.token_file)
    if not token_path.is_file():
        raise AutoShortsError(
            f"No YouTube token at {token_path}. Run 'autoshorts auth youtube' first "
            "(on a headless server: run it on a PC with a browser and copy the token file over)."
        )
    try:
        creds = g.Credentials.from_authorized_user_file(str(token_path), SCOPES)
    except (ValueError, OSError) as exc:
        raise AutoShortsError(f"YouTube token {token_path} is unreadable ({exc}); run 'autoshorts auth youtube' again") from exc
    if creds.valid:
        return creds
    if not (creds.expired and creds.refresh_token):
        raise AutoShortsError("YouTube token is invalid; run 'autoshorts auth youtube' again")
    try:
        creds.refresh(g.Request())
    except g.RefreshError as exc:
        raise AutoShortsError(
            "YouTube token could not be refreshed (revoked, or expired after 7 days because the OAuth "
            "consent screen is in 'Testing' mode). Run 'autoshorts auth youtube' again."
        ) from exc
    save_token(token_path, creds)
    log.debug("YouTube token refreshed")
    return creds


# --------------------------------------------------------------------------- errors


def _error_info(exc: Any) -> tuple[int, str, str]:
    """(HTTP status, first error reason, message) from a googleapiclient HttpError."""
    status = int(getattr(getattr(exc, "resp", None), "status", 0) or 0)
    reason, message = "", ""
    content = getattr(exc, "content", b"") or b""
    try:
        err = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content).get("error", {})
        message = err.get("message", "") or ""
        details = err.get("errors") or []
        if details:
            reason = details[0].get("reason", "") or ""
    except (ValueError, AttributeError):
        message = str(exc)
    return status, reason, message


ERROR_HINTS = {
    "quotaExceeded": (
        "YouTube API quota used up for today. Uploads have their own daily quota (default 100 per "
        "project); it resets at midnight Pacific Time. Try again tomorrow or request more quota in "
        "the Google Cloud console."
    ),
    "uploadLimitExceeded": "This channel hit YouTube's daily upload limit. Try again in 24 hours.",
    "rateLimitExceeded": "Too many requests to the YouTube API in a short time; wait a bit and retry.",
    "userRateLimitExceeded": "Too many requests to the YouTube API in a short time; wait a bit and retry.",
    "youtubeSignupRequired": "This Google account has no YouTube channel yet; create one on youtube.com first.",
    "invalidTitle": "YouTube rejected the title (empty, too long or contains '<'/'>').",
    "invalidDescription": "YouTube rejected the description (too long or contains '<'/'>').",
    "invalidTags": "YouTube rejected the tags (over 500 characters in total?).",
    "invalidCategoryId": "Unknown upload.youtube.category_id; 27 = Education, 24 = Entertainment.",
    "forbidden": "YouTube refused the upload for this account (check the channel status in YouTube Studio).",
}


def explain_http_error(exc: Any) -> str:
    status, reason, message = _error_info(exc)
    if status == 401 or reason in ("authError", "unauthorized"):
        return f"YouTube rejected the credentials (HTTP 401); run 'autoshorts auth youtube' again. {message}".strip()
    hint = ERROR_HINTS.get(reason, "")
    detail = f"HTTP {status} {reason}: {message}".strip()
    return f"{hint} ({detail})" if hint else detail


# --------------------------------------------------------------------------- upload


def video_body(meta: dict[str, Any]) -> dict[str, Any]:
    """videos.insert request body (snippet + status) from build_metadata(...)["youtube"]."""
    snippet: dict[str, Any] = {
        "title": meta["title"],
        "description": meta.get("description", ""),
        "tags": list(meta.get("tags", [])),
        "categoryId": str(meta.get("category_id", "27")),
    }
    language = meta.get("default_language")
    if language:
        snippet["defaultLanguage"] = language
        snippet["defaultAudioLanguage"] = language
    status = {
        "privacyStatus": meta.get("privacy", "private"),
        "selfDeclaredMadeForKids": bool(meta.get("made_for_kids", False)),
        "containsSyntheticMedia": bool(meta.get("contains_synthetic_media", True)),
    }
    return {"snippet": snippet, "status": status}


def resumable_upload(request: Any, g: SimpleNamespace, max_retries: int = MAX_RETRIES) -> dict[str, Any]:
    """Drive ``request.next_chunk()`` to completion, retrying 5xx and network errors.

    Raises g.HttpError for non-retriable API errors and AutoShortsError after
    ``max_retries`` consecutive failures.
    """
    retriable = (OSError, http.client.HTTPException, *g.transport_errors)
    response = None
    retry = 0
    while response is None:
        error = ""
        try:
            progress, response = request.next_chunk()
            if progress is not None:
                log.info("YouTube upload %d%%", int(progress.progress() * 100))
            retry = 0
        except g.HttpError as exc:
            status, reason, message = _error_info(exc)
            if status not in RETRIABLE_STATUS:
                raise
            error = f"HTTP {status} {reason} {message}".strip()
        except retriable as exc:
            error = f"{type(exc).__name__}: {exc}"
        if error:
            retry += 1
            if retry > max_retries:
                raise AutoShortsError(f"YouTube upload failed after {max_retries} retries: {error}")
            delay = random.uniform(0.5, 2 ** retry)
            log.warning("YouTube upload hiccup (%s); retry %d/%d in %.1fs", error, retry, max_retries, delay)
            _sleep(delay)
    if not isinstance(response, dict) or "id" not in response:
        raise AutoShortsError(f"YouTube upload returned an unexpected response: {response!r}")
    return response


def set_thumbnail(youtube: Any, g: SimpleNamespace, video_id: str, thumbnail: Path) -> bool:
    """Set a custom thumbnail; failures only log a warning (custom thumbnails need a verified channel)."""
    if not thumbnail.is_file():
        log.warning("thumbnail %s not found, skipping", thumbnail)
        return False
    if thumbnail.stat().st_size > THUMBNAIL_MAX_BYTES:
        log.warning("thumbnail %s is over YouTube's 2 MB limit, skipping", thumbnail)
        return False
    mimetype = "image/png" if thumbnail.suffix.lower() == ".png" else "image/jpeg"
    try:
        media = g.MediaFileUpload(str(thumbnail), mimetype=mimetype)
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        return True
    except (g.HttpError, OSError) as exc:
        detail = explain_http_error(exc) if isinstance(exc, g.HttpError) else str(exc)
        log.warning(
            "YouTube thumbnail not set (%s). Custom thumbnails need a verified channel: "
            "https://www.youtube.com/verify", detail,
        )
        return False


def upload(cfg: Config, video_path: str | Path, meta: dict[str, Any], thumbnail: str | Path | None = None) -> UploadResult:
    """Upload ``video_path`` as a Short. API refusals come back as UploadResult(ok=False)."""
    if "youtube" in meta and "title" not in meta:  # accept the full build_metadata() dict too
        meta = meta["youtube"]
    video_path = Path(video_path)
    if not video_path.is_file():
        raise AutoShortsError(f"video not found: {video_path}")
    g = _google()
    creds = load_credentials(cfg, g)
    youtube = g.build("youtube", "v3", credentials=creds, cache_discovery=False)
    media = g.MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=CHUNK_SIZE, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=video_body(meta), media_body=media)

    log.info("Uploading %s to YouTube (%s)", video_path.name, meta.get("privacy", "private"))
    try:
        response = resumable_upload(request, g)
    except g.HttpError as exc:
        return UploadResult(platform="youtube", ok=False, error=explain_http_error(exc))
    except AutoShortsError as exc:
        return UploadResult(platform="youtube", ok=False, error=str(exc))

    video_id = str(response["id"])
    url = f"https://youtube.com/shorts/{video_id}"
    log.info("YouTube upload done: %s", url)
    if meta.get("privacy", "private") != "private":
        log.info("If the video shows as private: unaudited API projects are locked to private uploads.")
    if thumbnail:
        set_thumbnail(youtube, g, video_id, Path(thumbnail))
    return UploadResult(platform="youtube", ok=True, id=video_id, url=url)
