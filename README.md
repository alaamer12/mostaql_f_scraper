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

### Usage Guide
The system is now optimized for production deployment as a FastAPI web service, though it can still be run locally.

#### 1. Configuration
Configure the scraper using environment variables or a `.env` file. See `.env.example` for available options.
```bash
cp .env.example .env
# Edit .env with your desired settings
```

#### 2. Running the API Server
The primary entry point starts a FastAPI server:
```bash
python main.py
```
Or use `uvicorn` directly:
```bash
uvicorn src.main_api:app --host 0.0.0.0 --port 8000
```

#### 3. Web UI (one page per command)
Every phase that used to be a CLI command now has its own web page, styled with the
[MUI CSS](https://www.muicss.com/) library for fast iteration. Open `http://localhost:8000/`
for the guiding home page, which links to a page per command:

- `/scrape`, `/discovery`, `/extract`, `/fetch`, `/deep_scrape`, `/followup`, `/fixup`

Each command page provides:
- **Inputs** — a form with the relevant options (resume/continue, limit, deep, etc.), plus a
  drag & drop / file-explorer upload zone for commands that need an input file (`followup`, `fixup`).
- **Process** — a live progress bar (with room for sub-progress bars) driven by the same
  progress reporting the CLI used to print to the terminal.
- **Log** — the last 50 log lines, refreshed automatically.
- **Output** — the list of generated result files, available for download even before the
  run finishes.
- **Report** — a reactive panel that keeps polling and updating with the latest per-phase metrics.

#### 4. API Endpoints
The pages above are just a UI over the following JSON API, which you can still call directly:
- `GET /health`: Check service status.
- `POST /api/run/{command}`: Trigger a scraping operation in the background (`command` is one of
  `scrape`, `discovery`, `extract`, `fetch`, `deep_scrape`, `followup`, `fixup`).
  - Body (JSON): `resume` (bool), `deep` (bool), `limit` (int), `input_file` (path, for followup/fixup).
- `POST /api/upload`: Upload a `.json` input file (used by the followup/fixup pages); returns the saved path.
- `GET /api/stats`: View current progress, task status, and per-phase metrics.
- `GET /api/logs`: Last 50 log lines.
- `GET /api/results`: List available JSON/CSV result files.
- `GET /results/download/{filename}`: Download a specific result file.
- `POST /scrape`, `GET /stats`, `GET /results`: Kept as backward-compatible aliases.

#### 5. Analytics Dashboard
The interactive dashboard is still available and can be launched separately:
```bash
# Launch via the API stats or by running the dashboard script
python -m src.dashboard
```

### Deployment (Railway)
This project is ready for 24/7 operation on Railway:
1. Connect your GitHub repository to Railway.
2. Add a **Persistent Volume** and mount it if you want to keep results across redeploys.
3. Set environment variables (e.g., `MOSTAQL_MAX_PAGES`) in the Railway dashboard.
4. Railway will automatically detect the `requirements.txt` and start the server using the default `python main.py`.

### Project Architecture
The project is organized into a clean `src/` directory:
- `templates/`: Jinja2 + MUI CSS templates for the web UI (`base.html`, `index.html`, `command.html`).
- `outsourcing/`: Runtime directory; each run/upload gets its own `outsourcing/<uuid>/{uploads,downloads,logs}/` sandbox (uploaded files, generated output files, and that run's log file all share the same uuid).
- `src/main_api.py`: FastAPI server, per-command page routes, JSON API, and background task management.
- `src/services/`:
  - `orchestrator.py`: Pipeline coordination and worker management for all 4 phases.
  - `network.py`: Advanced rate limiting and HTTP request handling.
  - `parser.py`: HTML processing for profiles and directory pages.
  - `storage.py`: Raw JSON/JSONL/CSV file persistence and checkpoint management.
  - `exporter.py`: Format-aware exporting of any phase's results to JSON/CSV.
- `src/models.py`: Pydantic `BaseSettings` for configuration and typed `dataclasses` for domain entities.
- `src/utils/`:
  - `reporting.py`: `PhaseMetrics` and `MetricsRegistry` for metrics tracking.
  - `formatting.py`, `combos.py`, `logging_utils.py`: Time-based paths, filter combinations, logging setup.
- `main.py`: Entry point that launches the FastAPI server.

### Help
The API provides self-documenting Swagger UI at `/docs` (e.g., `http://localhost:8000/docs`) where you can test all endpoints directly.
