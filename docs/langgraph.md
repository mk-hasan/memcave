# LangGraph

Install the extra and run the example on the fake model. No API key is required.

```bash
pip install -e ".[langgraph]"
python examples/langgraph_agent.py
```

`memcave.integrations.langgraph.tools` exposes five tools: `search_memory`, `fetch_memory`, `add_memory`, `update_memory`, and `delete_memory`. The registry is fixed. Text stored in memory is not parsed into tool names. `on_session_start` injects the compact index unless `memory_opt_out` is set. The example graph calls `MemoryManager` and the fake model. It does not import a SQLAlchemy session or an adapter engine.

Tests live in `tests/integration/test_langgraph.py` and skip with a clear reason when LangGraph is not installed.
