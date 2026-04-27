# TODO

## MCP Integration (in progress)

- [ ] Debug Open WebUI MCP connection failure — server responds on port 3001 but Open WebUI can't connect. Likely a transport compatibility issue (streamable-http vs SSE). May need to switch MCP server to SSE transport or investigate Open WebUI's expected MCP format.
- [ ] Verify OpenCode MCP stdio transport works (untested)
- [ ] Once MCP is working, test tool execution end-to-end from both Open WebUI and OpenCode

## Testing & Validation

- [ ] Test `./agent.sh` end-to-end against a real task with the server running
- [ ] Validate 35B-A3B actual KV cache allocation (estimated ~11 KB/token, never measured)
- [ ] Confirm Open WebUI conversation persistence survives `./stop.sh` + `./start.sh` cycle

## Cleanup

- [ ] `git init` the repo and make first commit
