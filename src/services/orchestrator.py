import asyncio
import logging
import time
from typing import Any, Callable, List, Optional, Set, Dict
from dataclasses import asdict
from tqdm.auto import tqdm
import aiohttp

from ..models import (
    Freelancer,
    PageCountItem,
    KeywordItem,
    ProfileDetails,
    RawProfileRecord,
    ScrapeConfig,
)
from ..pipeline.channel import Channel, NullChannel
from .network import NetworkService
from .parser import ParsingService
from .storage import StorageService
from .exporter import ExporterService
from ..utils.combos import ComboManager
from ..utils.reporting import (
    WORKERS,
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
        # Replaced by the pipelined runner so stages report to the live
        # multi-bar display instead of creating their own tqdm bars.
        self.progress_factory: Optional[Callable[[str, Optional[int], int], Any]] = None

    # ------------------------------------------------------------------
    # Progress helpers (tqdm by default, live display in pipelined mode)
    # ------------------------------------------------------------------
    def _make_bar(self, desc: str, total: Optional[int] = None, initial: int = 0):
        if self.progress_factory is not None:
            return self.progress_factory(desc, total, initial)
        return tqdm(total=total, initial=initial, desc=desc)

    @staticmethod
    def _bump_total(bar, delta: int = 1) -> None:
        """Grow a bar's total as more work is discovered upstream."""
        bar.total = (bar.total or 0) + delta
        # Do not call refresh() here to avoid excessive line printing in some envs

    def _header(self, max_pages, concurrency: int, rate: str) -> None:
        """Print the banner, unless a live dashboard already owns the screen."""
        if self.progress_factory is not None:
            return
        print_scraper_header(max_pages, concurrency, rate)

    @staticmethod
    def _close_bar(bar) -> None:
        close = getattr(bar, "close", None)
        if close is not None:
            close()

    # ------------------------------------------------------------------
    # Phase 0: Followup
    # ------------------------------------------------------------------
    async def run_followup(self, input_path: Optional[str] = None, use_continue: bool = True) -> List[KeywordItem]:
        """Phase 0: extract unique first names from existing data."""
        return await self.stream_followup(NullChannel(), input_path=input_path, use_continue=use_continue)

    async def stream_followup(self, out: Channel, input_path: Optional[str] = None, use_continue: bool = True) -> List[KeywordItem]:
        """Streaming form of Phase 0.

        Reads unique first words from the input JSON file and streams
        them as KeywordItem milestones for downstream extraction.
        """
        metrics = PhaseMetrics(phase_name="Followup")
        start_time = time.monotonic()
        actual_input_path = input_path or self.config.resolve_path("followup_input")
        log.info(f"Starting Followup Phase from {actual_input_path}...")

        try:
            data = self.storage.load_json(actual_input_path)
        except Exception as e:
            log.error(f"Failed to load followup input file {actual_input_path}: {e}")
            await out.close()
            return []

        if not data:
            log.warning(f"Followup input file {actual_input_path} is empty or not found.")
            await out.close()
            return []

        unique_names = set()
        for item in data:
            name = item.get("name")
            if name and isinstance(name, str):
                first_word = name.strip().split()[0]
                if first_word:
                    unique_names.add(first_word)

        log.info(f"Extracted {len(unique_names)} unique names for followup.")
        metrics.increment("urls_discovered", len(unique_names)) # Using urls_discovered as a proxy for keywords

        results = []
        pbar = self._make_bar("Preparing Followup", total=len(unique_names))
        
        # We wrap each name in a KeywordItem. 
        # The 'combo' here is a virtual one that 'extract' knows how to use.
        for name in sorted(list(unique_names)):
            item = KeywordItem(keyword=name, combo={"dim": "followup", "value": name, "params": {}})
            results.append(item)
            await out.send(item)
            pbar.update(1)

        self._close_bar(pbar)
        await out.close()

        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        print_phase_stats("Followup", len(unique_names), len(unique_names), 0, metrics)
        
        return results

    # ------------------------------------------------------------------
    # Phase 1: Discovery
    # ------------------------------------------------------------------
    async def run_discovery(self, use_continue: bool = True) -> Dict[str, int]:
        """Phase 1: binary search combos to discover their max page count.

        Persists a combo-label -> last_page mapping to the pagination cache
        so Phase 2 can be run independently, later, without repeating the
        binary search.
        """
        return await self.stream_discovery(NullChannel(), use_continue=use_continue)

    async def stream_discovery(self, out: Channel, use_continue: bool = True) -> Dict[str, int]:
        """Streaming form of Phase 1.

        Identical to :meth:`run_discovery` except that every solved
        combination is emitted on ``out`` as a ``PageCountItem`` the moment
        its binary search finishes, so a downstream ``extract`` stage can
        start scraping listing pages long before discovery completes.
        """
        metrics = PhaseMetrics(phase_name="Discovery")
        start_time = time.monotonic()
        self._header("Auto (Binary Search)", self.config.dir_concurrency, f"{self.config.discovery_rate_burst}/{self.config.discovery_rate_period}s per worker")
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

        # Cached combos are still milestones for the downstream stage.
        combos_by_label = {self.combo_manager.get_label(c): c for c in combos}
        for label, pages in page_counts.items():
            if pages > 0 and label in combos_by_label:
                await out.send(PageCountItem(label=label, combo=combos_by_label[label], last_page=pages))

        queue = asyncio.Queue()
        for combo in pending:
            queue.put_nowait(combo)

        pbar = self._make_bar("Discovering Combos", total=len(combos), initial=len(combos) - len(pending))

        try:
            # One session (and thus one cookie jar / connection pool) per
            # worker, exactly like the pre-refactor brute-force script: a
            # worker stalled in a cooldown then never affects the others.
            workers = [
                asyncio.create_task(
                    self._discovery_worker(i, queue, page_counts, pbar, metrics, out, cache_path)
                )
                for i in range(self.config.dir_concurrency)
            ]
            await queue.join()
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        finally:
            self._close_bar(pbar)
            await out.close()

        self.storage.save_json(page_counts, cache_path)

        success = len([v for v in page_counts.values() if v > 0])
        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        print_phase_stats("Discovery", len(page_counts), success, len(page_counts) - success, metrics)

        log.info(f"Discovery complete. {len(page_counts)} combos processed, {success} with pages.")
        return page_counts

    async def _discovery_worker(self, worker_id, queue, page_counts, pbar, metrics: PhaseMetrics,
                                out: Optional[Channel] = None, cache_path: Optional[str] = None):
        net = NetworkService(
            self.config,
            worker_id=worker_id,
            metrics=metrics,
            rate_burst=self.config.discovery_rate_burst,
            rate_period=self.config.discovery_rate_period,
        )
        # Each worker gets its own slot, so no worker can be starved by a
        # sibling that is sleeping off a 429.
        sem = asyncio.Semaphore(1)
        async with aiohttp.ClientSession() as session:
            await self._prime_cookies(session, sem, net, worker_id)
            await self._discovery_loop(worker_id, session, queue, sem, net, page_counts, pbar, metrics, out, cache_path)

    async def _prime_cookies(self, session, sem, net, worker_id) -> None:
        """Warm up a worker session so the first real request carries cookies."""
        try:
            status, _ = await net.get(session, self.config.base_url, sem, ua_index=worker_id)
            if status != 200:
                log.warning(f"[W{worker_id}] cookie priming failed (HTTP {status})")
        except Exception as e:
            log.warning(f"[W{worker_id}] cookie priming failed: {e}")

    async def _discovery_loop(self, worker_id, session, queue, sem, net, page_counts, pbar, metrics: PhaseMetrics,
                              out: Optional[Channel] = None, cache_path: Optional[str] = None):
        while True:
            net.state.set("waiting for combo", "")
            combo = await queue.get()
            try:
                metrics.increment("combos_processed")
                net.state.set("searching", self.combo_manager.get_label(combo))
                last_page = await self._find_max_pages(session, combo, sem, net, worker_id)
                if last_page > 0:
                    metrics.increment("pages_found", last_page)
                    if last_page > metrics.max_pages_seen:
                        metrics.max_pages_seen = last_page
                    if metrics.min_pages_seen == 0 or last_page < metrics.min_pages_seen:
                        metrics.min_pages_seen = last_page
                else:
                    metrics.increment("empty_combos")
                label = self.combo_manager.get_label(combo)
                page_counts[label] = last_page
                pbar.update(1)
                if out is not None and last_page > 0:
                    await out.send(PageCountItem(label=label, combo=combo, last_page=last_page))
                if cache_path and metrics.combos_processed % self.config.checkpoint_flush_every == 0:
                    await self.storage.asave_json(dict(page_counts), cache_path)
            except Exception as e:
                log.error(f"Discovery worker {worker_id} failed on combo {combo}: {e}")
            finally:
                queue.task_done()

    async def _find_max_pages(self, session, combo, sem, net, ua_index: int = 0) -> int:
        """Find max pages for a combo using binary search."""
        if not await self._check_page_exists(session, combo, 1, sem, net, ua_index):
            return 0

        lo, hi = 1, self.config.binary_search_initial
        while await self._check_page_exists(session, combo, hi, sem, net, ua_index):
            lo = hi
            hi *= 2
            if hi > 10000:
                break

        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if await self._check_page_exists(session, combo, mid, sem, net, ua_index):
                lo = mid
            else:
                hi = mid
        return lo

    async def _check_page_exists(self, session, combo, page, sem, net, ua_index: int = 0) -> bool:
        url = self.combo_manager.get_url(combo, page)
        status, html = await net.get(session, url, sem, ua_index=ua_index)
        if status == 404 or not html:
            return False
        freelancers = self.parser.parse_directory(html)
        return len(freelancers) > 0

    async def _followup_worker(self, worker_id, session, queue, sem, storage_dict, pbar, metrics: PhaseMetrics,
                               out: Optional[Channel] = None, target_output_json: Optional[str] = None,
                               target_output_csv: Optional[str] = None, suppression_urls: Optional[Set[str]] = None):
        """Specialized worker for keyword-based extraction (followup)."""
        net = NetworkService(self.config, worker_id=worker_id, metrics=metrics)
        processed_items = 0
        while True:
            net.state.set("waiting for keyword", "")
            keyword_item = await queue.get()
            try:
                keyword = keyword_item.keyword
                combo = keyword_item.combo
                net.state.set("searching", keyword)
                
                # For followup, we don't know the last_page, so we scrape until 
                # we hit an empty page or reach max_pages.
                page = 1
                while True:
                    if self.config.max_pages != -1 and page > self.config.max_pages:
                        break
                        
                    virtual_combo = dict(combo)
                    virtual_combo["keyword"] = keyword
                    url = self.combo_manager.get_url(virtual_combo, page)
                    
                    _, html = await net.get(session, url, sem)
                    metrics.increment("pages_scraped")
                    
                    if not html:
                        break
                        
                    freelancers = self.parser.parse_directory(html)
                    if not freelancers:
                        break
                        
                    for f in freelancers:
                        if suppression_urls and f.profile_url in suppression_urls:
                            metrics.increment("duplicate_urls_skipped")
                            continue

                        if f.profile_url not in storage_dict:
                            storage_dict[f.profile_url] = f
                            metrics.increment("urls_discovered")
                            if f.name == "Unknown":
                                metrics.increment("unknown_names")
                            if out is not None:
                                await out.send(f)
                        else:
                            metrics.increment("duplicate_urls_skipped")
                    
                    page += 1
                    
                pbar.update(1)
                processed_items += 1
                if target_output_json and processed_items % self.config.checkpoint_flush_every == 0:
                    records = list(storage_dict.values())
                    self.exporter.export(records, json_path=target_output_json, csv_path=target_output_csv)
            except Exception as e:
                log.error(f"Followup worker {worker_id} failed on {keyword_item}: {e}")
            finally:
                net.state.set("idle", "")
                queue.task_done()

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
    async def run_extraction(self, use_continue: bool = True,
                             output_json: Optional[str] = None,
                             output_csv: Optional[str] = None) -> List[Freelancer]:
        """Phase 2: scrape listing pages (using the pagination cache from Phase 1)
        to collect unique freelancer name/URL records.
        """
        return await self.stream_extraction(None, NullChannel(), use_continue=use_continue,
                                            output_json=output_json, output_csv=output_csv)

    async def stream_extraction(self, inp: Optional[Channel], out: Channel,
                                use_continue: bool = True,
                                output_json: Optional[str] = None,
                                output_csv: Optional[str] = None) -> List[Freelancer]:
        """Streaming form of Phase 2.

        With ``inp is None`` the stage seeds itself from the pagination
        cache exactly like :meth:`run_extraction`; otherwise it consumes
        ``PageCountItem`` or ``KeywordItem`` milestones as the upstream stage
        produces them. Every newly discovered unique freelancer is
        forwarded on ``out`` immediately.
        """
        metrics = PhaseMetrics(phase_name="URL Extraction")
        start_time = time.monotonic()
        
        # Detect mode based on input type if streaming
        mode_desc = "From Cache"
        if inp is not None:
            mode_desc = "Streaming"
            
        self._header(mode_desc, self.config.dir_concurrency, f"{self.config.rate_limit_burst}/{self.config.rate_limit_period}s")
        log.info(f"Starting URL Extraction Phase ({mode_desc})...")

        is_followup_channel = inp is not None and "followup" in getattr(inp, "name", "")
        default_json = self.config.resolve_path("followup_output_json") if is_followup_channel else self.config.resolve_path("output_json")
        default_csv = self.config.resolve_path("followup_output_csv") if is_followup_channel else self.config.resolve_path("output_csv")

        target_output_json = output_json or default_json
        target_output_csv = output_csv or default_csv

        combos_by_label = {self.combo_manager.get_label(c): c for c in self.combo_manager.get_combinations()}
        page_counts: Dict[str, int] = {}
        if inp is None:
            page_counts = self.storage.load_json(self.config.resolve_path("pagination_cache")) or {}
            if not page_counts:
                log.error("No pagination cache found. Run discovery first.")
                await out.close()
                return []

        all_freelancers: Dict[str, Freelancer] = {}
        suppression_urls: Set[str] = set()

        if is_followup_channel:
            # In followup mode, we want to skip users already in the input file
            input_path = self.config.resolve_path("followup_input")
            log.info(f"Loading suppression list from {input_path}...")
            input_data = self.storage.load_json(input_path)
            if input_data:
                for item in input_data:
                    url = item.get("profile_url")
                    if url:
                        suppression_urls.add(url)
                log.info(f"Loaded {len(suppression_urls)} URLs to skip in followup.")

        if use_continue:
            existing = self.storage.load_json(target_output_json)
            if existing:
                for item in existing:
                    f = Freelancer(**item)
                    all_freelancers[f.profile_url] = f
                log.info(f"Loaded {len(all_freelancers)} existing freelancers from {target_output_json}.")
                metrics.increment("skipped_resumed", len(all_freelancers))
                # Already-known freelancers are milestones for the downstream
                # stage too; it de-duplicates against its own checkpoint.
                for f in list(all_freelancers.values()):
                    await out.send(f)

        jobs = [(combos_by_label[label], pages) for label, pages in page_counts.items() if pages > 0 and label in combos_by_label]

        queue = asyncio.Queue()
        for combo, pages in jobs:
            queue.put_nowait((combo, pages))

        sem = asyncio.Semaphore(self.config.dir_concurrency)
        pbar = self._make_bar("Extracting URLs", total=len(jobs))

        try:
            async with aiohttp.ClientSession() as session:
                workers = []
                
                # Check if we are in followup mode (KeywordItem)
                is_followup_mode = False
                
                if inp is not None:
                    # We need to wait for the first item to know the worker type, 
                    # OR we can peek if the channel allows. 
                    # Since we don't know yet, we'll start workers lazily or 
                    # use a generic worker that handles both.
                    # Given the differences, let's wait for the first item.
                    
                    async for item in inp:
                        if isinstance(item, PageCountItem):
                            if not workers:
                                workers = [
                                    asyncio.create_task(self._extraction_worker(i, session, queue, sem, all_freelancers, pbar, metrics, out, target_output_json=target_output_json, target_output_csv=target_output_csv, suppression_urls=suppression_urls))
                                    for i in range(self.config.dir_concurrency)
                                ]
                            if item.last_page <= 0:
                                continue
                            self._bump_total(pbar)
                            await queue.put((item.combo, item.last_page))
                        elif isinstance(item, KeywordItem):
                            is_followup_mode = True
                            if not workers:
                                workers = [
                                    asyncio.create_task(self._followup_worker(i, session, queue, sem, all_freelancers, pbar, metrics, out, target_output_json=target_output_json, target_output_csv=target_output_csv, suppression_urls=suppression_urls))
                                    for i in range(self.config.dir_concurrency)
                                ]
                            self._bump_total(pbar)
                            await queue.put(item)
                else:
                    # Default cache-based discovery extraction
                    workers = [
                        asyncio.create_task(
                            self._extraction_worker(i, session, queue, sem, all_freelancers, pbar, metrics, out, target_output_json=target_output_json, target_output_csv=target_output_csv, suppression_urls=suppression_urls)
                        )
                        for i in range(self.config.dir_concurrency)
                    ]
                
                await queue.join()
                for w in workers:
                    w.cancel()
        finally:
            self._close_bar(pbar)
            await out.close()

        result = list(all_freelancers.values())
        if output_json is None and is_followup_mode:
            target_output_json = self.config.resolve_path("followup_output_json")
            target_output_csv = self.config.resolve_path("followup_output_csv")

        self.exporter.export(result, json_path=target_output_json, csv_path=target_output_csv)

        success = len([f for f in result if f.name != "Unknown"])
        metrics.unknown_names = len(result) - success
        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        print_phase_stats("URL Extraction", len(result), success, len(result) - success, metrics)
        print_completion_paths(target_output_json, target_output_csv, len(result))

        log.info(f"URL Extraction complete. Found {len(result)} unique freelancers.")
        return result

    async def _extraction_worker(self, worker_id, session, queue, sem, storage_dict, pbar, metrics: PhaseMetrics,
                                 out: Optional[Channel] = None, target_output_json: Optional[str] = None,
                                 target_output_csv: Optional[str] = None, suppression_urls: Optional[Set[str]] = None):
        net = NetworkService(self.config, worker_id=worker_id, metrics=metrics)
        processed_items = 0
        while True:
            net.state.set("waiting for combo", "")
            combo, last_page = await queue.get()
            try:
                net.state.set("paging", self.combo_manager.get_label(combo))
                for page in range(1, last_page + 1):
                    url = self.combo_manager.get_url(combo, page)
                    _, html = await net.get(session, url, sem)
                    metrics.increment("pages_scraped")
                    if html:
                        for f in self.parser.parse_directory(html):
                            if suppression_urls and f.profile_url in suppression_urls:
                                metrics.increment("duplicate_urls_skipped")
                                continue

                            if f.profile_url not in storage_dict:
                                storage_dict[f.profile_url] = f
                                metrics.increment("urls_discovered")
                                if f.name == "Unknown":
                                    metrics.increment("unknown_names")
                                if out is not None:
                                    await out.send(f)
                            else:
                                metrics.increment("duplicate_urls_skipped")
                pbar.update(1)
                processed_items += 1
                if target_output_json and processed_items % self.config.checkpoint_flush_every == 0:
                    records = list(storage_dict.values())
                    self.exporter.export(records, json_path=target_output_json, csv_path=target_output_csv)
            except Exception as e:
                log.error(f"Extraction worker {worker_id} failed on combo {combo}: {e}")
            finally:
                net.state.set("idle", "")
                queue.task_done()

    # ------------------------------------------------------------------
    # Phase 3: Fetch (raw HTML download)
    # ------------------------------------------------------------------
    async def run_fetch(self, limit: Optional[int] = None, use_continue: bool = True) -> int:
        """Phase 3: download raw profile + portfolio HTML and cache it to disk,
        without parsing. Allows Phase 4 (Parse) to be re-run independently,
        e.g. after improving the parser, without re-hitting the network.
        """
        return await self.stream_fetch(None, NullChannel(), limit=limit, use_continue=use_continue)

    async def stream_fetch(self, inp: Optional[Channel], out: Channel, limit: Optional[int] = None,
                           use_continue: bool = True) -> int:
        """Streaming form of Phase 3.

        With ``inp is None`` the URL list is seeded from the extraction
        output as in :meth:`run_fetch`; otherwise ``Freelancer`` milestones
        are consumed as the upstream extract stage finds them. Each
        downloaded profile is appended to the fetch checkpoint and
        forwarded on ``out`` as a ``RawProfileRecord``.
        """
        metrics = PhaseMetrics(phase_name="Fetch")
        start_time = time.monotonic()
        self._header(limit or "All", self.config.profile_concurrency, f"{self.config.rate_limit_burst}/{self.config.rate_limit_period}s")
        log.info("Starting Fetch Phase...")

        urls: List[str] = []
        if inp is None:
            freelancers_data = self.storage.load_json(self.config.resolve_path("output_json"))
            if not freelancers_data:
                log.error("No discovered freelancers found. Run extraction first.")
                await out.close()
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
        if inp is None and not remaining_urls:
            log.info("All profiles already fetched.")
            await out.close()
            return len(processed_urls)

        sem = asyncio.Semaphore(self.config.profile_concurrency)
        pbar = self._make_bar("Fetching Profiles", total=len(urls), initial=len(processed_urls))

        try:
            async with aiohttp.ClientSession() as session:
                queue = asyncio.Queue()
                for url in remaining_urls:
                    await queue.put(url)

                workers = [
                    asyncio.create_task(
                        self._fetch_worker(i, session, queue, sem, pbar, checkpoint_path, metrics, out)
                    )
                    for i in range(self.config.profile_concurrency)
                ]
                if inp is not None:
                    seen: Set[str] = set(processed_urls)
                    queued = 0
                    async for freelancer in inp:
                        url = freelancer.profile_url
                        if url in seen:
                            metrics.increment("skipped_resumed")
                            continue
                        if limit is not None and queued >= limit:
                            continue
                        seen.add(url)
                        queued += 1
                        self._bump_total(pbar)
                        await queue.put(url)
                await queue.join()
                for w in workers:
                    w.cancel()
        finally:
            self._close_bar(pbar)
            await out.close()

        total_fetched = len(processed_urls) + metrics.profiles_fetched
        metrics.duration_seconds = time.monotonic() - start_time
        self.registry.register(metrics)
        print_phase_stats("Fetch", max(len(urls), metrics.profiles_fetched + metrics.fetch_failed),
                          metrics.profiles_fetched, metrics.fetch_failed, metrics)
        print_completion_paths(checkpoint_path, "-", total_fetched)

        log.info(f"Fetch complete. Total cached raw pages: {total_fetched}")
        return total_fetched

    async def _fetch_worker(self, worker_id, session, queue, sem, pbar, checkpoint_path, metrics: PhaseMetrics,
                            out: Optional[Channel] = None):
        net = NetworkService(self.config, worker_id=worker_id, metrics=metrics)
        while True:
            net.state.set("waiting for url", "")
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

                await self.storage.asave_jsonl(
                    [{"profile_url": url, "html": html, "portfolio_html": p_html}],
                    checkpoint_path,
                )
                pbar.update(1)
                if out is not None:
                    await out.send(RawProfileRecord(profile_url=url, html=html, portfolio_html=p_html))
            except Exception as e:
                log.error(f"Fetch worker {worker_id} failed on {url}: {e}")
                metrics.increment("fetch_failed")
            finally:
                net.state.set("idle", "")
                queue.task_done()

    # ------------------------------------------------------------------
    # Phase 4: Parse
    # ------------------------------------------------------------------
    def run_parse(self, use_continue: bool = True) -> List[ProfileDetails]:
        """Phase 4: parse the raw HTML cached during Phase 3 into
        structured ProfileDetails records. Pure CPU-bound work, no network.
        """
        return asyncio.run(self.stream_parse(None, NullChannel(), use_continue=use_continue))

    async def stream_parse(self, inp: Optional[Channel], out: Channel,
                           use_continue: bool = True) -> List[ProfileDetails]:
        """Streaming form of Phase 4.

        With ``inp is None`` the raw records are seeded from the fetch
        checkpoint as in :meth:`run_parse`; otherwise ``RawProfileRecord``
        milestones are consumed as the upstream fetch stage downloads them.
        Parsing is CPU-bound, so it runs in a worker thread to keep the
        event loop responsive for the other stages.
        """
        metrics = PhaseMetrics(phase_name="Parse")
        start_time = time.monotonic()
        log.info("Starting Parse Phase...")

        checkpoint_path = self.config.resolve_path("checkpoint_fetch_json")
        seeded: List[RawProfileRecord] = []
        if inp is None:
            raw_records = self.storage.load_jsonl(checkpoint_path)
            if not raw_records:
                log.error("No raw HTML cache found. Run fetch first.")
                await out.close()
                return []
            seeded = [
                RawProfileRecord(
                    profile_url=rec["profile_url"],
                    html=rec.get("html"),
                    portfolio_html=rec.get("portfolio_html"),
                )
                for rec in raw_records
            ]

        results: List[ProfileDetails] = []
        pbar = self._make_bar("Parsing Profiles", total=len(seeded))
        state = WORKERS.get("Parse", 0)

        try:
            for record in seeded:
                state.set("parsing", record.profile_url)
                state.requests += 1
                await self._parse_record(record, results, metrics, out)
                pbar.update(1)

            if inp is not None:
                state.set("waiting for html", "")
                async for record in inp:
                    state.set("parsing", record.profile_url)
                    state.requests += 1
                    self._bump_total(pbar)
                    await self._parse_record(record, results, metrics, out)
                    pbar.update(1)
                    state.set("waiting for html", "")
        finally:
            state.set("idle", "")
            self._close_bar(pbar)
            await out.close()

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

    async def _parse_record(self, record: RawProfileRecord, results: List[ProfileDetails],
                            metrics: PhaseMetrics, out: Optional[Channel] = None) -> None:
        """Parse one raw record off the event loop and record its outcome."""
        metrics.increment("profiles_parsed")
        if not record.html:
            metrics.increment("parse_failed")
            return
        profile = await asyncio.to_thread(
            self.parser.parse_profile, record.html, record.profile_url, record.portfolio_html
        )
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
        if out is not None and profile is not None:
            await out.send(profile)

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
        return await self.stream_parse(None, NullChannel(), use_continue=use_continue)

    def print_session_summary(self) -> None:
        """Print an aggregated report across every phase run in this session."""
        self.registry.print_aggregate()
