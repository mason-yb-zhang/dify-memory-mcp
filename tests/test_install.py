"""
Task 1 regression tests for install.py:
  - install_skill_files() copies scripts/session_store.py
  - patch_tool_config() writes valid JSON atomically (no .tmp leftovers)
  - install.py source does not reference the non-existent setup_sessions.py
"""

import importlib.util
import json
import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Import install.py as a module without executing main()
# ---------------------------------------------------------------------------

_INSTALL_PY = os.path.join(
    os.path.dirname(__file__),  # tests/
    "..",
    "scripts",
    "install.py",
)
_INSTALL_PY = os.path.abspath(_INSTALL_PY)


def _load_install_module():
    """Load install.py under the name 'install_mod' without running main()."""
    spec = importlib.util.spec_from_file_location("install_mod", _INSTALL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 1: install_skill_files copies session_store.py
# ---------------------------------------------------------------------------


def test_install_skill_files_copies_session_store(tmp_path, monkeypatch):
    """install_skill_files() must copy runtime scripts and OpenCode plugin files."""
    install_mod = _load_install_module()

    # Redirect HOME so the skill is installed under tmp_path
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Also override os.path.expanduser so ~/ maps to fake_home on all platforms
    original_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path.startswith("~"):
            return str(fake_home) + path[1:]
        return original_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)

    # Reload the module so SCRIPT_DIR / PROJECT_DIR etc. remain the real src paths
    # but the destination (~/.config/...) resolves under fake_home.
    install_mod.install_skill_files()

    expected = (
        fake_home
        / ".config"
        / "opencode"
        / "skills"
        / "mify-memory-mcp"
        / "scripts"
        / "session_store.py"
    )
    assert expected.exists(), (
        f"session_store.py not found at {expected}. "
        "install_skill_files() must include 'scripts/session_store.py' in scripts_to_copy."
    )

    plugin = (
        fake_home
        / ".config"
        / "opencode"
        / "skills"
        / "mify-memory-mcp"
        / "plugins"
        / "mify-memory-inject.js"
    )
    plugin_config = (
        fake_home
        / ".config"
        / "opencode"
        / "skills"
        / "mify-memory-mcp"
        / "config"
        / "mify-memory-plugin.jsonc"
    )
    assert plugin.exists(), (
        "install_skill_files() must copy plugins/mify-memory-inject.js"
    )
    assert plugin_config.exists(), (
        "install_skill_files() must copy config/mify-memory-plugin.jsonc"
    )

    runtime_config = (
        fake_home
        / ".config"
        / "opencode"
        / "skills"
        / "mify-memory-mcp"
        / "scripts"
        / "runtime_config.py"
    )
    mcporter_config = (
        fake_home
        / ".config"
        / "opencode"
        / "skills"
        / "mify-memory-mcp"
        / "config"
        / "mcporter.json"
    )
    assert runtime_config.exists(), (
        "install_skill_files() must copy scripts/runtime_config.py"
    )
    assert mcporter_config.exists(), (
        "install_skill_files() must copy config/mcporter.json"
    )
    for name in (
        "README.md",
        "CHANGELOG.md",
        "references/installation.md",
        "references/mcp-registration.md",
        "references/api-reference.md",
        "references/architecture.md",
        "references/environment.md",
        "references/troubleshooting.md",
    ):
        assert (fake_home / ".config" / "opencode" / "skills" / "mify-memory-mcp" / name).exists()


# ---------------------------------------------------------------------------
# Test 2: patch_tool_config writes valid JSON atomically (no .tmp leftover)
# ---------------------------------------------------------------------------


def test_patch_tool_config_writes_valid_json_atomically(tmp_path):
    """patch_tool_config() must produce valid JSON and leave no .tmp file."""
    install_mod = _load_install_module()

    config_path = tmp_path / "opencode.json"
    config_path.write_text(json.dumps({"mcp": {}}), encoding="utf-8")

    install_mod.patch_tool_config(
        tool_name="opencode",
        config_path=str(config_path),
        python_path="/usr/bin/python3",
        server_path="/some/server.py",
        env_vars={"MIFY_API_KEY": "dataset-test"},
    )

    # Must be valid JSON
    content = config_path.read_text(encoding="utf-8")
    parsed = json.loads(content)  # raises if invalid

    # Must contain the canonical mify-memory entry
    assert "mcp" in parsed
    assert "mify-memory" in parsed["mcp"], (
        "Expected 'mify-memory' key under 'mcp' for opencode tool"
    )
    assert "memory" not in parsed["mcp"], (
        "Legacy 'memory' key should be removed from the canonical OpenCode config"
    )
    registration_env = parsed["mcp"]["mify-memory"]["environment"]
    assert registration_env["MCPORTER_CONFIG"].endswith(
        os.path.join("config", "mcporter.json")
    )

    # Must NOT leave any .tmp file in the directory
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Unexpected .tmp file(s) left behind: {tmp_files}"


def test_persistent_registration_is_harness_neutral_and_secret_free():
    install_mod = _load_install_module()

    registration = install_mod.build_persistent_registration(
        "/usr/bin/python3", "/opt/mify-tools/mcp/mify-memory-mcp/scripts/server.py"
    )

    assert registration["server_key"] == "mify-memory"
    assert registration["description"]
    assert registration["transport"] == "stdio"
    assert registration["persistent"] is True
    assert registration["mcporter_config"].endswith(
        os.path.join("config", "mcporter.json")
    )
    assert "MCPORTER_CONFIG" in registration["env_keys"]
    assert "dataset-" not in str(registration)


def test_patch_tool_config_refuses_to_rewrite_json5(tmp_path):
    """JSON5 agent configs must be preserved instead of reset to an empty object."""
    install_mod = _load_install_module()

    config_path = tmp_path / "openclaw.json"
    original = (
        "{\n"
        "  // preserve this comment and existing config\n"
        "  mcp: { existing: true }\n"
        "}\n"
    )
    config_path.write_text(original, encoding="utf-8")

    result = install_mod.patch_tool_config(
        tool_name="openclaw",
        config_path=str(config_path),
        python_path="/usr/bin/python3",
        server_path="/some/server.py",
        env_vars={"MIFY_API_KEY": "dataset-test"},
    )

    assert result is False
    assert config_path.read_text(encoding="utf-8") == original


def test_install_opencode_memory_plugin_registers_plugin(tmp_path, monkeypatch):
    """install_opencode_memory_plugin() must copy files and register the plugin path."""
    install_mod = _load_install_module()

    fake_home = tmp_path / "home"
    config_dir = fake_home / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    opencode_config = config_dir / "opencode.json"
    opencode_config.write_text(json.dumps({"plugin": []}), encoding="utf-8")

    plugin_path = config_dir / "plugins" / "mify-memory-inject.js"
    plugin_config_path = config_dir / "mify-memory-plugin.jsonc"
    monkeypatch.setattr(install_mod, "OPENCODE_PLUGIN_PATH", str(plugin_path))
    monkeypatch.setattr(
        install_mod, "OPENCODE_PLUGIN_CONFIG_PATH", str(plugin_config_path)
    )

    result = install_mod.install_opencode_memory_plugin(str(opencode_config))

    assert result["installed"] is True
    assert result["registered"] is True
    assert plugin_path.exists()
    assert plugin_config_path.exists()
    parsed = json.loads(opencode_config.read_text(encoding="utf-8"))
    assert str(plugin_path) in parsed["plugin"]


# ---------------------------------------------------------------------------
# Test 3: Static source check — no reference to setup_sessions.py
# ---------------------------------------------------------------------------


def test_update_mode_message_does_not_reference_setup_sessions_py():
    """install.py must not contain any reference to 'setup_sessions.py'."""
    with open(_INSTALL_PY, "r", encoding="utf-8") as fh:
        source = fh.read()

    assert "setup_sessions.py" not in source, (
        "install.py still references 'setup_sessions.py', which does not exist. "
        "Remove or replace all such references."
    )


def test_append_agent_rules_refreshes_existing_rules(tmp_path, monkeypatch):
    """append_agent_rules() must refresh existing rules instead of skipping stale content."""
    install_mod = _load_install_module()

    fake_home = tmp_path / "home"
    config_dir = fake_home / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    agents_md = config_dir / "AGENTS.md"
    agents_md.write_text(
        "# Existing Rules\n\n---\n\n# Agent Memory Rules\n\nold content\n",
        encoding="utf-8",
    )

    original_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path.startswith("~"):
            return str(fake_home) + path[1:]
        return original_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)

    assert install_mod.append_agent_rules() is True

    content = agents_md.read_text(encoding="utf-8")
    assert "old content" not in content
    assert "<!-- BEGIN mify-memory-mcp AGENT_RULES -->" in content
    assert "<!-- END mify-memory-mcp AGENT_RULES -->" in content
    assert "## 会话存档（Session Archive）" in content


def test_get_memory_env_state_reports_missing_required_values():
    """An enabled MCP entry without environment values needs configuration."""
    install_mod = _load_install_module()

    state = install_mod.get_memory_env_state(
        {
            "mcp": {
                "memory": {
                    "enabled": True,
                    "environment": {"MIFY_API_KEY": "dataset-test"},
                }
            }
        }
    )

    assert state == {
        "field": "environment",
        "missing": ["MIFY_DATASET_ID", "MIFY_DOCUMENT_ID"],
        "ready": False,
    }


def test_get_memory_env_state_accepts_complete_environment():
    """A complete OpenCode environment is ready without exposing values."""
    install_mod = _load_install_module()

    state = install_mod.get_memory_env_state(
        {
            "mcp": {
                "memory": {
                    "environment": {
                        "MIFY_API_KEY": "dataset-test",
                        "MIFY_DATASET_ID": "dataset-id",
                        "MIFY_DOCUMENT_ID": "document-id",
                    }
                }
            }
        }
    )

    assert state == {"field": "environment", "missing": [], "ready": True}


def test_get_memory_env_state_reports_legacy_env_field():
    """Legacy env is recognized so update mode can migrate it."""
    install_mod = _load_install_module()

    state = install_mod.get_memory_env_state(
        {
            "mcp": {
                "memory": {
                    "env": {
                        "MIFY_API_KEY": "dataset-test",
                        "MIFY_DATASET_ID": "dataset-id",
                        "MIFY_DOCUMENT_ID": "document-id",
                    }
                }
            }
        }
    )

    assert state == {"field": "env", "missing": [], "ready": True}


def test_migrate_legacy_memory_env_moves_values_to_environment(tmp_path):
    """Legacy env config is migrated to OpenCode's environment field."""
    install_mod = _load_install_module()
    config_path = tmp_path / "opencode.json"
    config_path.write_text(
        json.dumps(
            {
                "mcp": {
                    "memory": {
                        "enabled": True,
                        "env": {
                            "MIFY_API_KEY": "dataset-test",
                            "MIFY_DATASET_ID": "dataset-id",
                            "MIFY_DOCUMENT_ID": "document-id",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert install_mod.migrate_legacy_memory_env(str(config_path)) is True
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["mcp"]["memory"]["environment"]["MIFY_DATASET_ID"] == "dataset-id"
    assert "env" not in config["mcp"]["memory"]
