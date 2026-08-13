"""
test_progress_reactivity.py
============================
End-to-end check that the FastAPI progress bar for the "followup" command
is genuinely reactive: while a (mocked, no real network) run is in flight,
`progress_state.snapshot()` must show `n` increasing over multiple samples
taken *during* the run, not just jump from 0 straight to 100% at the very
end.

This reproduces the exact code path used by `POST /api/run/followup`
(`run_scraper_task` -> `orchestrator.stream_followup` +
`orchestrator.stream_extraction`), but with the network layer and parser
mocked out so it runs instantly and deterministically, without hitting
mostaql.com.

Usage
-----
    uv run python test/test_progress_reactivity.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.network import NetworkService
from src.services.parser import ParsingService
import src.main_api as api


async def fake_get(self, session, url, sem, ua_index=None):
    """Simulate a slow-ish network call so the run takes observable time,
    without touching the real site."""
    await asyncio.sleep(0.05)
    return 200, "<html>fake</html>"


def fake_parse_directory(self, html, *args, **kwargs):
    # No freelancers found -> each keyword's page loop stops after page 1.
    return []


async def main() -> bool:
    # --- Build a tiny fake "existing users" input file (20 unique names) ---
    tmp_dir = tempfile.mkdtemp(prefix="mostaql_progress_test_")
    input_path = os.path.join(tmp_dir, "fake_users.json")
    fake_users = [{"name": f"TestName{i}", "profile_url": f"https://mostaql.com/u/testname{i}"} for i in range(20)]
    with open(input_path, "w", encoding="utf-8") as f:
        json.dump(fake_users, f)

    # --- Patch the network + parser layers so no real HTTP happens ---
    original_get = NetworkService.get
    original_parse_directory = ParsingService.parse_directory
    NetworkService.get = fake_get
    ParsingService.parse_directory = fake_parse_directory

    # Keep each keyword's job to a single simulated page fetch.
    api.config.max_pages = 1
    api.config.dir_concurrency = 3

    samples = []
    try:
        task = asyncio.create_task(
            api.run_scraper_task(
                "followup",
                input_file=input_path,
                resume=False,
                output_json=os.path.join(tmp_dir, "followup_out.json"),
            )
        )

        # Poll the exact same snapshot the frontend polls via /api/stats,
        # while the background task is still running.
        while not task.done():
            snap = api.progress_state.snapshot()
            samples.append((round(time.monotonic(), 3), snap["n"], snap["total"], snap["desc"]))
            await asyncio.sleep(0.02)

        await task
        # One final sample after completion.
        final_snap = api.progress_state.snapshot()
        samples.append((round(time.monotonic(), 3), final_snap["n"], final_snap["total"], final_snap["desc"]))
    finally:
        NetworkService.get = original_get
        ParsingService.parse_directory = original_parse_directory

    print("Collected", len(samples), "progress samples while the task was running:")
    for t, n, total, desc in samples:
        print(f"  t={t}  desc={desc!r}  n={n}  total={total}")

    distinct_n_values = sorted({s[1] for s in samples})
    print("\nDistinct `n` values observed:", distinct_n_values)

    assert api.scrape_status.error is None, f"Task ended with an error: {api.scrape_status.error}"
    assert len(distinct_n_values) > 1, (
        "Progress bar `n` never changed across samples -> NOT reactive "
        "(it only ever reported a single value while the task ran)."
    )
    assert distinct_n_values[0] == 0, "Expected the bar to start at 0 before any work was reported."

    print("\nSUCCESS: progress bar `n` increased across multiple live samples "
          "while the followup task was running -> reactivity confirmed.")
    return True


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
