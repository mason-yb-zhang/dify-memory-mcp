"""Regression test: env pollution must not break dify-memory server.

Simulates the real-world failure where an sk- format gateway key occupies
DIFY_API_KEY in the process environment. The server must fall back to the
dataset- key in .env (search_memory must succeed, no HTTP 401 leaks).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PYTHON = sys.executable
SERVER = str(Path(__file__).parents[2] / "scripts" / "server.py")


def polluted_env() -> dict:
    e = os.environ.copy()
    e["DIFY_API_KEY"] = "sk-" + "invalid" + "-gateway-probe-" + ("0" * 16)
    for k in [k for k in list(e) if k.startswith("DIFY_") and k != "DIFY_API_KEY"]:
        del e[k]
    return e


def start(env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )


def send(proc: subprocess.Popen, obj: dict) -> None:
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def recv(proc: subprocess.Popen, want_id: int, timeout: float = 45.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return {"__server_exited__": proc.returncode, "stderr": proc.stderr.read()}
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == want_id:
            return msg
    return {"__timeout__": True}


def handshake(proc: subprocess.Popen):
    send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pollution-test", "version": "0.0.1"},
            },
        },
    )
    init = recv(proc, 1)
    if "result" not in init:
        return init
    send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return init


def main() -> int:
    p = start(polluted_env())
    init = handshake(p)
    if "result" not in init:
        print("BOOT FAILED:", json.dumps(init, ensure_ascii=False)[:300])
        return 1
    send(
        p,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "search_memory",
                "arguments": {"query": "probe", "top_k": 1},
            },
        },
    )
    resp = recv(p, 2)
    p.kill()
    if "result" not in resp:
        print("CALL FAILED:", json.dumps(resp, ensure_ascii=False)[:300])
        return 1
    text = resp["result"]["content"][0]["text"]
    failed = ("401" in text) or ("unauthorized" in text.lower())
    print("RESPONSE:", text[:200].replace("\n", " "))
    print("RESULT:", "FAIL(401 leaked)" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
