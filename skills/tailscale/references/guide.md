# Tailscale for DeepSeek and OpenBeast

DeepSeek runs on the Spark pair. A client can use the browser, OpenCode, or an OpenAI-compatible app over its authorized tailnet connection. Keep this guide and the docs mirror on the machine where the model's tools execute. A prepared native LAN SSH connection can reach beast-gate during a WAN outage.

## Connect to the running model

| Client or location | How to connect |
|---|---|
| Linux or macOS with OpenBeast client mode | Run `openbeast-client rigs` and `openbeast-client status`. To select the configured Spark pair, use `openbeast-client use spark-hydra` if that name is listed. In OpenCode, choose the model from the active `openbeast-rig` provider or the named Spark provider. Client tools execute on this client. |
| Windows, macOS, Linux, iOS or Android with a browser | Connect the Tailscale client to the authorized tailnet, then open `https://RIG_FQDN` in a browser. On the Sparks, use the published name for Spark 1. Sign in with the Open WebUI account and choose the served model. The browser workflow uses its own user login. |
| Any OpenAI-compatible app | Configure `https://RIG_FQDN:8443/v1` as the base URL, the device key enrolled on that rig as the credential, and the exact ID returned by `GET /v1/models`. Store the key in the app's protected credential field. On Windows use native HTTPS tooling or `curl.exe`, on mobile use a client that supports a custom base URL. |
| Off-site on cellular, hotel Wi-Fi or another LAN | Use the same tailnet hostname and credentials. Both ends need a viable network path. Check for a captive portal or a conflicting VPN on the client. A relay can carry successful requests with increased latency. |
| On the rig | Use the published gate as an ordinary client, or use its loopback gate with the device key. Confirm the provider actually selected by OpenCode. A local GPU alone does not select an endpoint or credential. |
| WAN unavailable, both devices on reachable physical networks | First try the existing tailnet path. If that fails, use the prepared native SSH tunnel below. A remote phone on cellular also needs a route to the site. |

`RIG_FQDN` is the rig's full MagicDNS name under the actual tailnet suffix. The published Spark endpoint in this deployment uses `spark-1.tail4109f9.ts.net`. Serving spans both Sparks, but clients send requests to Spark 1. Beast is a separate rig with its own host, model and enrollment. A client with access to both can choose either. An exit node, subnet router, public Funnel or inbound router port forward is unnecessary for routine access to an enrolled peer's published tailnet service. [MagicDNS](https://tailscale.com/docs/features/magicdns) · [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

### Validate a connected session

First check the read-only slot contract, when published. Then check the authenticated model list. These commands run on macOS/Linux. Set the hostname and the matching device key file for the selected rig. Keep shell tracing off.

```sh
RIG_FQDN=spark-1.tail4109f9.ts.net
KEY_FILE="$HOME/.config/openbeast/spark-hydra-gate.key"
curl --silent --show-error --fail --connect-timeout 5 "https://$RIG_FQDN:8444/api/slot"
gate_header() { printf 'Authorization: Bearer '; tr -d '\r\n' < "$KEY_FILE"; printf '\n'; }
gate_header | curl --silent --show-error --fail --connect-timeout 5 --header @- "https://$RIG_FQDN:8443/v1/models"
```

For a short inference check, replace `MODEL_ID` with the exact returned ID and run one bounded request. Report the returned model, status and whether the assistant answered. Keep credentials, private prompts, and detailed peer status out of shared logs.

```sh
gate_header | curl --silent --show-error --fail --max-time 120 --header @- \
  --header 'Content-Type: application/json' \
  --data '{"model":"MODEL_ID","messages":[{"role":"user","content":"Reply with the word ready."}],"max_tokens":256}' \
  "https://$RIG_FQDN:8443/v1/chat/completions"
```

On Windows PowerShell, an enrolled device can check the model catalog with the same private key stored at its locally configured path. Reading the key from its file avoids pasting it into shell history.

```powershell
$Rig = "spark-1.tail4109f9.ts.net"
$KeyFile = Join-Path $HOME ".config/openbeast/spark-hydra-gate.key"
$Headers = @{ Authorization = "Bearer $((Get-Content -Raw $KeyFile).Trim())" }
(Invoke-RestMethod -TimeoutSec 10 -Uri "https://${Rig}:8443/v1/models" -Headers $Headers).data.id
Remove-Variable Headers
```

Use the rig's full published hostname for HTTPS. If `:8444/api/slot` is absent, the authenticated model list can still work. A slot response alone does not authenticate the inference edge. If the catalog works but a completion fails, confirm that request's model ID, limits and the provider selected in the app. HTTP 403 calls for checking authorization, 429 means a limit was reached, and 502/503 calls for checking the gate's upstream model and readiness. Check the status body from the responding component before changing server settings. [beast-slot contract](../../../docs/BEAST_SLOT.md) · [Spark client setup](../../../docs/SPARK_HYDRA.md)

## Find the failure

Use the Tailscale CLI on Linux, macOS or Windows where installed. Replace `PEER` with the peer's saved Tailscale IP. Mobile clients expose peer details and ping in the app where supported. Record the Tailscale and physical LAN addresses separately while online.

| Check | Interpretation and next action |
|---|---|
| `tailscale status` and `tailscale ip -4` | Identify the local account, peer and overlay IP. Cached status alone cannot confirm a working route. |
| `tailscale ping PEER` | Inspect the returned path. A direct address means UDP peer-to-peer. A DERP region or peer relay means a third machine carried the probe. Early pings can use a relay before a direct connection forms. |
| Known tailnet IP works but MagicDNS name fails | Investigate DNS. A service using HTTPS may still require its original hostname for certificate verification. |
| `tailscale netcheck` | Reports UDP and relay reachability. Loss of public DERP can be expected during a WAN outage. Try the actual peer and application. |
| Application returns HTTP 401 | An HTTP service answered. Confirm the configured rig, gate URL, and device-specific bearer key. Moving between rigs can require different enrolled keys. |
| Peer and application unreachable | Try the native LAN procedure below. Check physical links, LAN routes, host firewall, and whether the service is listening. |

Keep already-running Tailscale clients signed in. Existing peers may communicate using previously received keys, policy and addresses as long as an available data path and valid keys remain. An endpoint change, a public relay becoming unreachable, or expired keys can break that path. Enrollment, key refresh and policy changes require a working coordination server. Logouts and resets destroy state that may still be serving traffic. [Coordination outages](https://tailscale.com/docs/reference/coordination-server-down) · [Connection types](https://tailscale.com/docs/reference/connection-types) · [CLI](https://tailscale.com/docs/reference/tailscale-cli)

**Restarting during an outage.** For supported desktop/server clients on version 1.104 or later, the administrator can opt selected nodes into `cache-network-maps` while connected. Verify the attribute with `tailscale status --json` (under `Self.CapMap`) and allow the client to obtain its netmap while online. Persistent maps may help previously known peers reconnect after restart. Reachability and key expiry still apply. Tailscale currently excludes mobile clients and ephemeral nodes. Check the version and eligibility of each endpoint before relying on this. [Client-side netmap caching](https://tailscale.com/docs/reference/client-side-netmap-caching)

### Checks at the rig

If the peer answers but all clients fail, an operator on the selected rig can run `tailscale serve status`. Match the published HTTPS port to the expected local service. An authenticated loopback request to the gate distinguishes a working local service from a tailnet publication or policy problem. When loopback inference also fails, check the local gate and model service logs for the request time. A restart or re-enrollment changes state and can interrupt other clients.

If only one client fails, check its active tailnet account, connected state, target hostname, provider base URL and credential. On iOS or Android, confirm which VPN profile is active, open the peer list, then try the HTTPS WebUI in the device browser. A browser login succeeds independently of a third-party app's device key. For DNS trouble, test the saved overlay IP with peer ping, retain the published HTTPS hostname for the app, and check MagicDNS using the client or OS resolver. For an authorized new device, the operator enrolls it on the tailnet and separately provisions a rig-scoped gate key using the project's enrollment procedure. [Client enrollment](../../../docs/SPARK_HYDRA.md) · [Tailnet policy](https://tailscale.com/docs/features/tailnet-policy-file)

## Reach OpenBeast over the physical network

Before an outage, record each rig's reserved LAN IP, native SSH account and independently verified host key, actual loopback gate port, and location of that rig's device key. Test from each intended client. The LAN must allow the route to the rig and its native SSH daemon. Confirm the SSH account can authenticate locally and is allowed to forward to the gate. Test mobile or Windows SSH clients separately. Keep these machine-specific details in the private operator inventory.

1. Use the physical IP or an existing SSH alias that resolves over local DNS/mDNS. On the Mac here, `spark-1` uses the LAN. Verify an alias with `ssh -G spark-1` and, once connected, `ssh spark-1 'echo "$SSH_CONNECTION"'`. Source and destination should be the expected LAN addresses. Native OpenSSH uses the server host key pinned for this LAN address/alias. A mismatch needs independent verification.
2. Keep this tunnel running in one terminal. Replace `LAN_SSH_TARGET` with the prepared native SSH target and `8090` with the rig's configured **beast-gate** port. Port 18443 is a client-side example. On Windows the same arguments work with OpenSSH's `ssh.exe`. A phone/tablet needs an SSH client with local forwarding support.

   ```sh
   ssh -NT -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -L 127.0.0.1:18443:127.0.0.1:8090 LAN_SSH_TARGET
   ```

3. In a second terminal, request the model list through the tunnel. This POSIX-shell example reads the Spark device's bearer key from its private file and streams the header into curl. Run with shell tracing disabled. The key stays out of the command arguments. Pick a returned model ID for inference.

   ```sh
   KEY_FILE="$HOME/.config/openbeast/spark-hydra-gate.key"
   { printf 'Authorization: Bearer '; tr -d '\r\n' < "$KEY_FILE"; printf '\n'; } |
     curl --noproxy '*' --fail --silent --show-error --header @- http://127.0.0.1:18443/v1/models
   ```

4. Point the client's OpenAI-compatible provider at `http://127.0.0.1:18443/v1` and supply the same rig-specific key through its usual private credential setting. Keep the tunnel bound to loopback. This forwards just the inference gate. Other endpoints, such as slot discovery or the WebUI, require their own services and appropriate authentication.

On 2026-11-17 the Mac reached Spark 1 over native LAN SSH, forwarded to its loopback gate on 8090, listed models with HTTP 200, and completed a DeepSeek request. The test exercised the fallback while internet access remained available. For an outage drill, isolate WAN access on a spare test network, retain LAN routes and verify both peer ping and an authenticated application request. Restore WAN before testing new enrollments or policy changes.

## Ask a local model for help

Use the installed `platform_docs` tool with `source="tailscale"`. Search for a topic and read a returned document ID. With only a shell, use the commands below from the OpenBeast checkout. Windows systems with Python 3.9+ can substitute `py -3 agents/docs/cli.py` for `./scripts/docs.sh`. Both search and read operate locally. A model without tools can read this Markdown guide or open the exported page files directly.

```sh
./scripts/docs.sh search "connection types" --source tailscale
./scripts/docs.sh read tailscale:/docs/reference/coordination-server-down
./scripts/docs.sh read tailscale:/docs/reference/client-side-netmap-caching
```

```text
platform_docs(query="peer relay", source="tailscale")
platform_docs(doc="tailscale:/docs/reference/connection-types")
```

Each mirrored page carries its publisher URL and fetch date. In new answers, cite the page and check the installed client version. Useful search terms include `MagicDNS`, `subnet routers`, `key expiry`, `grants`, `DERP servers`, `Tailscale Serve`, and `Tailscale SSH`.

## Refresh and transport the reference

From a connected OpenBeast checkout, run the following to sync the official docs sitemap and pack just this source. Keep the resulting private archive and its SHA-256 alongside the field guide. Re-run sync periodically or after interruptions. The acquirer respects the publisher's robots policy and records source metadata. Downloaded pages live outside version control. [Official sitemap](https://tailscale.com/sitemap.xml) · [Publisher's Markdown index](https://tailscale.com/docs/llms.txt)

```sh
./scripts/docs.sh sync tailscale
./scripts/docs.sh pack tailscale-docs.tar.gz --source tailscale
```

On an offline client, use the hash printed by the trusted packing host. Install to the store read by the client's tool process (`OPENBEAST_DOCS_DIR`, default `~/.openbeast/docs`). If the LAN and its services are unavailable, this local copy still answers questions.

```sh
./scripts/docs.sh unpack tailscale-docs.tar.gz --sha256 TRUSTED_SHA256
./scripts/docs.sh search "key expiry" --source tailscale
```

The docs bundle holds reference text. For reinstalling software offline, separately stage the correct signed installer or package for each operating system and architecture. Check the platform entries in [beast-ark](../../../docs/BEAST_ARK.md).

## When independent tailnet management is required

For ongoing enrollment and key/policy changes through a prolonged internet outage, plan a separately managed network with a LAN-reachable coordination server such as Headscale. Clients must be configured and enrolled with that server beforehand. Their existing hosted Tailscale account and policy do not automatically fail over. Plan trustworthy local DNS, TLS, time, registration credentials, storage and recovery, as well as direct peer connectivity or a LAN-reachable relay. Headscale supports an embedded DERP and a custom relay map. Test each intended OS with WAN unavailable before depending on this design. [Custom coordination server](https://tailscale.com/docs/how-to/set-up-custom-coordination-server) · [Headscale relay configuration](https://headscale.net/stable/ref/derp/)
