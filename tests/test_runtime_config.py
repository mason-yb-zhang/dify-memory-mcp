import asyncio
import json
from pathlib import Path

import local_store
import openclaw_session
import runtime_config
import session_store


def test_mcporter_config_path_prefers_environment(tmp_path, monkeypatch):
    config_path = tmp_path / "custom-mcporter.json"
    monkeypatch.setenv("MCPORTER_CONFIG", str(config_path))
    assert runtime_config.get_config_path() == config_path


def test_default_mcporter_config_is_cwd_relative(tmp_path, monkeypatch):
    monkeypatch.delenv("MCPORTER_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    assert runtime_config.get_config_path() == tmp_path / "config" / "mcporter.json"


def test_config_controls_persistence_and_session_sources(tmp_path, monkeypatch):
    config_path = tmp_path / "config" / "mcporter.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "persistence": {"directory": "./state"},
                "sessionSources": {
                    "default": "opencode",
                    "opencode": {"databasePath": "./sessions/opencode.db"},
                    "openclaw": {"sessionsRoot": "./sessions/openclaw"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MCPORTER_CONFIG", str(config_path))
    monkeypatch.delenv("MIFY_MEMORY_STATE_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_DB_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_SESSIONS_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert runtime_config.state_dir() == tmp_path / "state"
    assert runtime_config.session_source() == "opencode"
    assert runtime_config.opencode_db_path() == tmp_path / "sessions" / "opencode.db"
    assert runtime_config.openclaw_sessions_root() == tmp_path / "sessions" / "openclaw"
    assert local_store.default_state_dir() == tmp_path / "state"
    assert openclaw_session.default_session_root() == tmp_path / "sessions" / "openclaw"
    assert session_store._opencode_db_path() == tmp_path / "sessions" / "opencode.db"


def test_missing_session_sources_disable_extraction(tmp_path, monkeypatch):
    config_path = tmp_path / "config" / "mcporter.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({"sessionSources": {"default": ""}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MCPORTER_CONFIG", str(config_path))
    monkeypatch.delenv("OPENCODE_DB_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_SESSIONS_ROOT", raising=False)

    assert runtime_config.opencode_db_path() is None
    assert runtime_config.openclaw_sessions_root() is None
    assert session_store._extract_opencode_sessions() == []
    assert openclaw_session.extract_openclaw_sessions() == []
    result = asyncio.run(session_store.save_session(source="configured"))
    assert result["success"] is True
    assert result["skipped"] is True


def test_mify_client_rebinds_local_store_after_environment_load(tmp_path, monkeypatch):
    import memory_service

    monkeypatch.setenv("MIFY_MEMORY_STATE_DIR", str(tmp_path / "state"))
    client = memory_service.MifyClient()
    client.configure_from_env()
    assert client.local_store.state_dir == tmp_path / "state"
