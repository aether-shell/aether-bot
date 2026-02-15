import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from nanobot.cli.commands import _build_web_search_config, app
from nanobot.config.schema import Config

runner = CliRunner()


@pytest.fixture
def mock_paths():
    """Mock configuration and workspace paths for isolation."""
    with patch("nanobot.config.loader.get_config_path") as mock_config_path, \
         patch("nanobot.config.loader.save_config") as mock_save_config, \
         patch("nanobot.utils.helpers.get_workspace_path") as mock_ws_path:
        
        # Create temporary paths
        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()
        
        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"
        
        mock_config_path.return_value = config_file
        mock_ws_path.return_value = workspace_dir
        
        # We need save_config to actually write the file for existence checks to work
        def side_effect_save_config(config):
            with open(config_file, "w") as f:
                f.write("{}")

        mock_save_config.side_effect = side_effect_save_config
        
        yield config_file, workspace_dir
        
        # Cleanup
        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths):
    """Test onboarding with no existing files."""
    config_file, workspace_dir = mock_paths
    
    result = runner.invoke(app, ["onboard"])
    
    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "nanobot is ready" in result.stdout
    
    assert config_file.exists()
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()


def test_onboard_existing_config_no_overwrite(mock_paths):
    """Test onboarding with existing config, user declines overwrite."""
    config_file, workspace_dir = mock_paths
    
    # Pre-create config
    config_file.write_text('{"existing": true}')
    
    # Input "n" for overwrite prompt
    result = runner.invoke(app, ["onboard"], input="n\n")
    
    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    
    # Verify config was NOT changed
    assert '{"existing": true}' in config_file.read_text()
    
    # Verify workspace was still created
    assert "Created workspace" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Test onboarding with existing config, user checks overwrite."""
    config_file, workspace_dir = mock_paths
    
    # Pre-create config
    config_file.write_text('{"existing": true}')
    
    # Input "y" for overwrite prompt
    result = runner.invoke(app, ["onboard"], input="y\n")
    
    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Created config" in result.stdout
    
    # Verify config WAS changed (our mock writes "{}")
    test_content = config_file.read_text()
    assert test_content == "{}" or test_content == "" 

    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Test onboarding with existing workspace safely creates templates without prompting."""
    config_file, workspace_dir = mock_paths
    
    # Pre-create workspace
    workspace_dir.mkdir(parents=True)
    
    # Scenario: Config exists (keep it), Workspace exists (add templates automatically)
    config_file.write_text("{}")
    
    inputs = "n\n"  # No overwrite config
    result = runner.invoke(app, ["onboard"], input=inputs)
    
    assert result.exit_code == 0
    # Workspace exists message
    # Depending on implementation, it might say "Workspace already exists" or just proceed.
    # Code in commands.py Line 180: if not workspace.exists(): ...
    # It does NOT print "Workspace already exists" if it exists.
    # It only prints "Created workspace" if it created it.
    
    assert "Created workspace" not in result.stdout
    
    # Should NOT prompt for templates
    assert "Create missing default templates?" not in result.stdout
    
    # But SHOULD create them (since _create_workspace_templates is called unconditionally)
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()


def test_build_web_search_config_adds_openai_resilience_fallbacks() -> None:
    config = Config()
    config.tools.web.search.provider = "openai_hosted"
    config.tools.web.search.fallback_providers = []

    search_cfg = _build_web_search_config(config)

    assert search_cfg.fallback_providers == ["bing_news_jina", "hn_algolia"]


def test_build_web_search_config_prefers_active_openai_model() -> None:
    config = Config()
    config.agents.defaults.model = "gpt-5.3-codex"
    config.providers.openai.api_key = "sk-test"
    config.tools.web.search.openai_model = "gpt-4.1-mini"

    search_cfg = _build_web_search_config(config)

    assert search_cfg.openai_model == "gpt-5.3-codex"
