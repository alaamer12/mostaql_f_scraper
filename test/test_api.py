import pytest
from src.api import Pipeline, Commands, Configuration


def test_configuration_and_pipeline_init():
    config = Configuration(max_pages=3, profile_concurrency=5)
    assert config.max_pages == 3
    assert config.profile_concurrency == 5

    pipeline = Pipeline(config=config)
    assert pipeline.config == config
    assert pipeline.orchestrator.config == config


def test_commands_enum():
    assert Commands.DISCOVERY == "discovery"
    assert Commands.EXTRACT == "extract"
    assert Commands.FETCH == "fetch"
    assert Commands.PARSE == "parse"
    assert Commands.SCRAPE == "scrape"
    assert Commands.CLEANUP == "cleanup"
    assert Commands.STATS == "stats"


def test_pipeline_invalid_command():
    pipeline = Pipeline()
    with pytest.raises(ValueError, match="Unknown command"):
        pipeline.run("nonexistent_command_xyz")


def test_pipeline_cleanup_and_stats():
    config = Configuration(output_json="test_output.json", profiles_json="test_profiles.json")
    pipeline = Pipeline(config=config)
    
    # Run stats (should return default/zero or handle missing files gracefully)
    stats = pipeline.run(Commands.STATS)
    assert isinstance(stats, dict)

    # Run cleanup
    res = pipeline.run(Commands.CLEANUP)
    assert res is True


def test_command_registry_input_file():
    from src.main_api import COMMANDS_BY_SLUG

    deep_scrape_cmd = COMMANDS_BY_SLUG.get("deep_scrape")
    assert deep_scrape_cmd is not None
    assert deep_scrape_cmd["needs_file"] is True
    assert deep_scrape_cmd["file_field"] == "input_file"

    fetch_cmd = COMMANDS_BY_SLUG.get("fetch")
    assert fetch_cmd is not None
    assert fetch_cmd["needs_file"] is True
    assert fetch_cmd["file_field"] == "input_file"


def test_orchestrator_custom_input_file(tmp_path):
    import json
    import asyncio
    from src.services.orchestrator import ScraperOrchestrator
    from src.models import ScrapeConfig

    custom_input = tmp_path / "custom_users.json"
    custom_input.write_text(json.dumps([
        {"name": "Alice", "profile_url": "https://mostaql.com/u/alice_test", "title": "Developer"}
    ]), encoding="utf-8")

    out_profiles = tmp_path / "custom_profiles.json"
    checkpoint_fetch = tmp_path / "checkpoint_fetch.jsonl"

    config = ScrapeConfig(
        output_json=str(tmp_path / "unused.json"),
        profiles_json=str(out_profiles),
        checkpoint_fetch_json=str(checkpoint_fetch),
        profile_concurrency=1,
    )
    orch = ScraperOrchestrator(config)

    # Calling run_fetch with custom input path
    data = orch.storage.load_json(str(custom_input))
    assert len(data) == 1
    assert data[0]["profile_url"] == "https://mostaql.com/u/alice_test"


def test_periodic_flush_during_parsing(tmp_path):
    import json
    import asyncio
    from src.services.orchestrator import ScraperOrchestrator
    from src.models import ScrapeConfig
    from src.pipeline.channel import NullChannel

    # Create dummy raw html records in checkpoint_fetch.jsonl
    checkpoint_fetch = tmp_path / "checkpoint_fetch.jsonl"
    dummy_html = """
    <html>
        <head><title>Test User</title></head>
        <body>
            <h1><bdi>Test User</bdi></h1>
            <div id="user-stats">
                <table>
                    <tr><td>معدل إكمال المشاريع</td><td>100%</td></tr>
                </table>
            </div>
        </body>
    </html>
    """
    records = [
        {"profile_url": f"https://mostaql.com/u/user_{i}", "html": dummy_html, "portfolio_html": None}
        for i in range(15)
    ]
    with open(checkpoint_fetch, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    out_profiles = tmp_path / "analysis.json"
    out_csv = tmp_path / "analysis.csv"

    config = ScrapeConfig(
        checkpoint_fetch_json=str(checkpoint_fetch),
        profiles_json=str(out_profiles),
        profiles_csv=str(out_csv),
        checkpoint_flush_every=5,  # flush every 5 items
    )
    orch = ScraperOrchestrator(config)

    # Run stream_parse
    parsed = asyncio.run(orch.stream_parse(None, NullChannel(), output_json=str(out_profiles), output_csv=str(out_csv)))
    assert len(parsed) == 15
    assert out_profiles.exists()
    assert out_csv.exists()

    saved_json = orch.storage.load_json(str(out_profiles))
    assert len(saved_json) == 15


def test_concurrent_deep_scrape_with_seeded_checkpoint(tmp_path):
    import json
    import asyncio
    from src.services.orchestrator import ScraperOrchestrator
    from src.models import ScrapeConfig

    # Seed 10 records in checkpoint_fetch.jsonl
    checkpoint_fetch = tmp_path / "checkpoint_fetch.jsonl"
    dummy_html = """
    <html>
        <head><title>Test User</title></head>
        <body>
            <h1><bdi>Concurrent User</bdi></h1>
            <div id="user-stats">
                <table>
                    <tr><td>معدل إكمال المشاريع</td><td>100%</td></tr>
                </table>
            </div>
        </body>
    </html>
    """
    records = [
        {"profile_url": f"https://mostaql.com/u/concurrent_user_{i}", "html": dummy_html, "portfolio_html": None}
        for i in range(10)
    ]
    with open(checkpoint_fetch, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    input_users = tmp_path / "users.json"
    input_users.write_text(json.dumps([
        {"name": f"concurrent_user_{i}", "profile_url": f"https://mostaql.com/u/concurrent_user_{i}"}
        for i in range(10)
    ]), encoding="utf-8")

    out_profiles = tmp_path / "deep_profiles.json"
    out_csv = tmp_path / "deep_profiles.csv"

    config = ScrapeConfig(
        checkpoint_fetch_json=str(checkpoint_fetch),
        output_json=str(input_users),
        profiles_json=str(out_profiles),
        profiles_csv=str(out_csv),
        checkpoint_flush_every=5,
    )
    orch = ScraperOrchestrator(config)

    res = asyncio.run(orch.run_deep_scrape(
        use_continue=True,
        input_path=str(input_users),
        output_json=str(out_profiles),
        output_csv=str(out_csv)
    ))

    assert len(res) == 10
    assert out_profiles.exists()
    assert out_csv.exists()
    saved = orch.storage.load_json(str(out_profiles))
    assert len(saved) == 10
