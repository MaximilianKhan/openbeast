# TODO

## Completed (2026-04-27)

- [x] Debug Open WebUI MCP connection failure — replaced direct MCP HTTP with MCPO (MCP→OpenAPI proxy). Open WebUI's native MCP Streamable HTTP support has a known bug; MCPO wraps our MCP server as OpenAPI endpoints that Open WebUI consumes natively. Also set model function_calling to `native` (prompt-based default mode broke with Qwen's thinking). Both configs are now automated via `configure-webui.sh`, called by `start.sh`.
- [x] Verify OpenCode MCP stdio transport — works correctly, confirmed via initialize handshake.
- [x] Test `./agent.sh` end-to-end — completed a real task in 3 iterations with tool use.
- [x] Validate 35B-A3B actual KV cache allocation — measured ~6.3 KB/token (model=20GB, KV at 512K=3.1GB). Much better than the 11 KB/token estimate. Could safely run at 1M context.
- [x] Confirm Open WebUI persistence survives stop/start — Docker named volume persists across `docker compose down` (only `-v` flag removes it).
- [x] `git init` and first commit — done.
