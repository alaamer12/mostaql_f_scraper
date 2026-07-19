Mostaql "development" brute-force sweep
========================================

A professional, three-phase scraper for Mostaql freelancers in the "development" category.

### Features
- **Phase 1 (Discovery)**: Uses binary search to find all possible profile URLs across 300+ filter combinations.
- **Phase 2 (Extraction)**: Scrapes the discovered URLs to build a comprehensive list of freelancers.
- **Phase 3 (Deep Scrape)**: Performs detailed extraction of profile stats, skills, and portfolio data.
- **Robustness**: Advanced `CountdownRateLimiter` with escalating cooldowns and per-worker progress reporting.
- **Resilience**: Full checkpointing and recovery support for long-running scrapes.

### Installation
```bash
pip install aiohttp aiolimiter tenacity beautifulsoup4 lxml rich tqdm
```

### Usage
The system is unified under `main.py` using `typer`.

Run the full pipeline (Discovery -> URL Extraction):
```bash
python main.py scrape
```

Run and continue from last checkpoint:
```bash
python main.py scrape --continue
```

Run with Phase 3 (Deep Profile Scrape):
```bash
python main.py scrape --deep
```

Run only the deep scrape (requires Phase 2 results):
```bash
python main.py deep-scrape
```

Cleanup all temporary artifacts:
```bash
python main.py cleanup
```

View data statistics:
```bash
python main.py stats
```

For a list of all commands and options:
```bash
python main.py --help
```

### Project Structure
- `bruteforce_scraper.py`: Main orchestrator for Phase 1 & 2 (and optionally Phase 3).
- `profile_scraper.py`: Phase 3 detailed profile scraper.
- `pagination_discovery.py`: Phase 1 discovery logic.
- `parsing.py`: Profile and directory parsing logic.
- `common.py`: Shared utilities and advanced rate limiter.
- `config.py`: Global configuration and user agents.
- `combos.py`: Filter combination generator.
