# camt053-mcp examples

Runnable, self-contained examples for the camt053 MCP server. Run any of them
from the repository root:

```sh
python examples/<name>.py
```

| Example | Demonstrates |
|---------|--------------|
| [`mcp_tools.py`](mcp_tools.py) | Calling the server's nine MCP tools in-process — `list_message_types`, `filter_entries`, and the headline `generate_reversal` |

The examples import directly from `camt053_mcp.server`, so install this package
(and the core `camt053` library it depends on) first:

```sh
pip install camt053-mcp   # Python 3.10+
```

> While the core `camt053` library is not yet on PyPI, install it from source
> first: `pip install "git+https://github.com/sebastienrousseau/camt053.git"`.
