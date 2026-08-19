"""
test_cookie_download.py
------------------------
Ad-hoc, throwaway script (not part of the test suite) to actually test
whether the cookies.txt the user exported from the browser's "Application"
tab are enough to authenticate a plain `urllib.request` call and download
the real attachment link found in project_completed_1.html.

cookies.txt format (one per line): NAME="VALUE"
This is NOT the raw `Cookie:` header format (`NAME=VALUE; NAME2=VALUE2`),
so we parse it and rebuild the header string ourselves.
"""
import os
import re
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
COOKIES_PATH = os.path.join(os.path.dirname(HERE), "cookies.txt")

REAL_ATTACHMENT_URL = "https://mostaql.com/register?t=V62CHPIMc98XMqhTAQfOAgtzKiPFlowmsnnjCEaH"
PROJECT_PAGE_URL = "https://mostaql.com/project/1242939-%D8%A5%D9%83%D9%85%D8%A7%D9%84-%D8%AA%D8%B7%D8%A8%D9%8A%D9%82-saas-%D8%AC%D8%A7%D9%87%D8%B2-80-%D9%88%D8%A5%D8%B7%D9%84%D8%A7%D9%82%D9%87-netlify-supabase-claude-api?deal_id=9863977"


def build_cookie_header(path):
    header_parts = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip('"')
            header_parts.append(f"{name}={value}")
    return "; ".join(header_parts)


def fetch(url, cookie_header):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cookie": cookie_header,
        "Referer": "https://mostaql.com/",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            return response.status, response.headers.get("Content-Type"), data
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type") if e.headers else None, e.read()
    except urllib.error.URLError as e:
        return None, None, str(e).encode()


def looks_like_html(data):
    head = data[:512].lstrip()
    return head.startswith(b"<!DOCTYPE html") or b"<html" in head[:200]


def main():
    if not os.path.exists(COOKIES_PATH):
        print(f"cookies.txt not found at {COOKIES_PATH}")
        return

    cookie_header = build_cookie_header(COOKIES_PATH)
    print("Built Cookie header (truncated):")
    print(cookie_header[:150] + "...")
    print()

    # Step 1: sanity check - fetch the logged-in project page itself and see
    # if it now shows a real file link (no '/register?t=' href) instead of
    # the anonymous one we captured earlier.
    print("=== Step 1: Re-fetching the project page with cookies ===")
    status, ctype, data = fetch(PROJECT_PAGE_URL, cookie_header)
    print(f"HTTP status: {status}, Content-Type: {ctype}, bytes: {len(data)}")
    if status == 200 and not looks_like_html(data):
        print("WARNING: expected HTML page but got non-HTML content.")
    elif status == 200:
        page_text = data.decode("utf-8", errors="ignore")
        has_register_link = "/register?t=" in page_text
        # crude login indicator: look for a logout link or user menu marker
        looks_logged_in = ("تسجيل الخروج" in page_text) or ("logout" in page_text.lower())
        print(f"Still contains '/register?t=' attachment link: {has_register_link}")
        print(f"Page appears to show a logged-in indicator (logout link found): {looks_logged_in}")
    else:
        print("Could not fetch page successfully with these cookies.")

    print()
    print("=== Step 2: Attempting to download the attachment link directly ===")
    status2, ctype2, data2 = fetch(REAL_ATTACHMENT_URL, cookie_header)
    print(f"HTTP status: {status2}, Content-Type: {ctype2}, bytes: {len(data2)}")
    if status2 == 200 and not looks_like_html(data2):
        out_path = os.path.join(HERE, "EmotifyAIDevBriefv3.docx")
        with open(out_path, "wb") as f:
            f.write(data2)
        print(f"SUCCESS: looks like a real binary file. Saved to {out_path}")
    else:
        print("FAILED: response looks like an HTML page (login/register), not the real file.")
        snippet = data2[:300].decode("utf-8", errors="ignore")
        print("Response snippet:")
        print(snippet)


if __name__ == "__main__":
    main()
