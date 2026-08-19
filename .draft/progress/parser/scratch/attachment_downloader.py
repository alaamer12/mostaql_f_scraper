"""
attachment_downloader.py
-------------------------
Resolves/downloads the project asset files reported by
analyzer.extract_attachments() (e.g. the brief .docx).

Why a separate module: pipeline.py's job is PARSING (turn HTML into
fields), not performing network side-effects. Downloading is optional,
stateful (needs credentials), and shouldn't run just because someone
called parse_project() - so it lives here, called explicitly.

The problem being solved:
    On Mostaql, an anonymous (not-logged-in) request sees the attachment
    <a href> pointing at "/register?t=..." instead of the real file -
    analyzer.py already flags this as `requires_auth=True` with `url=None`
    (only `raw_url` kept). To actually fetch the file we need an
    authenticated session, i.e. a real browser's Mostaql cookies.

Configuration (no hardcoded secrets, matches the project's convention of
reading credentials from the environment rather than from code):
    MOSTAQL_COOKIE       - the raw `Cookie:` header value copied from a
                            logged-in browser session (e.g.
                            "remember_user_token=...; mostaql_session=...").
    MOSTAQL_COOKIE_FILE  - alternative: path to a text file containing that
                            same cookie header string (useful so the value
                            never has to touch shell history).

If neither is configured (the common case), attachments that require auth
are NOT downloaded - they are returned as plain links with a clear message
asking the user to open `raw_url` in their own logged-in browser and
download manually. This module never guesses/attempts a login flow itself.
"""

import os
import urllib.request
import urllib.error

STATUS_READY_URL = "ready_url"                 # public link, no auth needed
STATUS_DOWNLOADED = "downloaded"                # fetched to disk successfully
STATUS_MANUAL_DOWNLOAD_REQUIRED = "manual_download_required"  # needs a human
STATUS_AUTH_FAILED = "auth_failed"              # cookie provided but rejected

_HTML_SNIFF_MARKERS = (b"<!DOCTYPE html", b"<html")


def get_configured_cookie_header():
    """Read the optional auth cookie from configuration (env var or a file
    path stored in an env var) - never from a hardcoded value in code."""
    cookie = os.environ.get("MOSTAQL_COOKIE")
    if cookie:
        return '; '.join([line.strip() for line in cookie.strip().splitlines() if line.strip()])

    cookie_file = os.environ.get("MOSTAQL_COOKIE_FILE")
    if cookie_file and os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.read().splitlines() if line.strip()]
            return '; '.join(lines)

    return None


def _looks_like_html(raw_bytes):
    head = raw_bytes[:512].lstrip()
    return any(head.startswith(marker) or marker in head[:64] for marker in _HTML_SNIFF_MARKERS)


def resolve_attachment(attachment, dest_dir=None, cookie_header=None):
    """Resolve a single attachment dict (as returned by
    analyzer.extract_attachments) into a definitive outcome:

      - not requires_auth        -> STATUS_READY_URL, just use `url` directly.
      - requires_auth + no cookie configured
                                  -> STATUS_MANUAL_DOWNLOAD_REQUIRED, with a
                                     `message` pointing the user at `raw_url`.
      - requires_auth + cookie configured
                                  -> attempt an authenticated GET; if the
                                     response is still an HTML page (cookie
                                     rejected/expired) -> STATUS_AUTH_FAILED,
                                     otherwise save the file and return
                                     STATUS_DOWNLOADED with `local_path`.

    Never raises for the "no cookie" / "not needed" paths - only network
    failures during an actual authenticated attempt propagate as an
    `error` message field, not an exception.
    """
    filename = attachment.get("filename") or "attachment"
    raw_url = attachment.get("raw_url")

    if not attachment.get("requires_auth"):
        return {
            **attachment,
            "status": STATUS_READY_URL,
            "message": None,
        }

    if not raw_url:
        return {
            **attachment,
            "status": STATUS_MANUAL_DOWNLOAD_REQUIRED,
            "message": f"No URL captured for '{filename}'; nothing to download.",
        }

    cookie_header = cookie_header or get_configured_cookie_header()
    if not cookie_header:
        return {
            **attachment,
            "status": STATUS_MANUAL_DOWNLOAD_REQUIRED,
            "message": (
                f"'{filename}' requires a logged-in Mostaql session to download. "
                f"No MOSTAQL_COOKIE/MOSTAQL_COOKIE_FILE configured, so it was NOT "
                f"fetched automatically. Please open this link in your own "
                f"logged-in browser and download it manually: {raw_url}"
            ),
        }

    request = urllib.request.Request(raw_url, headers={
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookie_header,
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        return {
            **attachment,
            "status": STATUS_AUTH_FAILED,
            "message": f"Download attempt for '{filename}' failed: {exc}. "
                       f"Manual link: {raw_url}",
        }

    if _looks_like_html(data):
        # The cookie was rejected/expired: Mostaql redirected us right back
        # to a login/register HTML page instead of the actual file bytes.
        return {
            **attachment,
            "status": STATUS_AUTH_FAILED,
            "message": (
                f"Configured cookie was not accepted for '{filename}' (received an "
                f"HTML page instead of a file - likely an expired/invalid session). "
                f"Manual link: {raw_url}"
            ),
        }

    local_path = None
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
        local_path = os.path.join(dest_dir, filename)
        with open(local_path, "wb") as f:
            f.write(data)

    return {
        **attachment,
        "status": STATUS_DOWNLOADED,
        "local_path": local_path,
        "message": None,
    }


def resolve_attachments(attachments, dest_dir=None, cookie_header=None):
    """Bulk version of resolve_attachment() for the list returned by
    pipeline.parse_project()["attachments"]."""
    cookie_header = cookie_header or get_configured_cookie_header()
    return [resolve_attachment(a, dest_dir=dest_dir, cookie_header=cookie_header) for a in attachments]
