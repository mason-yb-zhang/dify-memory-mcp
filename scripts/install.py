#!/usr/bin/env python3
"""
dify-memory-mcp one-command installer.

Usage (paste the following instruction in an AI chat):

    帮我安装 dify-memory-mcp 记忆插件：
    1. pip install mcp httpx -q
    2. 下载 https://git.n.xiaomi.com/zhangyanbin3/ai-skills/-/archive/main/ai-skills-main.zip 到临时目录并解压（已存在则跳过）
    3. 运行其中的 dify-memory-mcp/scripts/install.py

Or if already cloned:
    python install.py --api-key <KEY>

What it does (fully automatic, no manual steps):
    1. Installs pip dependencies (mcp, httpx) if missing
    2. Verifies the Dify API Key
    3. Creates knowledge base + container document on Dify (idempotent)
    4. Copies Skill files to ~/.config/opencode/skills/dify-memory-mcp/
    5. Patches opencode.json to register the MCP server
    6. Appends AGENT_RULES to ~/.config/opencode/AGENTS.md (if not present)
    7. Prints success JSON to stdout
"""

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
import urllib.error

DIFY_BASE_URL = (
    os.environ.get("DIFY_BASE_URL")
    or os.environ.get("DIFY_BASE_URL")
    or "http://127.0.0.1/v1"
).rstrip("/")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # parent of scripts/
SERVER_PATH = os.path.join(SCRIPT_DIR, "server.py")
UNIFIED_SERVER_PATH = os.environ.get("DIFY_MEMORY_MCP_SERVER_PATH") or os.path.expanduser(
    "~/.dify-tools/mcp/dify-memory-mcp/scripts/server.py"
)
PLUGIN_SOURCE_PATH = os.path.join(PROJECT_DIR, "plugins", "dify-memory-inject.js")
PLUGIN_CONFIG_SOURCE_PATH = os.path.join(
    PROJECT_DIR, "config", "dify-memory-plugin.jsonc"
)
OPENCODE_PLUGIN_PATH = os.path.expanduser(
    "~/.config/opencode/plugins/dify-memory-inject.js"
)
OPENCODE_PLUGIN_CONFIG_PATH = os.path.expanduser(
    "~/.config/opencode/dify-memory-plugin.jsonc"
)
REQUIRED_MEMORY_ENV = (
    "DIFY_API_KEY",
    "DIFY_DATASET_ID",
    "DIFY_DOCUMENT_ID",
)


# ─── Utility ────────────────────────────────────────────────────────────────


def log(msg):
    print(f"[dify-memory] {msg}", file=sys.stderr)


def find_stable_python():
    """Find a stable, non-venv Python path suitable for long-term config.

    Priority:
    1. If current python is NOT in a venv/tmp dir, use it
    2. Try 'python3' / 'python' from PATH (non-venv)
    3. Fall back to sys.executable
    """
    import shutil as _shutil

    def is_stable_path(p):
        """Check if a python path is stable (not venv, not tmp)."""
        if not p:
            return False
        p_lower = p.lower().replace("\\", "/")
        unstable_markers = [
            "/venv/",
            "/.venv/",
            "/env/",
            "/virtualenv/",
            "/tmp/",
            "/temp/",
            "appdata/local/temp",
        ]
        return not any(m in p_lower for m in unstable_markers)

    # Option 1: current executable if stable
    current = sys.executable
    if is_stable_path(current):
        return current

    # Option 2: find python3/python from PATH
    for cmd in ["python3", "python"]:
        found = _shutil.which(cmd)
        if found and is_stable_path(found):
            return found

    # Option 3: fallback
    log(f"Warning: using potentially unstable python path: {current}")
    return current


def api_request(method, path, api_key, body=None):
    url = f"{DIFY_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        if resp.status == 204:
            return {"success": True}
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}: {error_body}"}
    except urllib.error.URLError as e:
        return {"error": f"Network error: {e.reason}. Check intranet connectivity."}
    except Exception as e:
        return {"error": f"Unexpected error: {type(e).__name__}: {e}"}


def find_opencode_config():
    """Find opencode.json in standard locations."""
    candidates = [
        os.path.expanduser("~/.config/opencode/opencode.json"),
        os.path.join(os.getcwd(), "opencode.json"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def get_memory_entry(config):
    """Return the canonical OpenCode memory MCP entry, accepting the legacy key."""
    mcp = config.get("mcp", {})
    return mcp.get("dify-memory") or mcp.get("memory") or {}


def get_memory_env_state(config):
    """Return safe readiness metadata for the OpenCode memory environment."""
    memory = get_memory_entry(config)
    if isinstance(memory.get("environment"), dict):
        field = "environment"
        values = memory["environment"]
    elif isinstance(memory.get("env"), dict):
        field = "env"
        values = memory["env"]
    else:
        field = "environment"
        values = {}

    missing = [name for name in REQUIRED_MEMORY_ENV if not values.get(name)]
    return {"field": field, "missing": missing, "ready": not missing}


def _write_json_atomic(config_path, config):
    """Write JSON beside the target and replace it only after a complete write."""
    config_dir = os.path.dirname(os.path.abspath(config_path))
    tmp_path = os.path.join(config_dir, os.path.basename(config_path) + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, config_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def migrate_legacy_memory_env(config_path):
    """Move legacy env to environment, rewriting JSON and removing comments."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    mcp = config.get("mcp", {})
    memory_key = next(
        (key for key in ("dify-memory", "memory") if isinstance(mcp.get(key), dict)),
        None,
    )
    memory = mcp.get(memory_key, {}) if memory_key else {}
    if isinstance(memory.get("environment"), dict) or not isinstance(
        memory.get("env"), dict
    ):
        return False

    memory["environment"] = memory.pop("env")
    _write_json_atomic(config_path, config)
    return True


# ─── Multi-tool detection ────────────────────────────────────────────────────

# Each tool has: config_path, mcp_key_path, env_field_name, format
TOOL_CONFIGS = {
    "opencode": {
        "paths": [
            os.path.expanduser("~/.config/opencode/opencode.json"),
        ],
        "mcp_root": "mcp",  # config["mcp"]["dify-memory"]
        "server_key": "dify-memory",
        "env_field": "environment",  # OpenCode uses "environment"
        "command_format": "array",  # ["python", "server.py"]
    },
    "claude_code": {
        "paths": [
            os.path.expanduser("~/.claude/settings.json"),
            os.path.expanduser("~/.claude.json"),
        ],
        "mcp_root": "mcpServers",  # config["mcpServers"]["dify-memory"]
        "server_key": "dify-memory",
        "env_field": "env",  # Claude uses "env"
        "command_format": "split",  # "command": "python", "args": ["server.py"]
    },
    "claude_desktop": {
        "paths": [
            os.path.join(
                os.environ.get("LOCALAPPDATA", ""),
                "Claude-3p",
                "claude_desktop_config.json",
            ),
            os.path.join(
                os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json"
            ),
            os.path.expanduser(
                "~/Library/Application Support/Claude/claude_desktop_config.json"
            ),
        ],
        "mcp_root": "mcpServers",
        "server_key": "dify-memory",
        "env_field": "env",
        "command_format": "split",
    },
    "cursor": {
        "paths": [
            os.path.expanduser("~/.cursor/mcp.json"),
        ],
        "mcp_root": "mcpServers",
        "server_key": "dify-memory",
        "env_field": "env",
        "command_format": "split",
    },
    "gemini": {
        "paths": [
            os.path.expanduser("~/.gemini/settings.json"),
        ],
        "mcp_root": "mcpServers",
        "server_key": "dify-memory",
        "env_field": "env",
        "command_format": "split",
    },
    "windsurf": {
        "paths": [
            os.path.expanduser("~/.windsurf/mcp.json"),
            os.path.expanduser("~/.codeium/windsurf/mcp_config.json"),
        ],
        "mcp_root": "mcpServers",
        "server_key": "dify-memory",
        "env_field": "env",
        "command_format": "split",
    },
    "openclaw": {
        "paths": [
            os.path.expanduser("~/.openclaw/openclaw.json"),
        ],
        "mcp_root": "mcp",
        "server_key": "dify-memory",
        "env_field": "env",
        "command_format": "split",
    },
}


def detect_installed_tools():
    """Detect which AI tools are installed by checking for their config files.
    Returns list of (tool_name, config_path) tuples.
    """
    found = []
    for tool_name, tool_info in TOOL_CONFIGS.items():
        for path in tool_info["paths"]:
            if os.path.isfile(path):
                found.append((tool_name, path))
                break  # take first match per tool
    return found


def resolve_mcporter_config_path(server_path):
    """Resolve the persistent MCPorter config path for a registered server."""
    configured = os.environ.get("MCPORTER_CONFIG")
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(server_path)))
    return os.path.join(project_dir, "config", "mcporter.json")


def build_persistent_registration(python_path, server_path):
    """Build harness-neutral MCP registration metadata without secrets."""
    return {
        "server_key": "dify-memory",
        "description": "Dify knowledge-base persistent memory MCP server",
        "transport": "stdio",
        "persistent": True,
        "command": python_path,
        "args": [server_path],
        "mcporter_config": resolve_mcporter_config_path(server_path),
        "env_keys": [
            "MCPORTER_CONFIG",
            "DIFY_API_KEY",
            "DIFY_DATASET_ID",
            "DIFY_DOCUMENT_ID",
            "DIFY_PERMANENT_DOCUMENT_ID",
            "DIFY_SESSION_INDEX_DOCUMENT_ID",
            "DIFY_SESSIONS_DATASET_ID",
        ],
    }


def patch_tool_config(tool_name, config_path, python_path, server_path, env_vars):
    """Persist an MCP server registration in a harness-owned config file.

    This writes the host's durable config directly and does not depend on a
    temporary registration process.
    Returns True on success.
    """
    tool_info = TOOL_CONFIGS[tool_name]

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        config = {}
    except json.JSONDecodeError as exc:
        log(
            f"  ! {tool_name}: {config_path} is not strict JSON; "
            f"refusing to rewrite it ({exc})"
        )
        return False

    mcp_root = tool_info["mcp_root"]
    server_key = tool_info["server_key"]
    env_field = tool_info["env_field"]
    registration_env = dict(env_vars)
    registration_env.setdefault(
        "MCPORTER_CONFIG", resolve_mcporter_config_path(server_path)
    )

    if mcp_root not in config:
        config[mcp_root] = {}

    # Build server entry based on tool format
    if tool_info["command_format"] == "array":
        # OpenCode style: "command": ["python", "server.py"]
        entry = {
            "command": [python_path, server_path],
            "enabled": True,
            "type": "local",
            env_field: registration_env,
        }
    else:
        # Claude/Cursor/Windsurf style: "command": "python", "args": ["server.py"]
        entry = {
            "command": python_path,
            "args": [server_path],
            env_field: registration_env,
        }

    if tool_name == "opencode":
        config[mcp_root].pop("memory", None)
    config[mcp_root][server_key] = entry

    _write_json_atomic(config_path, config)

    return True


def install_opencode_memory_plugin(opencode_config_path=None):
    """Install and register the optional OpenCode memory injection plugin."""
    if not os.path.isfile(PLUGIN_SOURCE_PATH):
        return {"installed": False, "reason": "plugin source missing"}

    os.makedirs(os.path.dirname(OPENCODE_PLUGIN_PATH), exist_ok=True)
    shutil.copy2(PLUGIN_SOURCE_PATH, OPENCODE_PLUGIN_PATH)

    config_created = False
    if os.path.isfile(PLUGIN_CONFIG_SOURCE_PATH) and not os.path.exists(
        OPENCODE_PLUGIN_CONFIG_PATH
    ):
        shutil.copy2(PLUGIN_CONFIG_SOURCE_PATH, OPENCODE_PLUGIN_CONFIG_PATH)
        config_created = True

    registered = False
    config_path = opencode_config_path or find_opencode_config()
    if config_path and os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            plugins = config.get("plugin", [])
            if not isinstance(plugins, list):
                plugins = []
            if OPENCODE_PLUGIN_PATH not in plugins:
                plugins.append(OPENCODE_PLUGIN_PATH)
                config["plugin"] = plugins
                config_dir = os.path.dirname(os.path.abspath(config_path))
                tmp_path = os.path.join(
                    config_dir, os.path.basename(config_path) + ".tmp"
                )
                try:
                    with open(tmp_path, "w", encoding="utf-8") as f:
                        json.dump(config, f, indent=2, ensure_ascii=False)
                    os.replace(tmp_path, config_path)
                except Exception:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    raise
            registered = True
        except Exception as e:
            return {
                "installed": True,
                "registered": False,
                "config_created": config_created,
                "error": str(e),
            }

    return {
        "installed": True,
        "registered": registered,
        "config_created": config_created,
        "path": OPENCODE_PLUGIN_PATH,
    }


# ─── Step: Check existing installation ───────────────────────────────────────


def check_existing_installation(opencode_config_path):
    """Check if dify-memory-mcp is already installed. Returns details dict or None."""
    checks = {}

    # Check 1: Skill directory exists with SKILL.md
    skill_dir = os.path.expanduser("~/.config/opencode/skills/dify-memory-mcp")
    checks["skill_installed"] = os.path.isfile(os.path.join(skill_dir, "SKILL.md"))

    # Check 2: server.py exists in skill
    server_in_skill = os.path.join(skill_dir, "scripts", "server.py")
    checks["server_exists"] = os.path.isfile(server_in_skill)

    # Check 3: Persistent registration entry exists in opencode.json.
    checks["mcp_registered"] = False
    if opencode_config_path and os.path.isfile(opencode_config_path):
        try:
            with open(opencode_config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            mcp = config.get("mcp", {})
            memory_key = next(
                (
                    key
                    for key in ("dify-memory", "memory")
                    if isinstance(mcp.get(key), dict) and mcp[key].get("enabled")
                ),
                None,
            )
            if memory_key:
                checks["mcp_registered"] = True
                env_state = get_memory_env_state(config)
                checks["mcp_env"] = mcp[memory_key].get(env_state["field"], {})
                checks["mcp_env_field"] = env_state["field"]
                checks["missing_environment"] = env_state["missing"]
                checks["needs_configuration"] = not env_state["ready"]
        except Exception:
            pass

    # Check 4: AGENT_RULES in AGENTS.md
    agents_md = os.path.expanduser("~/.config/opencode/AGENTS.md")
    checks["rules_appended"] = False
    if os.path.isfile(agents_md):
        try:
            with open(agents_md, "r", encoding="utf-8") as f:
                checks["rules_appended"] = "# Agent Memory Rules" in f.read()
        except Exception:
            pass

    # Consider installed if MCP is registered and server exists
    if checks["mcp_registered"] and checks["server_exists"]:
        return checks
    return None


# ─── Step: Install pip dependencies ─────────────────────────────────────────


def ensure_dependencies():
    """Install mcp and httpx if not already available."""
    missing = []
    try:
        import mcp  # noqa: F401
    except ImportError:
        missing.append("mcp")
    try:
        import httpx  # noqa: F401
    except ImportError:
        missing.append("httpx")
    if missing:
        log(f"Installing dependencies: {', '.join(missing)}")
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing, "-q"])


# ─── Step: Install Skill files ──────────────────────────────────────────────


def install_skill_files():
    """Copy Skill files to ~/.config/opencode/skills/dify-memory-mcp/"""
    skills_dir = os.path.expanduser("~/.config/opencode/skills/dify-memory-mcp")

    # Source files from project directory
    files_to_copy = {
        "SKILL.md": os.path.join(PROJECT_DIR, "SKILL.md"),
        "AGENT_RULES.md": os.path.join(PROJECT_DIR, "AGENT_RULES.md"),
        "README.md": os.path.join(PROJECT_DIR, "README.md"),
        "CHANGELOG.md": os.path.join(PROJECT_DIR, "CHANGELOG.md"),
    }
    scripts_to_copy = {
        "scripts/server.py": os.path.join(SCRIPT_DIR, "server.py"),
        "scripts/install.py": os.path.join(SCRIPT_DIR, "install.py"),
        "scripts/setup.py": os.path.join(SCRIPT_DIR, "setup.py"),
        "scripts/session_store.py": os.path.join(SCRIPT_DIR, "session_store.py"),
        "scripts/memory_model.py": os.path.join(SCRIPT_DIR, "memory_model.py"),
        "scripts/memory_service.py": os.path.join(SCRIPT_DIR, "memory_service.py"),
        "scripts/privacy.py": os.path.join(SCRIPT_DIR, "privacy.py"),
        "scripts/local_store.py": os.path.join(SCRIPT_DIR, "local_store.py"),
        "scripts/observability.py": os.path.join(SCRIPT_DIR, "observability.py"),
        "scripts/openclaw_session.py": os.path.join(SCRIPT_DIR, "openclaw_session.py"),
        "scripts/runtime_config.py": os.path.join(SCRIPT_DIR, "runtime_config.py"),
        "scripts/flush_offline_queue.py": os.path.join(SCRIPT_DIR, "flush_offline_queue.py"),
        "scripts/.env.example": os.path.join(SCRIPT_DIR, ".env.example"),
    }
    plugins_to_copy = {
        "plugins/dify-memory-inject.js": PLUGIN_SOURCE_PATH,
        "config/dify-memory-plugin.jsonc": PLUGIN_CONFIG_SOURCE_PATH,
        "config/openclaw-linux.json5.example": os.path.join(
            PROJECT_DIR, "config", "openclaw-linux.json5.example"
        ),
        "config/mcporter.json": os.path.join(PROJECT_DIR, "config", "mcporter.json"),
    }
    references_to_copy = {
        os.path.join("references", name): os.path.join(
            PROJECT_DIR, "references", name
        )
        for name in (
            "installation.md",
            "mcp-registration.md",
            "api-reference.md",
            "architecture.md",
            "environment.md",
            "troubleshooting.md",
        )
    }

    # Create directories
    os.makedirs(skills_dir, exist_ok=True)
    os.makedirs(os.path.join(skills_dir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(skills_dir, "plugins"), exist_ok=True)
    os.makedirs(os.path.join(skills_dir, "config"), exist_ok=True)
    os.makedirs(os.path.join(skills_dir, "references"), exist_ok=True)

    copied = []
    for dest_rel, src in {
        **files_to_copy,
        **scripts_to_copy,
        **plugins_to_copy,
        **references_to_copy,
    }.items():
        if os.path.isfile(src):
            dest = os.path.join(skills_dir, dest_rel)
            shutil.copy2(src, dest)
            copied.append(dest_rel)

    log(f"Skill installed to: {skills_dir} ({len(copied)} files)")
    return skills_dir


# ─── Step: Append AGENT_RULES to AGENTS.md ──────────────────────────────────


def append_agent_rules():
    """Append or refresh memory rules in global AGENTS.md."""
    agents_md = os.path.expanduser("~/.config/opencode/AGENTS.md")
    rules_file = os.path.join(PROJECT_DIR, "AGENT_RULES.md")
    marker = "# Agent Memory Rules"
    begin_marker = "<!-- BEGIN dify-memory-mcp AGENT_RULES -->"
    end_marker = "<!-- END dify-memory-mcp AGENT_RULES -->"

    if not os.path.isfile(rules_file):
        return False

    with open(rules_file, "r", encoding="utf-8") as f:
        rules_content = f.read()

    managed_block = f"\n\n---\n\n{begin_marker}\n\n{rules_content}\n\n{end_marker}\n"

    existing = ""
    if os.path.isfile(agents_md):
        with open(agents_md, "r", encoding="utf-8") as f:
            existing = f.read()

    if begin_marker in existing and end_marker in existing:
        start = existing.index(begin_marker)
        end = existing.index(end_marker) + len(end_marker)
        updated = (
            existing[:start].rstrip() + managed_block + existing[end:].lstrip("\n")
        )
        action = "updated"
    elif marker in existing:
        start = existing.index(marker)
        updated = existing[:start].rstrip() + managed_block
        action = "updated"
    else:
        updated = existing.rstrip() + managed_block
        action = "appended"

    with open(agents_md, "w", encoding="utf-8") as f:
        f.write(updated)

    log(f"Agent memory rules {action} in: {agents_md}")
    return True


# ─── Main ───────────────────────────────────────────────────────────────────


def open_browser(url):
    """Try to open URL in default browser. Returns True if likely succeeded."""
    import webbrowser
    import subprocess

    # Method 1: platform-specific commands (most reliable in subprocess contexts)
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["cmd", "/c", "start", "", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
        else:
            subprocess.Popen(
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
    except Exception:
        pass

    # Method 2: webbrowser module fallback
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="dify-memory-mcp one-command installer"
    )
    parser.add_argument(
        "--api-key",
        required=False,
        help="Dify API Key (dataset-XXXX). If omitted, opens browser for user to obtain one.",
    )
    parser.add_argument(
        "--opencode-config", help="Path to opencode.json (auto-detected)"
    )
    parser.add_argument("--python-path", help="Python executable path (auto-detected)")
    parser.add_argument(
        "--skip-tool-patch",
        "--skip-opencode-patch",
        dest="skip_tool_patch",
        action="store_true",
        help="Don't patch any detected agent config files",
    )
    parser.add_argument(
        "--skip-skill-install", action="store_true", help="Don't copy skill files"
    )
    parser.add_argument(
        "--skip-rules-append", action="store_true", help="Don't append AGENT_RULES"
    )
    args = parser.parse_args()

    api_key = args.api_key
    python_path = args.python_path or find_stable_python()
    opencode_config = args.opencode_config or find_opencode_config()

    # Step 0: Check if already installed — if so, update skill files and exit
    already_installed = check_existing_installation(opencode_config)
    if already_installed and not api_key:
        # Update mode: refresh skill files without re-creating KB or config
        plugin_result = {"installed": False, "reason": "skipped"}
        migrated_legacy_env = False
        if (
            opencode_config
            and already_installed.get("mcp_env_field") == "env"
            and not already_installed.get("missing_environment")
        ):
            migrated_legacy_env = migrate_legacy_memory_env(opencode_config)
            if migrated_legacy_env:
                already_installed["mcp_env_field"] = "environment"
                log("Migrated legacy mcp.memory.env to mcp.memory.environment")

        if not args.skip_skill_install:
            install_skill_files()
            plugin_result = install_opencode_memory_plugin(opencode_config)
            log("Skill files updated to latest version.")

        # Check if permanent doc is missing (upgraded from older version)
        needs_upgrade = False
        mcp_env = already_installed.get("mcp_env", {})
        if not mcp_env.get("DIFY_PERMANENT_DOCUMENT_ID"):
            needs_upgrade = True
            log(
                "WARNING: Long-term memory not configured. Run with --api-key to enable permanent memory."
            )

        needs_configuration = bool(already_installed.get("needs_configuration"))
        if needs_configuration:
            missing = ", ".join(already_installed.get("missing_environment", []))
            log(
                "WARNING: OpenCode memory configuration is incomplete. "
                f"Missing: {missing}. Re-run install.py --api-key <KEY> to repair it."
            )

        # Check if Session Archive is not yet configured (v1.4.0+ optional feature)
        needs_session_setup = False
        if not mcp_env.get("DIFY_SESSION_INDEX_DOCUMENT_ID") or not mcp_env.get(
            "DIFY_SESSIONS_DATASET_ID"
        ):
            needs_session_setup = True

        log("")
        log("=" * 60)
        log("  ✅ Update Complete!")
        log("=" * 60)
        log("")
        log("  Skill 文件已更新到最新版本。")
        if needs_upgrade:
            log("  ⚠️  长期记忆未配置。重新运行 install.py --api-key <KEY> 启用。")
        if needs_configuration:
            log(
                "  ⚠️  OpenCode 记忆环境变量未配置完整。"
                "重新运行 install.py --api-key <KEY> 修复。"
            )
        if needs_session_setup:
            log(
                f"  💡 可选：重新运行 install.py --api-key <KEY> 启用 Session Archive（跨工具会话共享）"
            )
        log("")
        log("  重启 AI 工具后生效。")
        log("")

        messages = [
            "dify-memory-mcp skill files updated. Restart to activate new version."
        ]
        if needs_upgrade:
            messages.append("Run again with --api-key to enable long-term memory.")
        if needs_configuration:
            messages.append(
                "Run again with --api-key to repair the OpenCode memory environment."
            )
        if needs_session_setup:
            messages.append(
                "Optional: re-run install.py --api-key <KEY> to enable Session Archive (cross-tool session sharing)."
            )

        print(
            json.dumps(
                {
                    "success": True,
                    "already_installed": True,
                    "updated": True,
                    "opencode_memory_plugin": plugin_result,
                    "migrated_legacy_environment": migrated_legacy_env,
                    "needs_configuration": needs_configuration,
                    "missing_environment": already_installed.get(
                        "missing_environment", []
                    ),
                    "needs_permanent_upgrade": needs_upgrade,
                    "needs_session_setup": needs_session_setup,
                    "message": " ".join(messages),
                    **already_installed,
                }
            )
        )
        sys.exit(0)

    # If no API key provided, open browser and prompt
    if not api_key:
        dify_url = "http://127.0.0.1/datasets?category=api"
        browser_opened = open_browser(dify_url)
        if browser_opened:
            log(f"Browser opened: {dify_url}")
        else:
            log(f"Could not open browser. Please manually visit: {dify_url}")
        log("Please go to Settings -> API, copy your API Key (format: dataset-XXXX)")
        print(
            json.dumps(
                {
                    "success": False,
                    "needs_api_key": True,
                    "browser_opened": browser_opened,
                    "url": dify_url,
                    "instruction": "请用户打开上方 URL，进入页面后点击右上角「设置」→「API」，复制 API Key（格式：dataset-XXXX）粘贴给 AI。",
                    "next_step": f'python "{os.path.abspath(__file__)}" --api-key <用户粘贴的KEY>',
                }
            )
        )
        sys.exit(0)

    # Validate API Key format
    if not api_key.startswith("dataset-"):
        print(
            json.dumps(
                {"success": False, "error": "API Key must start with 'dataset-'"}
            )
        )
        sys.exit(1)

    log("Starting installation...")

    # Step 1: Dependencies
    ensure_dependencies()

    # Step 2: Verify API Key
    log("Verifying API Key...")
    result = api_request("GET", "/datasets?page=1&limit=1", api_key)
    if "error" in result:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": f"API Key verification failed: {result['error']}",
                }
            )
        )
        sys.exit(1)
    log("API Key valid")

    # Step 3: Create or find knowledge base
    log("Setting up knowledge base...")
    existing = api_request(
        "GET", "/datasets?page=1&limit=100&keyword=opencode-agent-memory", api_key
    )
    dataset_id = None
    document_id = None
    permanent_document_id = None

    if existing.get("data"):
        for kb in existing["data"]:
            if kb["name"] == "opencode-agent-memory":
                dataset_id = kb["id"]
                docs = api_request(
                    "GET", f"/datasets/{dataset_id}/documents?page=1&limit=20", api_key
                )
                if docs.get("data"):
                    for doc in docs["data"]:
                        if doc["name"] == "agent-memories":
                            document_id = doc["id"]
                        elif doc["name"] == "agent-memories-permanent":
                            permanent_document_id = doc["id"]
                break

    if not dataset_id:
        kb = api_request(
            "POST",
            "/datasets",
            api_key,
            {
                "name": "opencode-agent-memory",
                "description": "AI Agent persistent memory store. Contains short-term (decayable) and long-term (permanent) memory documents.",
                "permission": "only_me",
                "indexing_technique": "high_quality",
            },
        )
        if "error" in kb:
            print(
                json.dumps(
                    {"success": False, "error": f"Failed to create KB: {kb['error']}"}
                )
            )
            sys.exit(1)
        dataset_id = kb["id"]
        log(f"Knowledge base created: {dataset_id}")
    else:
        log(f"Knowledge base found: {dataset_id}")

    # Create short-term memory document
    if not document_id:
        doc = api_request(
            "POST",
            f"/datasets/{dataset_id}/document/create-by-text",
            api_key,
            {
                "name": "agent-memories",
                "text": "Short-term memory container. Segments here are subject to decay (auto-deletion when hit_count=0 and older than threshold).",
                "indexing_technique": "high_quality",
                "doc_form": "text_model",
                "doc_language": "Chinese",
                "process_rule": {"mode": "automatic"},
            },
        )
        if "error" in doc:
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": f"Failed to create document: {doc['error']}",
                    }
                )
            )
            sys.exit(1)
        document_id = doc["document"]["id"]
        batch = doc["batch"]
        log("Waiting for indexing...")
        for _ in range(15):
            time.sleep(1)
            status = api_request(
                "GET",
                f"/datasets/{dataset_id}/documents/{batch}/indexing-status",
                api_key,
            )
            if (
                status.get("data")
                and status["data"][0].get("indexing_status") == "completed"
            ):
                break
        log(f"Short-term document created: {document_id}")
    else:
        log(f"Short-term document found: {document_id}")

    # Create long-term (permanent) memory document
    if not permanent_document_id:
        doc = api_request(
            "POST",
            f"/datasets/{dataset_id}/document/create-by-text",
            api_key,
            {
                "name": "agent-memories-permanent",
                "text": "Long-term memory container. Segments here are NEVER deleted by decay. Use for critical preferences, decisions, and facts that must persist indefinitely.",
                "indexing_technique": "high_quality",
                "doc_form": "text_model",
                "doc_language": "Chinese",
                "process_rule": {"mode": "automatic"},
            },
        )
        if "error" in doc:
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": f"Failed to create permanent document: {doc['error']}",
                    }
                )
            )
            sys.exit(1)
        permanent_document_id = doc["document"]["id"]
        batch = doc["batch"]
        log("Waiting for permanent doc indexing...")
        for _ in range(15):
            time.sleep(1)
            status = api_request(
                "GET",
                f"/datasets/{dataset_id}/documents/{batch}/indexing-status",
                api_key,
            )
            if (
                status.get("data")
                and status["data"][0].get("indexing_status") == "completed"
            ):
                break
        log(f"Permanent document created: {permanent_document_id}")
    else:
        log(f"Permanent document found: {permanent_document_id}")

    # Step 3b: Create session-indexes document (in same memory dataset)
    log("Setting up Session Archive...")
    session_index_document_id = None
    docs = api_request(
        "GET", f"/datasets/{dataset_id}/documents?page=1&limit=50", api_key
    )
    if docs.get("data"):
        for doc in docs["data"]:
            if doc["name"] == "session-indexes":
                session_index_document_id = doc["id"]
                break

    if not session_index_document_id:
        doc = api_request(
            "POST",
            f"/datasets/{dataset_id}/document/create-by-text",
            api_key,
            {
                "name": "session-indexes",
                "text": "Session index container. Each segment is a lightweight index entry pointing to a full session document.",
                "indexing_technique": "high_quality",
                "doc_form": "text_model",
                "doc_language": "Chinese",
                "process_rule": {"mode": "automatic"},
            },
        )
        if "error" not in doc:
            session_index_document_id = doc["document"]["id"]
            batch = doc["batch"]
            for _ in range(15):
                time.sleep(1)
                status = api_request(
                    "GET",
                    f"/datasets/{dataset_id}/documents/{batch}/indexing-status",
                    api_key,
                )
                if (
                    status.get("data")
                    and status["data"][0].get("indexing_status") == "completed"
                ):
                    break
            log(f"Session index document created: {session_index_document_id}")
        else:
            log(f"WARNING: Failed to create session-indexes doc: {doc.get('error')}")
    else:
        log(f"Session index document found: {session_index_document_id}")

    # Step 3c: Create sessions knowledge base (separate dataset for full session content)
    sessions_dataset_id = None
    existing_sessions = api_request(
        "GET", "/datasets?page=1&limit=100&keyword=agent-session-archive", api_key
    )
    if existing_sessions.get("data"):
        for kb in existing_sessions["data"]:
            if kb["name"] == "agent-session-archive":
                sessions_dataset_id = kb["id"]
                break

    if not sessions_dataset_id:
        kb = api_request(
            "POST",
            "/datasets",
            api_key,
            {
                "name": "agent-session-archive",
                "description": "Full AI agent session archive for cross-tool context sharing. Each session is an independent document.",
                "permission": "only_me",
                "indexing_technique": "high_quality",
            },
        )
        if "error" not in kb:
            sessions_dataset_id = kb["id"]
            log(f"Sessions dataset created: {sessions_dataset_id}")
        else:
            log(f"WARNING: Failed to create sessions dataset: {kb.get('error')}")
    else:
        log(f"Sessions dataset found: {sessions_dataset_id}")

    # Step 4: Install Skill files
    skill_dir = None
    if not args.skip_skill_install:
        skill_dir = install_skill_files()

    # Step 5: Detect and patch all installed AI tools
    skill_server = (
        os.path.join(skill_dir, "scripts", "server.py") if skill_dir else ""
    )
    installed_server = next(
        (
            path
            for path in (UNIFIED_SERVER_PATH, skill_server, SERVER_PATH)
            if path and os.path.isfile(path)
        ),
        SERVER_PATH,
    )

    env_config = {
        "DIFY_API_KEY": api_key,
        "DIFY_DATASET_ID": dataset_id,
        "DIFY_DOCUMENT_ID": document_id,
        "DIFY_PERMANENT_DOCUMENT_ID": permanent_document_id,
        "MCPORTER_CONFIG": resolve_mcporter_config_path(installed_server),
    }
    if session_index_document_id:
        env_config["DIFY_SESSION_INDEX_DOCUMENT_ID"] = session_index_document_id
    if sessions_dataset_id:
        env_config["DIFY_SESSIONS_DATASET_ID"] = sessions_dataset_id

    patched_tools = []
    registration_config_paths = []
    if not args.skip_tool_patch:
        detected_tools = detect_installed_tools()
        if detected_tools:
            log(
                f"Detected {len(detected_tools)} AI tool(s): {', '.join(t[0] for t in detected_tools)}"
            )
            for tool_name, config_path in detected_tools:
                try:
                    success = patch_tool_config(
                        tool_name,
                        config_path,
                        python_path,
                        installed_server,
                        env_config,
                    )
                    if success:
                        patched_tools.append(tool_name)
                        registration_config_paths.append(config_path)
                        log(f"  ✓ {tool_name}: {config_path}")
                except Exception as e:
                    log(f"  ✗ {tool_name}: {e}")
        else:
            log("No AI tools detected. You can manually configure MCP server.")

    patched = len(patched_tools) > 0

    # Step 6: Append AGENT_RULES
    rules_appended = False
    if not args.skip_rules_append:
        rules_appended = append_agent_rules()

    # Step 6b: Install optional OpenCode memory injection plugin
    plugin_result = {"installed": False, "reason": "skipped"}
    if not args.skip_skill_install:
        plugin_result = install_opencode_memory_plugin(opencode_config)
        if plugin_result.get("installed"):
            if plugin_result.get("registered"):
                log("OpenCode memory injection plugin installed and registered")
            else:
                log(
                    "OpenCode memory injection plugin installed; registration skipped or failed"
                )

    # Step 7: Write .env (backup)
    env_path = os.path.join(SCRIPT_DIR, ".env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"DIFY_API_KEY={api_key}\n")
        f.write(f"DIFY_DATASET_ID={dataset_id}\n")
        f.write(f"DIFY_DOCUMENT_ID={document_id}\n")
        f.write(f"DIFY_PERMANENT_DOCUMENT_ID={permanent_document_id}\n")
        f.write(f"DIFY_SESSION_INDEX_DOCUMENT_ID={session_index_document_id or ''}\n")
        f.write(f"DIFY_SESSIONS_DATASET_ID={sessions_dataset_id or ''}\n")
        f.write(f"DIFY_BASE_URL={DIFY_BASE_URL}\n")
        f.write(f"MEMORY_DECAY_DAYS=30\n")
        f.write("DIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS=1800\n")

    # Output result
    session_ok = bool(session_index_document_id and sessions_dataset_id)
    log("")
    log("=" * 60)
    log("  ✅ Installation Complete!")
    log("=" * 60)
    log("")
    log("  已配置全部 15 个工具：")
    log("")
    log("  记忆管理（7 个）：")
    log("    add_memory / search_memory / update_memory / delete_memory")
    log("    list_memories / decay_memories / promote_memory")
    log("")
    log("  会话共享（5 个）：")
    log("    save_session / list_sessions / search_session")
    log("    get_session_detail / delete_session")
    if not session_ok:
        log("    ⚠️  会话共享配置未完成（不影响记忆功能，工具不会注册）")
    log("")
    log("─── 已配置的工具 ───")
    log("")
    if patched_tools:
        for t in patched_tools:
            log(f"  ✓ {t}")
    else:
        log("  （未检测到已安装的 AI 工具，请手动配置）")
    log("")
    log("─── 使用说明 ───")
    log("")
    log("  记忆：Agent 自动管理，无需手动操作")
    log("  会话共享：对 AI 说「保存当前会话」首次存档，之后随记忆自动同步")
    log("")
    log("─── 下一步 ───")
    log("")
    log("  重启已配置的 AI 工具即可使用")
    log("")

    output = {
        "success": True,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "permanent_document_id": permanent_document_id,
        "session_index_document_id": session_index_document_id,
        "sessions_dataset_id": sessions_dataset_id,
        "session_archive_configured": session_ok,
        "server_path": installed_server,
        "python_path": python_path,
        "skill_dir": skill_dir,
        "patched_tools": patched_tools,
        "mcp_registration": {
            **build_persistent_registration(python_path, installed_server),
            "status": "persisted" if patched_tools else "manual_required",
            "config_paths": registration_config_paths,
        },
        "opencode_memory_plugin": plugin_result,
        "rules_appended": rules_appended,
        "tools_available": 15 if session_ok else 10,
        "message": "dify-memory-mcp is ready. Restart to activate.",
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
