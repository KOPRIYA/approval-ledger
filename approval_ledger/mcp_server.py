"""Read-only local stdio MCP adapter. No remote OAuth or write-tool exposure."""
import os
from .domain import Principal
from .store import Store


def main():
    from mcp.server.fastmcp import FastMCP
    store = Store(os.environ.get("LEDGER_DB", "local/ledger.db"))
    # Stdio is launched in a trusted local operator context. Never accept a tenant
    # or principal identity from tool arguments; remote transport needs real auth.
    principal = Principal(os.environ.get("LEDGER_SUBJECT", "local-reader"),
                          os.environ.get("LEDGER_TENANT", "demo"), frozenset({"evidence:read", "run:read"}))
    server = FastMCP("approval-ledger-readonly")

    @server.tool()
    def search_evidence(query: str) -> list:
        """Search evidence visible to the bound local tenant."""
        return store.retrieve(principal, query)

    @server.tool()
    def get_run(run_id: str) -> dict:
        """Read a proposal and its current status; does not approve or execute it."""
        return store.get(principal, run_id)

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
