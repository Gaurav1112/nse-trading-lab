"""F1 D1: Claude sessions can read the Kite MCP but every write verb is denied."""
import fnmatch
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WRITE_VERBS = ("place", "modify", "cancel", "delete", "convert", "close", "create", "set", "update", "add",
               "remove", "start", "stop", "buy", "sell", "exit")


def _settings() -> dict:
    return json.loads((REPO / ".claude" / "settings.json").read_text())  # must be valid JSON


def _kite_servers() -> list[str]:
    servers = json.loads((REPO / ".mcp.json").read_text())["mcpServers"]
    names = [n for n in servers if "kite" in n.lower() or "kite" in json.dumps(servers[n]).lower()]
    assert names, "the Kite MCP server is expected in .mcp.json"
    return names


def test_every_kite_server_denies_every_write_verb():
    deny = _settings()["permissions"]["deny"]
    assert all(isinstance(r, str) for r in deny)
    for name in _kite_servers():
        for verb in WRITE_VERBS:
            assert f"mcp__{name}__{verb}_*" in deny, f"missing deny mcp__{name}__{verb}_*"
            assert any(fnmatch.fnmatchcase(f"mcp__{name}__{verb}_order", r) for r in deny)


def test_no_allow_rule_matches_a_write_verb():
    allow = _settings().get("permissions", {}).get("allow", [])
    for name in _kite_servers():
        for verb in WRITE_VERBS:
            tool = f"mcp__{name}__{verb}_order"
            assert not any(fnmatch.fnmatchcase(tool, r) or r in (f"mcp__{name}", f"mcp__{name}__*", "*")
                           for r in allow), f"allow rule lets {tool} through"
