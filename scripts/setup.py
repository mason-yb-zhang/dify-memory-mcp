#!/usr/bin/env python3
"""
dify-memory-mcp one-click setup script.

Usage:
    python setup.py

What it does:
    1. Prompts for your Dify API Key
    2. Creates a dedicated knowledge base on Dify (idempotent — reuses if exists)
    3. Creates a container document for memory segments
    4. Writes environment config to .env file
    5. Shows the opencode.json / claude_desktop_config.json snippet to paste
"""

import json
import os
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


def api_request(method, path, api_key, body=None):
    """Make HTTP request to Dify API."""
    url = f"{DIFY_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        if resp.status == 204:
            return {"success": True}
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"\n[ERROR] HTTP {e.code}: {error_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"\n[ERROR] Network error: {e.reason}")
        print("  Please check your network connection and ensure you are on the company intranet.")
        sys.exit(1)


def main():
    print("=" * 60)
    print("  dify-memory-mcp Setup")
    print("  Persistent AI Agent Memory via Dify Knowledge Base")
    print("=" * 60)
    print()

    # Step 1: Get API Key
    print("[Step 1/4] Dify Knowledge Base API Key")
    print("  Get yours at: http://127.0.0.1/datasets?category=api")
    print("  Format: dataset-<YOUR_API_KEY>")
    print()
    api_key = input("  Enter your Dify API Key: ").strip()
    if not api_key.startswith("dataset-"):
        print("[ERROR] API Key must start with 'dataset-'")
        sys.exit(1)

    # Verify API Key
    print("\n  Verifying API Key...")
    result = api_request("GET", "/datasets?page=1&limit=1", api_key)
    print(f"  ✓ Connected! (found {result.get('total', 0)} existing knowledge bases)")

    # Step 2: Create or find Knowledge Base (idempotent)
    print("\n[Step 2/4] Setting up knowledge base 'opencode-agent-memory'...")
    dataset_id = None
    document_id = None
    permanent_document_id = None

    # Check if already exists
    existing = api_request("GET", "/datasets?page=1&limit=100&keyword=opencode-agent-memory", api_key)
    if existing.get("data"):
        for kb in existing["data"]:
            if kb["name"] == "opencode-agent-memory":
                dataset_id = kb["id"]
                print(f"  ✓ Knowledge base already exists: {dataset_id}")
                # Check for existing documents
                docs = api_request("GET", f"/datasets/{dataset_id}/documents?page=1&limit=20", api_key)
                if docs.get("data"):
                    for doc in docs["data"]:
                        if doc["name"] == "agent-memories":
                            document_id = doc["id"]
                            print(f"  ✓ Short-term document already exists: {document_id}")
                        elif doc["name"] == "agent-memories-permanent":
                            permanent_document_id = doc["id"]
                            print(f"  ✓ Long-term document already exists: {permanent_document_id}")
                break

    if not dataset_id:
        kb = api_request("POST", "/datasets", api_key, {
            "name": "opencode-agent-memory",
            "description": "AI Agent persistent memory store. Stores facts, decisions, preferences and project knowledge as segments for cross-session retrieval.",
            "permission": "only_me",
            "indexing_technique": "high_quality",
        })
        dataset_id = kb["id"]
        print(f"  ✓ Knowledge base created: {dataset_id}")

    # Step 3: Create Container Document (if not found above)
    if not document_id:
        print("\n[Step 3/4] Creating container document...")
        doc = api_request("POST", f"/datasets/{dataset_id}/document/create-by-text", api_key, {
            "name": "agent-memories",
            "text": "This document serves as the container for AI agent memory segments.",
            "indexing_technique": "high_quality",
            "doc_form": "text_model",
            "doc_language": "Chinese",
            "process_rule": {"mode": "automatic"},
        })
        document_id = doc["document"]["id"]
        batch = doc["batch"]
        print(f"  ✓ Document created: {document_id}")

        # Wait for indexing
        print("  Waiting for indexing...", end="", flush=True)
        for _ in range(30):
            time.sleep(1)
            status = api_request("GET", f"/datasets/{dataset_id}/documents/{batch}/indexing-status", api_key)
            if status.get("data") and status["data"][0].get("indexing_status") == "completed":
                print(" done!")
                break
            print(".", end="", flush=True)
        else:
            print("\n  [WARN] Indexing still in progress, but setup can continue.")
    else:
        print("\n[Step 3/4] Short-term document already exists, skipping.")

    # Create permanent document (if not found)
    if not permanent_document_id:
        print("  Creating long-term (permanent) memory document...")
        doc = api_request("POST", f"/datasets/{dataset_id}/document/create-by-text", api_key, {
            "name": "agent-memories-permanent",
            "text": "Long-term memory container. Segments here are NEVER deleted by decay.",
            "indexing_technique": "high_quality",
            "doc_form": "text_model",
            "doc_language": "Chinese",
            "process_rule": {"mode": "automatic"},
        })
        permanent_document_id = doc["document"]["id"]
        batch = doc["batch"]
        print(f"  ✓ Permanent document created: {permanent_document_id}")
        print("  Waiting for indexing...", end="", flush=True)
        for _ in range(30):
            time.sleep(1)
            status = api_request("GET", f"/datasets/{dataset_id}/documents/{batch}/indexing-status", api_key)
            if status.get("data") and status["data"][0].get("indexing_status") == "completed":
                print(" done!")
                break
            print(".", end="", flush=True)
        else:
            print("\n  [WARN] Indexing still in progress, but setup can continue.")
    else:
        print("  Long-term document already exists, skipping.")

    # Step 4: Write .env file
    print("\n[Step 4/4] Writing configuration...")
    env_path = os.path.join(SCRIPT_DIR, ".env")
    env_content = f"""# dify-memory-mcp configuration
# Generated by setup.py on {time.strftime('%Y-%m-%d %H:%M:%S')}
DIFY_API_KEY={api_key}
DIFY_DATASET_ID={dataset_id}
DIFY_DOCUMENT_ID={document_id}
DIFY_PERMANENT_DOCUMENT_ID={permanent_document_id}
DIFY_BASE_URL={DIFY_BASE_URL}
MEMORY_DECAY_DAYS=30
DIFY_MEMORY_MCP_IDLE_TIMEOUT_SECONDS=1800
DIFY_MEMORY_ACTOR=unknown
DIFY_MEMORY_AGENT=unknown
DIFY_MEMORY_DEVICE_ID=unknown
MCPORTER_CONFIG=
DIFY_MEMORY_STATE_DIR=
OPENCLAW_SESSIONS_ROOT=
"""
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(env_content)
    print(f"  ✓ Config written to: {env_path}")

    # Show integration snippets
    server_path = os.path.join(SCRIPT_DIR, "server.py")
    python_path = sys.executable

    print("\n" + "=" * 60)
    print("  ✅ Setup Complete!")
    print("=" * 60)
    print()
    print("Add the following to your AI tool config:")
    print()
    print("─── OpenCode (opencode.json) ───")
    opencode_config = {
        "mcp": {
            "dify-memory": {
                "command": [python_path, server_path],
                "enabled": True,
                "type": "local",
                "environment": {
                    "DIFY_API_KEY": api_key,
                    "DIFY_DATASET_ID": dataset_id,
                    "DIFY_DOCUMENT_ID": document_id,
                    "DIFY_PERMANENT_DOCUMENT_ID": permanent_document_id,
                    "MCPORTER_CONFIG": "./config/mcporter.json",
                },
            }
        }
    }
    print(json.dumps(opencode_config, indent=2))
    print()
    print("─── Claude Desktop / Cursor / Windsurf / OpenClaw ───")
    claude_config = {
        "mcpServers": {
            "dify-memory": {
                "command": python_path,
                "args": [server_path],
                "env": {
                    "DIFY_API_KEY": api_key,
                    "DIFY_DATASET_ID": dataset_id,
                    "DIFY_DOCUMENT_ID": document_id,
                    "DIFY_PERMANENT_DOCUMENT_ID": permanent_document_id,
                    "MCPORTER_CONFIG": "./config/mcporter.json",
                }
            }
        }
    }
    print(json.dumps(claude_config, indent=2))
    print()
    print("─── Run standalone to test ───")
    if sys.platform == "win32":
        print(f'  set DIFY_API_KEY={api_key}')
        print(f'  set DIFY_DATASET_ID={dataset_id}')
        print(f'  set DIFY_DOCUMENT_ID={document_id}')
        print(f'  set DIFY_PERMANENT_DOCUMENT_ID={permanent_document_id}')
    else:
        print(f'  export DIFY_API_KEY={api_key}')
        print(f'  export DIFY_DATASET_ID={dataset_id}')
        print(f'  export DIFY_DOCUMENT_ID={document_id}')
        print(f'  export DIFY_PERMANENT_DOCUMENT_ID={permanent_document_id}')
    print(f'  python "{server_path}"')
    print()


if __name__ == "__main__":
    main()
