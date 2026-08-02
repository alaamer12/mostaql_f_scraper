import asyncio
import logging
import time
from typing import List, Optional, Set, Dict
from dataclasses import asdict
from tqdm import tqdm
import aiohttp

from ..models import Freelancer, ProfileDetails, ScrapeConfig
from .network import NetworkService
from .parser import ParsingService
from .storage import StorageService
from .exporter import ExporterService
from ..utils.combos import ComboManager
from ..utils.reporting import (
    PhaseMetrics,
    MetricsRegistry,
    print_scraper_header,
    print_phase_stats,
    print_completion_paths,
)

log = logging.getLogger(__name__)


class ScraperOrchestrator:
    """High-level orchestrator exposing four independently runnable phases:

    1. Discovery      - binary search filter combinations to find page counts.
    2. URL Extraction - scrape listing pages to collect unique freelancer URLs.
    3. Fetch          - download raw profile/portfolio HTML for later parsing.
    4. Parse          - parse cached raw HTML into structured ProfileDetails.

    Each phase can run standalone (its own CLI command), has its own
    ``PhaseMetrics`` instance, and exports its own results independently.
    A shared ``MetricsRegistry`` collects metrics from every phase executed
    in the current session so a combined report can be printed at the end.
    """

    def __init__(self, config: ScrapeConfig):
        self.config = config
        self.storage = StorageService()
        self.exporter = ExporterService(self.storage)
        self.parser = ParsingService(config)
        self.combo_manager = ComboManager(config.base_url)
        self.registry = MetricsRegistry()

    # ------------------------------------------------------------------
    # Phase 1: Discovery
    # ------------------------------------------------------------------
    async def run_discovery(self, use_continue: bool = True) -> Dict[str, int]:
        """Phase 1: binary search combos to discover their max page count.

        Persists a combo-label -> last_page mapping to the pagination cache
        so Phase 2 can be run independently, later, without repeating the
        binary search.
        """
        metrics = PhaseMetrics(phase_name="Discovery")
        start_time = time.monotonic()
        print_scraper_header("Auto (Binary Search)", self.config.dir_concurrency, f"{self.config.rate_limit_burst}/{self.config.rate_limit_period}s")
        log.info("Starting Discovery Phase...")

        combos = self.combo_manager.get_combinations()
        cache_path = self.config.resolve_path("pagination_cache")
        page_counts: Dict[str, int] = {}

        if use_continue:
            existing = self.storage.load_json(cache_path)
            if existing:
                page_counts.update(existing)
                log.info(f"Loaded {len(page_counts)} cached combo page-counts.")

        pending = [c for c in combos if self.combo_manager.get_label(c) not in page_counts]
        metrics.increment("skipped_resumed", len(combos) - len(pending))

        queue = asyncio.Queue()
        for combo in pending:
            queue.put_nowait(combo)

        sem = asyncio.Semaphore(self.config.dir_concurrency)
        pbar = tqdm(total=len(combos), initial=len(combos) - len(pending), desc="Discovering Combos")

        async with aiohttp.ClientSession() as session:
            workers = [
                asyncio.create_task(self._discovery_worker(i, session, queue, sem, page_counts, pbar, metrics))
                for i in range(self.config.dir_concurrency)
            ]
            await queue.join()
            for w in workers:
                w.cancel()

        self.storage.save_json(page_counts, cache_path)

        success = len([v for v in page_counts.values() if v > 0])
        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        print_phase_stats("Discovery", len(page_counts), success, len(page_counts) - success, metrics)

        log.info(f"Discovery complete. {len(page_counts)} combos processed, {success} with pages.")
        return page_counts

    async def _discovery_worker(self, worker_id, session, queue, sem, page_counts, pbar, metrics: PhaseMetrics):
        net = NetworkService(self.config, worker_id=worker_id, metrics=metrics)
        while True:
            combo = await queue.get()
            try:
                metrics.increment("combos_processed")
                last_page = await self._find_max_pages(session, combo, sem, net)
                if last_page > 0:
                    metrics.increment("pages_found", last_page)
                    if last_page > metrics.max_pages_seen:
                        metrics.max_pages_seen = last_page
                    if metrics.min_pages_seen == 0 or last_page < metrics.min_pages_seen:
                        metrics.min_pages_seen = last_page
                else:
                    metrics.increment("empty_combos")
                page_counts[self.combo_manager.get_label(combo)] = last_page
                pbar.update(1)
            except Exception as e:
                log.error(f"Discovery worker {worker_id} failed on combo {combo}: {e}")
            finally:
                queue.task_done()

    async def _find_max_pages(self, session, combo, sem, net) -> int:
        """Find max pages for a combo using binary search."""
        if not await self._check_page_exists(session, combo, 1, sem, net):
            return 0

        lo, hi = 1, self.config.binary_search_initial
        while await self._check_page_exists(session, combo, hi, sem, net):
            lo = hi
            hi *= 2
            if hi > 10000:
                break

        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if await self._check_page_exists(session, combo, mid, sem, net):
                lo = mid
            else:
                hi = mid
        return lo

    async def _check_page_exists(self, session, combo, page, sem, net) -> bool:
        url = self.combo_manager.get_url(combo, page)
        status, html = await net.get(session, url, sem)
        if status == 404 or not html:
            return False
        freelancers = self.parser.parse_directory(html)
        return len(freelancers) > 0

    # ------------------------------------------------------------------
    # Sample: quick smoke test (no checkpoints, no cache writes)
    # ------------------------------------------------------------------
    async def run_sample(self, limit: int = 2) -> bool:
        """Quick live smoke test that exercises the whole pipeline end-to-end
        on a tiny slice of real data, without touching any cache/checkpoint
        files, to confirm the scraper still works against the live site.

        Fetches a single listing page for the first filter combination,
        extracts up to ``limit`` freelancer URLs from it, then fetches and
        parses each one (main profile + portfolio).
        """
        metrics = PhaseMetrics(phase_name="Sample")
        start_time = time.monotonic()
        print_scraper_header("Sample", 1, f"{self.config.rate_limit_burst}/{self.config.rate_limit_period}s")
        log.info("Starting Sample smoke test...")

        combos = self.combo_manager.get_combinations()
        if not combos:
            log.error("Sample failed: no filter combinations configured.")
            return False
        combo = combos[0]

        sem = asyncio.Semaphore(1)
        async with aiohttp.ClientSession() as session:
            net = NetworkService(self.config, worker_id=0, metrics=metrics)

            listing_url = self.combo_manager.get_url(combo, 1)
            status, html = await net.get(session, listing_url, sem)
            if not html:
                metrics.increment("fetch_failed")
                self._finish_sample(metrics, start_time, success=False)
                return False

            freelancers = self.parser.parse_directory(html)
            metrics.increment("urls_discovered", len(freelancers))
            if not freelancers:
                self._finish_sample(metrics, start_time, success=False)
                return False

            sample_freelancers = freelancers[:limit]
            parsed_ok = 0
            for f in sample_freelancers:
                status, profile_html = await net.get(session, f.profile_url, sem)
                if not profile_html:
                    metrics.increment("fetch_failed")
                    continue
                metrics.increment("profiles_fetched")

                _, portfolio_html = await net.get(session, f.profile_url + "/portfolio", sem)
                if portfolio_html:
                    metrics.increment("portfolios_fetched")

                profile = self.parser.parse_profile(profile_html, f.profile_url, portfolio_html=portfolio_html)
                if profile and profile.name != "Unknown":
                    metrics.increment("parse_success")
                    parsed_ok += 1
                else:
                    metrics.increment("parse_failed")

            success = parsed_ok > 0
            self._finish_sample(metrics, start_time, success=success)
            return success

    def _finish_sample(self, metrics: PhaseMetrics, start_time: float, success: bool) -> None:
        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        total = max(metrics.urls_discovered, 1)
        print_phase_stats("Sample", total, metrics.parse_success, total - metrics.parse_success, metrics)
        if success:
            log.info("Sample smoke test passed: the pipeline works end-to-end.")
        else:
            log.error("Sample smoke test failed: check network/parsing.")

    # ------------------------------------------------------------------
    # Phase 2: URL Extraction
    # ------------------------------------------------------------------
    async def run_extraction(self, use_continue: bool = True) -> List[Freelancer]:
        """Phase 2: scrape listing pages (using the pagination cache from Phase 1)
        to collect unique freelancer name/URL records.
        """
        metrics = PhaseMetrics(phase_name="URL Extraction")
        start_time = time.monotonic()
        print_scraper_header("From Cache", self.config.dir_concurrency, f"{self.config.rate_limit_burst}/{self.config.rate_limit_period}s")
        log.info("Starting URL Extraction Phase...")

        page_counts = self.storage.load_json(self.config.resolve_path("pagination_cache"))
        if not page_counts:
            log.error("No pagination cache found. Run discovery first.")
            return []

        combos_by_label = {self.combo_manager.get_label(c): c for c in self.combo_manager.get_combinations()}
        all_freelancers: Dict[str, Freelancer] = {}

        if use_continue:
            existing = self.storage.load_json(self.config.resolve_path("output_json"))
            if existing:
                for item in existing:
                    f = Freelancer(**item)
                    all_freelancers[f.profile_url] = f
                log.info(f"Loaded {len(all_freelancers)} existing freelancers.")
                metrics.increment("skipped_resumed", len(all_freelancers))

        jobs = [(combos_by_label[label], pages) for label, pages in page_counts.items() if pages > 0 and label in combos_by_label]

        queue = asyncio.Queue()
        for combo, pages in jobs:
            queue.put_nowait((combo, pages))

        sem = asyncio.Semaphore(self.config.dir_concurrency)
        pbar = tqdm(total=len(jobs), desc="Extracting URLs")

        async with aiohttp.ClientSession() as session:
            workers = [
                asyncio.create_task(self._extraction_worker(i, session, queue, sem, all_freelancers, pbar, metrics))
                for i in range(self.config.dir_concurrency)
            ]
            await queue.join()
            for w in workers:
                w.cancel()

        result = list(all_freelancers.values())
        self.exporter.export(result, json_path=self.config.resolve_path("output_json"), csv_path=self.config.resolve_path("output_csv"))

        success = len([f for f in result if f.name != "Unknown"])
        metrics.unknown_names = len(result) - success
        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        print_phase_stats("URL Extraction", len(result), success, len(result) - success, metrics)
        print_completion_paths(self.config.resolve_path("output_json"), self.config.resolve_path("output_csv"), len(result))

        log.info(f"URL Extraction complete. Found {len(result)} unique freelancers.")
        return result

    async def _extraction_worker(self, worker_id, session, queue, sem, storage_dict, pbar, metrics: PhaseMetrics):
        net = NetworkService(self.config, worker_id=worker_id, metrics=metrics)
        while True:
            combo, last_page = await queue.get()
            try:
                for page in range(1, last_page + 1):
                    url = self.combo_manager.get_url(combo, page)
                    _, html = await net.get(session, url, sem)
                    metrics.increment("pages_scraped")
                    if html:
                        for f in self.parser.parse_directory(html):
                            if f.profile_url not in storage_dict:
                                storage_dict[f.profile_url] = f
                                metrics.increment("urls_discovered")
                                if f.name == "Unknown":
                                    metrics.increment("unknown_names")
                            else:
                                metrics.increment("duplicate_urls_skipped")
                pbar.update(1)
            except Exception as e:
                log.error(f"Extraction worker {worker_id} failed on combo {combo}: {e}")
            finally:
                queue.task_done()

    # ------------------------------------------------------------------
    # Phase 3: Fetch (raw HTML download)
    # ------------------------------------------------------------------
    async def run_fetch(self, limit: Optional[int] = None, use_continue: bool = True) -> int:
        """Phase 3: download raw profile + portfolio HTML and cache it to disk,
        without parsing. Allows Phase 4 (Parse) to be re-run independently,
        e.g. after improving the parser, without re-hitting the network.
        """
        metrics = PhaseMetrics(phase_name="Fetch")
        start_time = time.monotonic()
        print_scraper_header(limit or "All", self.config.profile_concurrency, f"{self.config.rate_limit_burst}/{self.config.rate_limit_period}s")
        log.info("Starting Fetch Phase...")

        freelancers_data = self.storage.load_json(self.config.resolve_path("output_json"))
        if not freelancers_data:
            log.error("No discovered freelancers found. Run extraction first.")
            return 0

        urls = [f["profile_url"] for f in freelancers_data]
        if limit:
            urls = urls[:limit]

        checkpoint_path = self.config.resolve_path("checkpoint_fetch_json")
        processed_urls: Set[str] = set()
        if use_continue:
            for rec in self.storage.load_jsonl(checkpoint_path):
                processed_urls.add(rec["profile_url"])
            log.info(f"Resuming fetch. {len(processed_urls)} already fetched.")

        remaining_urls = [u for u in urls if u not in processed_urls]
        metrics.increment("skipped_resumed", len(urls) - len(remaining_urls))
        if not remaining_urls:
            log.info("All profiles already fetched.")
            return len(processed_urls)

        sem = asyncio.Semaphore(self.config.profile_concurrency)
        pbar = tqdm(total=len(urls), initial=len(processed_urls), desc="Fetching Profiles")

        async with aiohttp.ClientSession() as session:
            queue = asyncio.Queue()
            for url in remaining_urls:
                await queue.put(url)

            workers = [
                asyncio.create_task(self._fetch_worker(i, session, queue, sem, pbar, checkpoint_path, metrics))
                for i in range(self.config.profile_concurrency)
            ]
            await queue.join()
            for w in workers:
                w.cancel()

        total_fetched = len(processed_urls) + metrics.profiles_fetched
        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        print_phase_stats("Fetch", len(urls), metrics.profiles_fetched, metrics.fetch_failed, metrics)
        print_completion_paths(checkpoint_path, "-", total_fetched)

        log.info(f"Fetch complete. Total cached raw pages: {total_fetched}")
        return total_fetched

    async def _fetch_worker(self, worker_id, session, queue, sem, pbar, checkpoint_path, metrics: PhaseMetrics):
        net = NetworkService(self.config, worker_id=worker_id, metrics=metrics)
        while True:
            url = await queue.get()
            try:
                status, html = await net.get(session, url, sem)
                p_html = None
                if html:
                    metrics.increment("bytes_downloaded", len(html.encode("utf-8", errors="ignore")))
                    portfolio_url = url + "/portfolio"
                    _, p_html = await net.get(session, portfolio_url, sem)
                    if p_html:
                        metrics.increment("portfolios_fetched")
                        metrics.increment("bytes_downloaded", len(p_html.encode("utf-8", errors="ignore")))
                    else:
                        metrics.increment("portfolio_fetch_failed")
                    metrics.increment("profiles_fetched")
                else:
                    metrics.increment("fetch_failed")

                self.storage.save_jsonl(
                    [{"profile_url": url, "html": html, "portfolio_html": p_html}],
                    checkpoint_path,
                )
                pbar.update(1)
            except Exception as e:
                log.error(f"Fetch worker {worker_id} failed on {url}: {e}")
                metrics.increment("fetch_failed")
            finally:
                queue.task_done()

    # ------------------------------------------------------------------
    # Phase 4: Parse
    # ------------------------------------------------------------------
    def run_parse(self, use_continue: bool = True) -> List[ProfileDetails]:
        """Phase 4: parse the raw HTML cached during Phase 3 into
        structured ProfileDetails records. Pure CPU-bound work, no network.
        """
        metrics = PhaseMetrics(phase_name="Parse")
        start_time = time.monotonic()
        log.info("Starting Parse Phase...")

        checkpoint_path = self.config.resolve_path("checkpoint_fetch_json")
        raw_records = self.storage.load_jsonl(checkpoint_path)
        if not raw_records:
            log.error("No raw HTML cache found. Run fetch first.")
            return []

        results: List[ProfileDetails] = []
        for rec in tqdm(raw_records, desc="Parsing Profiles"):
            metrics.increment("profiles_parsed")
            html = rec.get("html")
            if not html:
                metrics.increment("parse_failed")
                continue
            profile = self.parser.parse_profile(html, rec["profile_url"], portfolio_html=rec.get("portfolio_html"))
            if profile and profile.name != "Unknown":
                metrics.increment("parse_success")
                if profile.portfolio_count:
                    metrics.increment("portfolios_parsed")
                metrics.increment("fields_missing", self._count_missing_fields(profile))
                results.append(profile)
            else:
                metrics.increment("parse_failed")
                if profile:
                    results.append(profile)

        self.exporter.export(
            results,
            json_path=self.config.resolve_path("profiles_json"),
            csv_path=self.config.resolve_path("profiles_csv"),
        )

        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        print_phase_stats("Parse", metrics.profiles_parsed, metrics.parse_success, metrics.parse_failed, metrics)
        print_completion_paths(self.config.resolve_path("profiles_json"), self.config.resolve_path("profiles_csv"), len(results))

        log.info(f"Parse complete. {len(results)} profiles parsed successfully.")
        return results

    @staticmethod
    def _count_missing_fields(profile: ProfileDetails) -> int:
        """Count how many of the key profile fields came back empty/None,
        even though the parse as a whole was considered a success.
        """
        key_fields = (
            profile.title, profile.location, profile.completion_rate,
            profile.rehire_rate, profile.response_time, profile.last_seen,
            profile.member_since,
        )
        missing = sum(1 for v in key_fields if not v)
        if not profile.skills:
            missing += 1
        return missing

    # ------------------------------------------------------------------
    # Composite: Deep Scrape (Fetch + Parse), kept for convenience/back-compat
    # ------------------------------------------------------------------
    async def run_deep_scrape(self, limit: Optional[int] = None, use_continue: bool = True) -> List[ProfileDetails]:
        """Convenience wrapper chaining Phase 3 (Fetch) and Phase 4 (Parse)."""
        await self.run_fetch(limit=limit, use_continue=use_continue)
        return self.run_parse(use_continue=use_continue)

    def print_session_summary(self) -> None:
        """Print an aggregated report across every phase run in this session."""
        self.registry.print_aggregate()
