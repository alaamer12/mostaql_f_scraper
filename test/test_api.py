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
