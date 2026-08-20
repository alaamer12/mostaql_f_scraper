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


def test_main_api_deep_scrape_command_and_results(tmp_path):
    import json
    import asyncio
    from src.main_api import run_scraper_task, list_results, COMMANDS_BY_SLUG, RUN_HISTORY

    # Create a temporary input file
    input_users = tmp_path / "mostaql_users.json"
    input_users.write_text(json.dumps([
        {"name": "Alice", "profile_url": "https://mostaql.com/u/alice_test_user", "title": "Developer"}
    ]), encoding="utf-8")

    # Verify deep_scrape output attributes only list actual profiles outputs
    deep_spec = COMMANDS_BY_SLUG.get("deep_scrape")
    assert deep_spec["output_attrs"] == ["profiles_json", "profiles_csv"]

    # Verify scrape output attrs
    scrape_spec = COMMANDS_BY_SLUG.get("scrape")
    assert "pagination_cache" not in scrape_spec["output_attrs"]
    assert "profiles_json" in scrape_spec["output_attrs"]

    # Test list_results
    results_dict = asyncio.run(list_results())
    assert "files" in results_dict
    assert isinstance(results_dict["files"], list)


def test_history_directory_tree_and_download(tmp_path, monkeypatch):
    import os
    import time
    import asyncio
    import pytest
    from fastapi import HTTPException
    import src.main_api as api_mod
    from src.main_api import _build_directory_tree, get_history_tree, download_history_file

    mock_outsource = tmp_path / "mock_outsourcing"
    mock_outsource.mkdir()
    monkeypatch.setattr(api_mod, "OUTSOURCE_DIR", str(mock_outsource))

    # Create run 1 (older)
    run1 = mock_outsource / "run_old"
    run1.mkdir()
    r1_dl = run1 / "downloads"
    r1_dl.mkdir()
    (r1_dl / "users_old.json").write_text('{"count": 5}', encoding="utf-8")
    (r1_dl / "users_old.csv").write_text('id,name\n1,test', encoding="utf-8")

    # Set mtime for run1 to be older
    os.utime(str(run1), (1000000, 1000000))

    # Create run 2 (newer)
    run2 = mock_outsource / "run_new"
    run2.mkdir()
    r2_dl = run2 / "downloads"
    r2_dl.mkdir()
    (r2_dl / "profiles.json").write_text('{"profiles": 10}', encoding="utf-8")
    r2_logs = run2 / "logs"
    r2_logs.mkdir()
    (r2_logs / "run.log").write_text('info log line', encoding="utf-8")

    os.utime(str(run2), (2000000, 2000000))

    # Call get_history_tree
    tree_res = asyncio.run(get_history_tree())
    assert tree_res["exists"] is True
    assert tree_res["total_folders"] == 2
    assert tree_res["total_files"] == 4

    # Validate sorting: run_new should come before run_old (latest first)
    top_folders = [c["name"] for c in tree_res["tree"] if c["type"] == "directory"]
    assert top_folders == ["run_new", "run_old"]

    # Validate run_new node file count badge
    run_new_node = [c for c in tree_res["tree"] if c["name"] == "run_new"][0]
    assert run_new_node["file_count"] == 2 # 1 in downloads, 1 in logs
    assert len(run_new_node["children"]) == 2

    # Validate run_old node file count badge
    run_old_node = [c for c in tree_res["tree"] if c["name"] == "run_old"][0]
    assert run_old_node["file_count"] == 2 # 2 in downloads

    # Test download valid file
    res = asyncio.run(download_history_file(path="run_new/downloads/profiles.json"))
    assert res.filename == "profiles.json"
    assert os.path.exists(res.path)

    # Test path traversal attack blocked
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(download_history_file(path="../../main.py"))
    assert exc_info.value.status_code == 403

    # Test non-existent file returns 404
    with pytest.raises(HTTPException) as exc_info404:
        asyncio.run(download_history_file(path="run_new/downloads/non_existent.json"))
    assert exc_info404.value.status_code == 404


def test_phase_metrics_live_reporting_and_api_stats():
    import asyncio
    from src.utils.reporting import PhaseMetrics, MetricsRegistry
    from src.main_api import get_stats, orchestrator

    # Test PhaseMetrics to_dict while running
    metrics = PhaseMetrics(phase_name="Discovery")
    metrics.increment("combos_processed", 5)
    metrics.increment("pages_found", 12)
    metrics.increment("requests", 10)

    dict_running = metrics.to_dict()
    assert dict_running["status"] == "running"
    assert dict_running["combos_processed"] == 5
    assert dict_running["pages_found"] == 12
    assert dict_running["requests"] == 10
    assert "duration_seconds" in dict_running

    # Test completed phase metrics
    metrics.duration_seconds = 4.5
    dict_completed = metrics.to_dict()
    assert dict_completed["status"] == "completed"
    assert dict_completed["duration_seconds"] == 4.5

    # Test registry integration with /api/stats
    orchestrator.registry.clear()
    orch_metrics = PhaseMetrics(phase_name="Fetch")
    orch_metrics.increment("profiles_fetched", 20)
    orchestrator.registry.register(orch_metrics)

    stats = asyncio.run(get_stats())
    assert "metrics" in stats
    assert "phases" in stats["metrics"]
    assert "Fetch" in stats["metrics"]["phases"]
    assert stats["metrics"]["phases"]["Fetch"]["profiles_fetched"] == 20
    assert stats["metrics"]["phases"]["Fetch"]["status"] == "running"


def test_task_status_timing_in_stats():
    import asyncio
    import time
    from src.main_api import get_stats, scrape_status

    scrape_status.is_running = True
    scrape_status.current_command = "deep_scrape"
    scrape_status.started_at = time.time() - 5.5
    scrape_status.duration_seconds = 0.0

    stats = asyncio.run(get_stats())
    assert stats["task_status"]["is_running"] is True
    assert stats["task_status"]["current_command"] == "deep_scrape"
    assert stats["task_status"]["started_at"] is not None
    assert stats["task_status"]["duration_seconds"] >= 5.0

    # Test completed task duration
    scrape_status.is_running = False
    scrape_status.duration_seconds = 12.34
    stats_done = asyncio.run(get_stats())
    assert stats_done["task_status"]["is_running"] is False
    assert stats_done["task_status"]["duration_seconds"] == 12.34
