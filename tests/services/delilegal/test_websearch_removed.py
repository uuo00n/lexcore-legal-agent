from pathlib import Path


ROOT = Path(__file__).parents[3]


def test_websearch_implementation_files_are_removed():
    assert not (ROOT / "agent" / "tools" / "web_search.py").exists()
    assert not (ROOT / "mcp_server" / "tools" / "web_search.py").exists()


def test_agent_registries_contain_only_trusted_search_tools():
    from agent.tools import ALL_TOOLS, LEGAL_CONSULT_TOOLS

    all_names = {item.name for item in ALL_TOOLS}
    legal_names = {item.name for item in LEGAL_CONSULT_TOOLS}
    forbidden = {"web_search", "web_search_tool", "internet_search", "online_search"}

    assert not all_names.intersection(forbidden)
    assert not legal_names.intersection(forbidden)
    assert legal_names == {"retrieve_local_law_tool", "search_law_tool"}


def test_runtime_prompts_and_mcp_server_have_no_internet_fallback():
    prompt = (ROOT / "agent" / "prompts.py").read_text(encoding="utf-8").lower()
    server = (ROOT / "mcp_server" / "server.py").read_text(encoding="utf-8").lower()
    for forbidden in ("web_search", "internet_search", "online_search", "tavily", "duckduckgo"):
        assert forbidden not in prompt
        assert forbidden not in server
