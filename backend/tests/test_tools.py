from app.tools import TOOL_REGISTRY, resolve_agent_tools

EXPECTED = {
    "fetch_url", "http_request", "get_current_datetime", "web_search",
    "execute_code", "read_file", "write_file", "edit_file",
    "delete_file", "list_files", "search_files",
}


def test_all_builtins_registered():
    assert EXPECTED <= set(TOOL_REGISTRY)


def test_default_policy_matches_spec():
    specs, policy = resolve_agent_tools(list(EXPECTED), {})
    assert policy["execute_code"] == "ask"
    assert policy["write_file"] == "ask"
    assert policy["http_request"] == "ask"
    assert policy["fetch_url"] == "auto"
    assert policy["read_file"] == "auto"
    assert policy["get_current_datetime"] == "auto"


def test_tool_policy_override():
    _, policy = resolve_agent_tools(["execute_code"], {"execute_code": "auto"})
    assert policy["execute_code"] == "auto"


def test_unattended_drops_ask_tools_unless_allowed():
    specs, policy = resolve_agent_tools(
        list(EXPECTED), {}, unattended=True, unattended_allowed=["execute_code"]
    )
    names = {s.name for s in specs}
    assert "write_file" not in names          # ask -> dropped
    assert "execute_code" in names            # explicitly allowed
    assert policy["execute_code"] == "auto"   # forced auto for the run
    assert "read_file" in names               # auto -> kept
