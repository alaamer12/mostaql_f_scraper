"""
test_pipeline.py
-----------------
Validates pipeline.py's combined structural-first / inference-fallback
parser: it must resolve correctly both on a real project page (where the
structural fast path applies) and on adversarial_mostaql.html (where every
structural selector is absent by design, forcing 100% reliance on the
inference.py fallback).

Run: python test_pipeline.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline
import attachment_downloader

HERE = os.path.dirname(os.path.abspath(__file__))

PASS = []
FAIL = []


def check(name, condition, detail=""):
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append((name, detail))
        print(f"  FAIL  {name}  -- {detail}")


def _read(filename):
    with open(os.path.join(HERE, filename), "r", encoding="utf-8") as f:
        return f.read()


def test_real_project_uses_structural_fast_path():
    html = _read("project_1.html")
    result = pipeline.parse_project(html)
    for field in ["project_status", "budget", "duration", "registration_date"]:
        entry = result["fields"][field]
        check(
            f"real_project[{field}]: resolved with non-empty value",
            entry["value"] is not None,
            detail=str(entry),
        )
        check(
            f"real_project[{field}]: resolved via structural fast path",
            entry["source"] == "structural",
            detail=str(entry),
        )
    # project_1.html's hire_rate is genuinely the "not yet calculated"
    # placeholder - the correct, nullable-by-design outcome is None, not
    # an error.
    hire_rate_entry = result["fields"]["hire_rate"]
    check(
        "real_project[hire_rate]: 'لم يحسب بعد' placeholder correctly nulled out",
        hire_rate_entry["value"] is None and hire_rate_entry["source"] == "structural",
        detail=str(hire_rate_entry),
    )
    check(
        "real_project: title resolved",
        bool(result["title"]),
        detail=str(result["title"]),
    )
    check(
        "real_project: no cross-validation mismatches on a clean, real page",
        result["mismatches"] == [],
        detail=str(result["mismatches"]),
    )


def test_adversarial_fixture_forces_inference_fallback():
    html = _read("adversarial_mostaql.html")
    result = pipeline.parse_project(html)
    fields = result["fields"]

    expectations = {
        "budget": lambda v: v and "500" in v and "800" in v,
        "duration": lambda v: v and "11" in v,
        "hire_rate": lambda v: v and "30" in v,
        "in_progress_count": lambda v: v and "3" in v,
        "ongoing_conversations": lambda v: v and "5" in v,
        "registration_date": lambda v: v and "2024" in v,
        "published_date": lambda v: v and "2024" in v,
    }
    for field, expect_fn in expectations.items():
        entry = fields[field]
        check(
            f"adversarial[{field}]: value matches expectation via inference fallback",
            expect_fn(entry["value"]),
            detail=str(entry),
        )
        check(
            f"adversarial[{field}]: resolved via inference (no matching structural selector exists)",
            entry["source"] == "inference",
            detail=str(entry),
        )

    for field, entry in fields.items():
        val = entry["value"] or ""
        check(
            f"adversarial[{field}]: does not resolve to decorative junk (999/42)",
            "999" not in val and "42" not in val,
            detail=val,
        )


def test_real_completed_project_with_review_no_deal_fields():
    """Real HTML captured from a live completed project that also has a
    client review + freelancer proposals sharing the 'text-wrapper-div'
    class. Two prior bugs surfaced from this exact fixture:
      1. started_since/deal_date/delivery_date labels are genuinely absent
         from the page even though status == 'مكتمل' - inference used to
         hallucinate noise (e.g. a validation-rule string) for them.
      2. The description lookup used to grab the FIRST '.text-wrapper-div'
         in document order, which is the client's review comment, not the
         real project description (there are 10 such divs on this page).
    """
    filepath = os.path.join(HERE, "project_completed_1.html")
    if not os.path.exists(filepath):
        print("  SKIP  project_completed_1.html not present (fetched from a live URL)")
        return

    html = _read("project_completed_1.html")
    result = pipeline.parse_project(html)
    fields = result["fields"]

    check(
        "real_completed[project_status]: resolved as completed",
        fields["project_status"]["value"] == pipeline.COMPLETED_STATUS_TEXT,
        detail=str(fields["project_status"]),
    )
    for f in ["budget", "duration", "registration_date", "open_projects_count",
              "in_progress_count", "ongoing_conversations"]:
        check(
            f"real_completed[{f}]: resolved with non-empty value via structural fast path",
            fields[f]["value"] is not None and fields[f]["source"] == "structural",
            detail=str(fields[f]),
        )

    for f in pipeline.COMPLETED_ONLY_FIELDS:
        check(
            f"real_completed[{f}]: correctly null (label absent) instead of hallucinated noise",
            fields[f]["value"] is None,
            detail=str(fields[f]),
        )

    # The client review text ("مشكور استاذ...") is only ~31 chars; the real
    # description is >1000 chars, so a length threshold alone is a reliable
    # signal that the review was NOT picked up in its place.
    check(
        "real_completed: description resolved from #projectDetailsTab, not the client review",
        result["description_length"] > 500,
        detail=str(result["description_length"]),
    )
    check(
        "real_completed: skills list captured from the meta panel",
        len(result["skills"]) > 0,
        detail=str(result["skills"]),
    )


def test_attachment_detected_on_completed_project_with_docx():
    """project_completed_1.html has one real attachment: the client's
    EmotifyAIDevBriefv3.docx brief, whose <a href> resolves to a
    '/register?...' link (anonymous fetch) rather than the real file."""
    filepath = os.path.join(HERE, "project_completed_1.html")
    if not os.path.exists(filepath):
        print("  SKIP  project_completed_1.html not present (fetched from a live URL)")
        return

    html = _read("project_completed_1.html")
    result = pipeline.parse_project(html)
    attachments = result["attachments"]

    check(
        "attachments: exactly one attachment detected on the completed project",
        len(attachments) == 1,
        detail=str(attachments),
    )
    if not attachments:
        return
    att = attachments[0]
    check(
        "attachments[0]: filename captured",
        att["filename"] == "EmotifyAIDevBriefv3.docx",
        detail=str(att),
    )
    check(
        "attachments[0]: extension captured as docx",
        att["extension"] == "docx",
        detail=str(att),
    )
    check(
        "attachments[0]: flagged as requiring auth (anonymous href is a /register redirect)",
        att["requires_auth"] is True,
        detail=str(att),
    )
    check(
        "attachments[0]: url nulled out (not a real download link) while raw_url is kept",
        att["url"] is None and att["raw_url"] and "/register" in att["raw_url"],
        detail=str(att),
    )
    check(
        "attachments[0]: size text captured",
        att["size_text"] == "(15.99KB)",
        detail=str(att),
    )


def test_no_attachments_on_regular_open_project():
    html = _read("project_1.html")
    result = pipeline.parse_project(html)
    check(
        "attachments: project_1.html has no file attachments",
        result["attachments"] == [],
        detail=str(result["attachments"]),
    )


def test_attachment_downloader_without_cookie_asks_for_manual_download():
    fake_attachment = {
        "filename": "EmotifyAIDevBriefv3.docx",
        "extension": "docx",
        "url": None,
        "raw_url": "https://mostaql.com/register?t=abc",
        "requires_auth": True,
        "size_text": "(15.99KB)",
    }
    resolved = attachment_downloader.resolve_attachment(fake_attachment, cookie_header=None)
    check(
        "downloader: no cookie configured -> manual_download_required status",
        resolved["status"] == attachment_downloader.STATUS_MANUAL_DOWNLOAD_REQUIRED,
        detail=str(resolved),
    )
    check(
        "downloader: manual-download message includes the raw link",
        resolved["message"] and fake_attachment["raw_url"] in resolved["message"],
        detail=str(resolved),
    )


def test_attachment_downloader_passes_through_public_links():
    fake_attachment = {
        "filename": "public.pdf",
        "extension": "pdf",
        "url": "https://mostaql.com/files/public.pdf",
        "raw_url": "https://mostaql.com/files/public.pdf",
        "requires_auth": False,
        "size_text": "(1.00KB)",
    }
    resolved = attachment_downloader.resolve_attachment(fake_attachment)
    check(
        "downloader: public (non-auth) attachment -> ready_url status, untouched",
        resolved["status"] == attachment_downloader.STATUS_READY_URL and resolved["url"] == fake_attachment["url"],
        detail=str(resolved),
    )


def test_attachment_downloader_with_real_cookie_file():
    cookie_file = os.path.join(HERE, "..", "cookies.txt")
    if not os.path.exists(cookie_file):
        print("  SKIP  cookies.txt not present")
        return

    fake_att = {
        "filename": "EmotifyAIDevBriefv3.docx",
        "extension": "docx",
        "url": "https://mostaql.com/file/4457705/6a10ae1689487/EmotifyAIDevBriefv3.docx",
        "raw_url": "https://mostaql.com/file/4457705/6a10ae1689487/EmotifyAIDevBriefv3.docx",
        "requires_auth": True,
        "size_text": "(15.99KB)",
    }
    old_env = os.environ.get("MOSTAQL_COOKIE_FILE")
    os.environ["MOSTAQL_COOKIE_FILE"] = cookie_file
    try:
        downloads_dir = os.path.join(HERE, "downloads_test")
        resolved = attachment_downloader.resolve_attachment(fake_att, dest_dir=downloads_dir)
        check(
            "downloader: real cookies.txt -> STATUS_DOWNLOADED",
            resolved["status"] == attachment_downloader.STATUS_DOWNLOADED,
            detail=str(resolved),
        )
        check(
            "downloader: local file actually written to disk and non-empty",
            resolved["local_path"] and os.path.exists(resolved["local_path"]) and os.path.getsize(resolved["local_path"]) > 10000,
            detail=f"path={resolved.get('local_path')}",
        )
    finally:
        if old_env is None:
            os.environ.pop("MOSTAQL_COOKIE_FILE", None)
        else:
            os.environ["MOSTAQL_COOKIE_FILE"] = old_env


def test_completed_only_fields_nullable_when_not_completed():
    html = _read("project_1.html")
    result = pipeline.parse_project(html)
    fields = result["fields"]
    status = fields["project_status"]["value"]
    if status != pipeline.COMPLETED_STATUS_TEXT:
        for f in pipeline.COMPLETED_ONLY_FIELDS:
            check(
                f"completed_only[{f}]: forced to None because status is '{status}'",
                fields[f]["value"] is None,
                detail=str(fields[f]),
            )
    else:
        print(f"  SKIP  project_1.html status is already '{status}' - nullability branch not exercised")


TESTS = [
    test_real_project_uses_structural_fast_path,
    test_adversarial_fixture_forces_inference_fallback,
    test_real_completed_project_with_review_no_deal_fields,
    test_attachment_detected_on_completed_project_with_docx,
    test_no_attachments_on_regular_open_project,
    test_attachment_downloader_without_cookie_asks_for_manual_download,
    test_attachment_downloader_passes_through_public_links,
    test_attachment_downloader_with_real_cookie_file,
    test_completed_only_fields_nullable_when_not_completed,
]


def main():
    print("Running pipeline.py combined structural+inference tests...")
    for t in TESTS:
        print(f"\n[{t.__name__}]")
        t()

    print(f"\n{'=' * 60}")
    print(f"TOTAL: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\nFailed checks:")
        for name, detail in FAIL:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    else:
        print("All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
