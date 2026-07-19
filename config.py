"""Shared configuration for Mostaql scraper and follow-up tools."""

CONFIG = {
    "BASE_URL": "https://mostaql.com/freelancers",

    # Set to -1  -> scrape ALL pages; Set to N -> scrape up to N pages
    "MAX_PAGES": -1,

    # Parallel workers (throughput capped by rate limiter below)
    "PROFILE_CONCURRENCY": 10,
    "DIR_CONCURRENCY": 3,

    # Token-bucket rate limiter (aiolimiter)
    "RATE_LIMIT_BURST": 6,
    "RATE_LIMIT_PERIOD": 2.0,

    # Tenacity retry policy
    "MAX_RETRIES": 6,
    "RETRY_WAIT_MIN": 2,
    "RETRY_WAIT_MAX": 90,

    "TIMEOUT": 20,
    "OUTPUT_JSON": "mostaql_development_all_users.json",
    "OUTPUT_CSV": "mostaql_development_all_users.csv",
    "PROFILES_JSON": "mostaql_development_profiles.json",
    "PROFILES_CSV": "mostaql_development_profiles.csv",

    "BINARY_SEARCH_INITIAL": 100,
    "MIN_CONFIDENCE": 2,
    "MIN_HTML_BYTES": 20_000,

    "USER_AGENTS": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ],
}

# Gentler defaults for follow-up / repair runs
FOLLOWUP_DEFAULTS = {
    "CONCURRENCY": 3,
    "RATE_LIMIT_BURST": 3,
    "RATE_LIMIT_PERIOD": 2.5,
    "MAX_RETRIES": 8,
    "RETRY_WAIT_MIN": 3,
    "RETRY_WAIT_MAX": 120,
}
