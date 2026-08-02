Mostaql "development" Professional Scraper
========================================

A high-performance, four-phase modular scraper for Mostaql freelancers in the "development" category. Every phase is fully independent — it can be run on its own, has its own metrics, and exports its own results.

### Features
- **Phase 1 (Discovery)**: Binary search through filter combinations to find each combo's page count, cached to `pagination_cache.json`.
- **Phase 2 (Extraction)**: Scrapes listing pages (using the discovery cache) to build a unique registry of freelancer name/URL records.
- **Phase 3 (Fetch)**: Downloads raw profile + portfolio HTML and caches it to disk, without parsing.
- **Phase 4 (Parse)**: Parses the cached raw HTML into structured profile details (stats, skills, portfolio counts) — pure CPU-bound work, no network calls.
- **Professional Core**:
  - Advanced rate limiter with persistent, escalating cooldowns.
  - Asynchronous worker-based architecture for high concurrency.
  - Modular service-oriented design following SOLID principles.
- **Resilience**: Each phase has its own checkpoint, so it can resume independently of the others.
- **Metrics & Reporting**: Each phase tracks its own `PhaseMetrics` (dataclass fields flagged `overlappable` or not). When multiple phases run in one session, the reporter prints each phase's report separately, then an aggregated summary that sums only the metrics safe to overlap (e.g. network requests) and lists phase-specific metrics (e.g. urls discovered, profiles parsed) per phase instead of blindly summing them.
- **Exporter System**: Any phase can independently export its results to JSON and/or CSV via a dedicated `ExporterService`.
- **Dynamic Paths**: Support for time-based placeholders in output filenames (e.g., `{TODAY}`, `{NOW}`).

### Quick Installation
```bash
pip install -r requirements.txt
```

### Usage Examples
The system is unified under a single CLI entry point: `main.py`.

#### View Examples Command
```bash
python main.py examples
```

#### Run Full Pipeline
Includes discovery and detailed profile scraping:
```bash
python main.py scrape --deep
```

#### Run Each Phase Independently
Each phase can be triggered on its own, with its own checkpoint and export:
```bash
python main.py discovery --new   # Phase 1
python main.py extract           # Phase 2
python main.py fetch --limit 100 # Phase 3
python main.py parse             # Phase 4
```

#### Resume Disrupted Scrape
Continue from where the system left off using checkpoints:
```bash
python main.py deep-scrape --continue
```

#### Testing with Limits
Process only the first 50 discovered profiles for validation:
```bash
python main.py deep-scrape --limit 50
```

#### Data Analytics & Dashboard
```bash
python main.py stats      # View data summary in terminal
python main.py dashboard  # Launch interactive web dashboard
```

### Project Architecture
The project is organized into a clean `src/` directory:
- `src/services/`:
  - `orchestrator.py`: Pipeline coordination and worker management for all 4 phases.
  - `network.py`: Advanced rate limiting and HTTP request handling.
  - `parser.py`: HTML processing for profiles and directory pages.
  - `storage.py`: Raw JSON/JSONL/CSV file persistence and checkpoint management.
  - `exporter.py`: Format-aware exporting of any phase's results to JSON/CSV.
- `src/models.py`: Typed `dataclasses` for domain entities.
- `src/utils/`:
  - `reporting.py`: `PhaseMetrics` dataclass (with `overlappable` field metadata) and `MetricsRegistry` for per-phase + aggregated reporting.
  - `formatting.py`, `combos.py`, `logging_utils.py`: Time-based paths, filter combinations, logging setup.
- `main.py`: Unified CLI entry point using `typer` and `rich`, exposing each phase as its own command.

### Help
For a full list of commands and flags:
```bash
python main.py --help
```
