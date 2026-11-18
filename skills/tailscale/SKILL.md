---
name: tailscale
description: Connect local models and clients to OpenBeast over Tailscale, select a rig and device key, or diagnose mobile, relay, DNS and offline LAN access.
allowed_tools: [bash, read_file, platform_docs]
recommends_subagent: false
prompt_index: false
---

# Reach DeepSeek and other local models over Tailscale

Read `skills/tailscale/references/guide.md` in the checkout for browser, API, OpenCode, PowerShell, native SSH and offline mirror commands. The slim client checkout also carries this file. If the tool process runs elsewhere, use the copy in the installed skill's `references/` directory.

1. Establish where the client runs, which rig it selected, and which model it needs. On a configured OpenBeast client, `openbeast-client rigs` and `openbeast-client status` show the registered and active rigs. For DeepSeek on the Spark pair, use the Spark 1 endpoint and its device enrollment.
2. On a connected desktop or phone, sign into the authorized tailnet. The browser uses the rig's HTTPS Open WebUI URL and WebUI login. An OpenAI-compatible app uses the authenticated `https://<rig-fqdn>:8443/v1` base URL. Select the model returned by that rig's `/v1/models`. Use its device credential from private storage.
3. Diagnose the layer that failed. Test the peer with `tailscale ping` by saved overlay IP, then the published service by full DNS name, then the authenticated catalog. A direct or relayed ping can succeed while the inference gate returns 401. Verify the actual provider, rig name and key pairing before touching the runtime.
4. For a complete validation, submit one short chat request with the returned model ID. Record the service, status, model and response. Keep keys out of logs. If only read-only status was requested, stop at the model catalog.
5. If WAN or control connectivity is lost, preserve any working Tailscale state. For physically reachable machines, use the guide's native LAN SSH tunnel to the rig's loopback beast-gate. Continue authenticating with the rig's device key. Independent management during a sustained outage requires a prepared local control plane.

For a platform-specific feature or version claim, use `platform_docs(query="connection types", source="tailscale")`, then read the returned ID. With shell tools, `scripts/docs.sh search "connection types" --source tailscale` and `scripts/docs.sh read <id>` work offline. Cite the publisher URL and fetch date.
