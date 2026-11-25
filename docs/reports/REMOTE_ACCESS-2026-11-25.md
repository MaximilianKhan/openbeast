# Remote access, 2026-11-25: "I could log in, but nothing worked away from home"

**The report (Max, 2026-11-25).** "When I was out of the house trying to
access the sparks from the remote WebUI, it failed. I could login, but I was
not allowed to access the portal, model, or anything. I kept getting
failures." The goal: everything works on the tailnet from anywhere, for every
path, with authentication on and nothing opened to the internet.

**Verdict.** We found two root causes. **Neither was the network, the
tailnet, TLS or the gate.**

1. **The WebUI had no models, for anyone, on any network.** Open WebUI keeps
   its upstream connection key in its own database. On spark-1 that key was
   still `not-needed`, the placeholder docker-compose seeded at the WebUI's
   first boot (2026-11-13, under GLM, an unkeyed engine). DeepSeek serves
   with an engine key (since 2026-11-17). Every model fetch the WebUI made was
   therefore a 401, and the WebUI showed an empty model picker after a
   successful login. `configure-webui.sh` synced the URL on every start but
   never the key. This was not specific to being away: the last chat that
   produced an answer in the WebUI was on 2026-11-15, under GLM. Max simply
   had not used the WebUI at home since DeepSeek came up. **Fixed** in code
   (the key now follows the URL, and the watchdog checks and heals it),
   deployed, and verified.
2. **`ssh spark-1` / `ssh spark-2` from the Mac only worked at home.** The
   short names came from NVIDIA Sync's ssh blocks, which point at
   `spark-3dc6.local` / `spark-9f26.local`. Those are mDNS names that resolve
   only on the home LAN. The tailnet entries existed only under the full
   `*.tail4109f9.ts.net` names, which nobody types. **Fixed** on the Mac's
   `~/.ssh/config`: the short names now resolve to the tailnet names, on
   every network. The fix is documented in
   [SPARK_HYDRA F1](../SPARK_HYDRA.md#f1-ssh-from-the-mac).

What **worked from cellular** on 2026-11-24, as the logs show: Tailscale
(a direct UDP path from the phone's carrier address), MagicDNS, the
`tailscale serve` TLS front, the WebUI login and every WebUI API call
(all 200 except one designed 403, below), and spark-guard. beast-gate admits
any enrolled device key from any tailnet address. There is no source-IP or
LAN allowlist anywhere in the path.

## Timeline (PST)

| When | What | Source |
|---|---|---|
| 2026-11-13 16:08 | The WebUI's DB config rows are written at first boot, `openai.api_keys = ["not-needed"]` (compose's fallback; GLM was unkeyed) | `webui.db` `config.updated_at` 1794614936 |
| 2026-11-15 18:29-18:57 | The last WebUI chats with assistant answers, all `GLM-5.3-Flash-EXL3` | `webui.db` `chat` table |
| 2026-11-17 to 11-18 | DeepSeek takes over, served by TensorFold with `--api-key-file`. The WebUI's model fetches start drawing 401s | TensorFold journal: `GET /v1/models … 401 - key=unauthenticated` (2026-11-17 23:30, 11-18 01:50, 11-19 10:16, …) |
| 2026-11-23 10:11-19:00 | The GLM window (unkeyed). `beastds` restores DeepSeek at 19:00 and the 401s resume (15 in 19:00:49-19:00:55 as the WebUI restarts) | journal; `openbeast.conf.bak-20261123-*` |
| 2026-11-24 08:50 | The command center restarts (`INFERENCE_SLOTS=8`); Open WebUI container recreated | `docker inspect`, conf mtime |
| **2026-11-24 21:11:56** | **Max's phone (`iphone181`, 100.121.132.65, node `[dt3kD]`) joins from 174.239.160.143, a cellular carrier address, over a direct UDP path** | spark-1 `tailscaled`: `magicsock: disco: node [dt3kD] … now using 174.239.160.143:1245` |
| 21:12:20-21:12:38 | Phone: `/auth` → sign-in as `maxjkh@…` (admin) → 200. `/api/models` 200 **with an empty list**, twice | Open WebUI access log; at the same seconds TensorFold logs `GET /v1/models 401 key=unauthenticated` (21:12:28, 21:12:38) |
| 21:59:44 | The phone comes back (new carrier port 1223); the same empty list (`/v1/models` 401 at 21:59:44). `/api/v1/channels/` 403 (the Channels feature is off: by design, harmless) | WebUI log, TensorFold journal, tailscaled |
| ~22:00 | Max gives up | |
| 2026-11-24 23:04 | The MacBook is seen on the home LAN (192.168.40.136). It was **not** away: all of its gate traffic that evening is from home | tailscaled `[930B5] now using 192.168.40.136` |
| 2026-11-25 01:20-02:30 | This investigation; the fix; deploy; verification | this report |

The phone never touched beast-gate `:8443` or the beast-chat console
`:8445` that evening. The gate's audit has no `phone` device and the chat
audit has no entry. The "portal, model, or anything" Max saw were all inside
the WebUI: its model picker, and the features that need a model.

## Evidence

All excerpts carry no secrets. Keys were compared only as SHA-256
fingerprints (first 12 hex digits) and never printed.

**The WebUI's own view, signed in as the configured admin, before the fix:**

```
admin role admin
models: []
openai cfg: {'ENABLE_OPENAI_API': True, 'OPENAI_API_BASE_URLS': ['http://127.0.0.1:8000/v1'], 'OPENAI_API_KEYS': '<n=1>', 'OPENAI_API_CONFIGS': {}}
user max... admin
user adm... admin
```

The users are admins, so this is not a `pending` role or model access
control. The URL is right (loopback `:8000`, the engine). The list is still
empty.

**The key the WebUI holds against the key the engine wants:**

```
conf LLAMA_API_KEY        fp 22688604b5a0      # openbeast.conf
webui container env key   fp 22688604b5a0      # OPENAI_API_KEY env: right, but IGNORED once the DB has a value
stored key (webui.db)     fp f67f81547582 len 10   # = "not-needed"
backend w/ stored key     HTTP Error 401: Unauthorized
POST /openai/verify       HTTP Error 401: Unauthorized
curl :8000/v1/models with the conf key   200
```

`webui.db`, table `config`:

```
['openai.enable',        'true',                          '1794614936']
['openai.api_keys',      '["not-needed"]',                '1794614936']   # 2026-11-13 16:08:56
['openai.api_base_urls', '["http://127.0.0.1:8000/v1"]',  '1794614936']
```

**The phone's session, Open WebUI access log (UTC; 05:12 UTC = 21:12 PST):**

```
05:12:26.549 authenticate_user: maxjkh@gmail.com
05:12:26.755 100.121.132.65:0 "POST /api/v1/auths/signin HTTP/1.1" 200
05:12:28.077 open_webui.routers.openai:get_all_models()
05:12:28.082 100.121.132.65:0 "GET /api/models HTTP/1.1" 200
05:12:28.161 100.121.132.65:0 "GET /api/v1/models/model/profile/image?id=undefined&lang=en-US HTTP/1.1" 302
05:12:38.054 100.121.132.65:0 "GET /api/models HTTP/1.1" 200
05:59:44.547 100.121.132.65:0 "GET /api/models HTTP/1.1" 200
05:59:44.721 100.121.132.65:0 "GET /api/v1/channels/ HTTP/1.1" 403
```

`id=undefined` is the WebUI's front end asking for the avatar of the
selected model when no model exists.

**The engine at the same seconds (spark-1, `journalctl --user -u deepseek-v41-inference`):**

```
2026-11-24T21:12:28-08:00 [tensorfold] 127.0.0.1 "GET /v1/models HTTP/1.1" 401 - key=unauthenticated
2026-11-24T21:12:38-08:00 [tensorfold] 127.0.0.1 "GET /v1/models HTTP/1.1" 401 - key=unauthenticated
2026-11-24T21:59:44-08:00 [tensorfold] 127.0.0.1 "GET /v1/models HTTP/1.1" 401 - key=unauthenticated
```

Each of the phone's `/api/models` calls triggered exactly one 401
upstream. Every other client on the engine shows `key=file-1` (the gate,
the dashboard, the healthcheck), so only the WebUI held the wrong key.

**The network path (spark-1 `tailscaled`):**

```
Nov 24 21:11:52 nodeBackend: peer [dt3kD] disco key changed …
Nov 24 21:11:56 magicsock: new contact: peer=[dt3kD] … via=derp
Nov 24 21:11:56 magicsock: disco: node [dt3kD] … now using 174.239.160.143:1245 mtu=1360
Nov 24 21:59:44 magicsock: disco: node [dt3kD] … now using 174.239.160.143:1223 mtu=1360
```

A first contact goes through DERP, then a direct path. Nothing was dropped
or refused.

**beast-gate audit (`.run/inference-audit.jsonl`), 2026-11-21 → 25.** The
only non-200s are `499 client_disconnect`, `400 fanout`, and
`502 upstream_error` / `401 upstream_status` during model switches (11-23
17:00, the GLM window), all from `macbook` or `rig-eval`. No request was
refused for its source address.

## Hypotheses tested

| Hypothesis | Result | How we know |
|---|---|---|
| WebUI → model uses a LAN or localhost address that only works at home | **No.** It uses `127.0.0.1:8000` on spark-1 itself, which is the same from anywhere | `OPENAI_API_BASE_URLS`; the WebUI runs `network_mode: host` |
| **The WebUI's stored connection key is stale** | **YES: root cause 1** | `webui.db`, 401s at the phone's exact seconds |
| MagicDNS / split DNS | No | the phone resolved and reached `spark-1.tail4109f9.ts.net`; the Mac resolves it to 100.66.191.118 |
| `tailscale serve` / Funnel config | No. All 7 surfaces are `(tailnet only)`; no Funnel | `tailscale serve status` |
| The gate's allowlist, device keys or source-IP checks | No. Keys only: `macbook` from the Mac, `phone` and `rig-eval` all pass through the tailnet name; a wrong key gets 401 | verification below |
| Open WebUI `pending` role / model access control | No. Both accounts are `admin`; `ui.default_user_role` is `pending` only for new signups, and signup is off | `/api/v1/users/`, `config` rows |
| CORS / WebSocket / origin | No | `access-control-allow-origin: https://spark-1.tail4109f9.ts.net`; `/ws/socket.io` WebSocket upgrade → `101 Switching Protocols` through `:443` |
| TLS per hostname | No | cert `CN = spark-1.tail4109f9.ts.net`, valid to 2027-02-13 |
| spark-guard / ufw | No. ufw is disabled (`ENABLED=no`); spark-guard drops only the engine rendezvous ports and the fabric addresses off-fabric. Every `tailscale serve` port is outside it | `spark-guard.sh --render` |
| Tailscale ACLs / tags | No. Every device is `maxjkh@` and every port answered the phone | tailscaled, WebUI log |
| HTTPS vs HTTP mixed content | No. The WebUI is served over HTTPS and calls only its own origin | access log |
| Phone path vs laptop path | The phone hit root cause 1. The laptop was at home that evening, but would have hit root cause 2 for `ssh spark-1` away | tailscaled endpoints; `ssh -G spark-1` → `hostname spark-3dc6.local` |
| **ssh short names resolve only at home** | **YES: root cause 2** | `ssh -G spark-1`: `hostname spark-3dc6.local` (from NVIDIA Sync's include) |

## Fixes

**1. `scripts/configure-webui.sh`: the key follows the URL** (PR, this
date). The step that pointed the WebUI's connection at
`OPENBEAST_MODEL_URL` is now `_model_connection <check|sync>`. It compares and
rewrites **both** `openai.api_base_urls` and `openai.api_keys`. The key
is `LLAMA_API_KEY` from `openbeast.conf`, or `not-needed` when the engine is
unkeyed, which is exactly what compose would seed today. Details:

- The key goes to `docker exec -i` on **stdin**. Only the URL is on argv,
  because `ps` shows argv to every uid. Nothing prints it. A test asserts the
  key never appears in docker's argv.
- The script inserts a missing row and updates the row's `updated_at`.
- Exit codes are unambiguous. The in-container Python returns 42 for "differs
  or rewritten", because docker's own failures are 1 and 125-127. A first
  draft used 1 for both, so a stopped container read as "stale". A test
  caught it.
- Two new modes, with no sign-in and no wait:
  `--check-connection` (read-only, does not create `openbeast.conf`; exit
  0 = right, 1 = stale, 3 = could not ask) and `--sync-connection`.
- No restart is needed. Open WebUI 0.13.x reads config rows straight from the
  DB on each request (`open_webui/models/config.py`: "Reads are direct DB
  lookups"). The live fix below took effect on the next `/api/models` with no
  container restart.
- It follows model switches. `beast-spark` (`beastds`, `beastglm`,
  `beasthy4`) restarts the command center, whose start runs
  `configure-webui.sh`. GLM unkeyed then gets `not-needed`, and DeepSeek or
  Hy4 get the key `beast-spark` writes to `LLAMA_API_KEY`.

**2. `scripts/healthcheck.sh`: "up" is not "usable".** A new line,
`OK   Open WebUI model connection (URL + key)`, appears when the WebUI
container is running. When the connection is stale it prints
`DOWN Open WebUI model connection — … the WebUI lists no models`. Under
`--restart` (the watchdog, every 5 minutes) it runs `--sync-connection`
itself, gated by `_may_restart` like every other heal. Before this change,
eight days of an unusable WebUI passed with every check green.

**3. The Mac's `~/.ssh/config`** (not in the repo; backup
`~/.ssh/config.bak-20261125-remote-access`). Two blocks go **before**
NVIDIA Sync's `Include`. They point `spark-1` / `spark-2` at the tailnet
names and keep `HostKeyAlias spark-3dc6.local` / `spark-9f26.local`, so the
known host keys still verify. A `Host *` line comes before the `Include`.
The first attempt left the `Include` inside the `Host spark-2` block, which
scoped Sync's file to spark-2 only, and `ssh spark-1` lost its user and key
("Permission denied (publickey)"). That lesson is in F1 and G8. At home,
Tailscale's direct LAN path carries the session, so nothing is slower. If
Tailscale is off at home, `ssh spark-3dc6.local` still works.

**4. Docs.** SPARK_HYDRA: a dated status paragraph at the top, the WebUI
connection key in [E5](../SPARK_HYDRA.md#e5-webui-auth-and-the-admin-account-before-publishing)
(with a by-hand check and the offline path), a rewritten
[F1](../SPARK_HYDRA.md#f1-ssh-from-the-mac), three new
[G8](../SPARK_HYDRA.md#g8-when-x-fails-troubleshooting) rows, and the
healthcheck count (one more check).

**Offline path.** Every step of the fix is local. `configure-webui.sh
--sync-connection` is a `docker exec` into the running container plus a
SQLite write. The healthcheck heal is the same. The ssh change is a text
edit. None of them needs the internet or an image pull. Tailscale itself
needs its coordination server to bring up *new* connections; an islanded
pair still serves the LAN and `ssh spark-3dc6.local`.

## Deploy log

PR [#322](https://github.com/MaximilianKhan/openbeast/pull/322) was merged
as `554f6cee`. It was deployed by [G6](../SPARK_HYDRA.md#g6-updating) on
2026-11-25 at about 01:40 PST, with the pair idle (`slots.busy` none; only
the Mac's 30-second `/v1/models` poll). No image pin moved, so no WebUI
backup was needed. DeepSeek (`deepseek-v41-inference`) was **not**
restarted and stayed `active` throughout.

```text
# both Sparks were at ea45b9fb (#299), 52 commits behind; the gate change in #319 was not yet live
spark-1$ git pull --ff-only && ./scripts/pydeps.sh install     # 554f6cee; lock moved (starlette 1.10.0, pydantic 2.14.3, …)
spark-2$ git pull --ff-only                                     # 554f6cee (same commit)

spark-1$ ./scripts/configure-webui.sh --check-connection   → 1   (stale: the live finding)
spark-1$ ./scripts/configure-webui.sh --sync-connection    → 0
spark-1$ ./scripts/configure-webui.sh --check-connection   → 0
         TensorFold 401s in the following 2 minutes: 0

spark-1$ systemctl --user restart openbeast      # the gate now runs #319 + the new lock
spark-1$ ./scripts/healthcheck.sh
  OK   TensorFold (tensorfold @ http://127.0.0.1:8000)
  OK   Tool server
  OK   Open WebUI
  OK   Open WebUI model connection (URL + key)
  OK   SearXNG
  OK   beast-gate
  OK   beast-chat console
  OK   beast-artifact
  OK   Dashboard (beast-slot)
  OK   Tailscale (remote access)
All 10 services healthy.
```

**The watchdog heal, proved on the live host.** We wrote `not-needed` back
into `openai.api_keys`, then ran one watchdog pass:

```text
--check-connection → 1
healthcheck.sh --restart:
  DOWN Open WebUI model connection — its stored URL or key is not this host's: the WebUI lists no models
       → re-synced (configure-webui.sh --sync-connection)
--check-connection → 0
```

The WebUI had no key for a few seconds. No chat was in flight.

## Verification

After the deploy (2026-11-25, 01:40-01:50 PST). Keys went to curl as
`--config` lines on stdin and were never printed or put on argv.
"Tailnet" means the request used only `*.tail4109f9.ts.net` names, which
resolve to `100.x` addresses, and arrived from a `100.x` source.

| Path | Device / key | Network | Result |
|---|---|---|---|
| WebUI `:443` page + `/api/config` | Mac browser path | tailnet (Mac 100.90.53.29) | 200, 200 |
| WebUI sign-in as Max's account | Mac | tailnet | 200, role `admin` |
| WebUI `/api/models` | Mac, Max's account | tailnet | **`['DeepSeek-V4.1-Flash-1017', 'arena-model']`** (was `[]`) |
| WebUI chat completion (`/api/chat/completions`) | Mac, Max's account | tailnet | "391" in 1.9 s |
| WebUI WebSocket (`/ws/socket.io`, upgrade) | Mac; rig | tailnet | `101 Switching Protocols`, both |
| WebUI CORS for its origin | Mac | tailnet | `access-control-allow-origin: https://spark-1.tail4109f9.ts.net` |
| WebUI page, `/api/config` | rig (`beast`, 100.100.201.38) | tailnet | 200, 200 |
| gate `:8443` `/v1/models`, no key | Mac | tailnet | 401 (auth enforced) |
| gate `/v1/models` | `macbook` key (the Mac's own file) | tailnet | 200 |
| gate streaming completion (SSE) | `macbook` key | tailnet | streamed, `data:` events |
| gate completion | `phone` key (spark-1's copy) | tailnet name (spark-1's own `100.x`) | 200, "42" |
| gate `/v1/models` | `rig-eval` key | tailnet (from `beast`) | 200 |
| gate, wrong key | — | tailnet | 401 |
| `openbeast-client status` (beast-slot on the laptop) | Mac | tailnet | 9 ok: model API, beast-slot, search, opencode |
| beast-slot `:8444/api/slot` | Mac; rig | tailnet | 200, 200 |
| beast-chat `:8445` | Mac; rig | tailnet | 200, 200 |
| beast-artifact `:8446` | Mac | tailnet | 200 |
| dashboard `:8447` page + `/api/status` | Mac; rig | tailnet | 200; 200 |
| SearXNG `:8889` | Mac | tailnet | 200 (JSON search) |
| TLS `:443` | Mac | tailnet | `CN = spark-1.tail4109f9.ts.net`, valid to 2027-02-13 |
| `ssh spark-1` / `ssh spark-2` | Mac | tailnet | arrives from `100.90.53.29` (was `.local`, LAN only) |
| `ssh beast` | Mac | tailnet | arrives from `100.90.53.29` |
| WebUI on the phone over cellular, 2026-11-24 (before the fix) | phone | cellular 174.239.x → tailnet | network fine; **empty model list** (root cause 1) |

**What this does and does not prove.** All of the above ran from home,
where Tailscale carries the session over the direct LAN path. Nothing in
the serving path can tell home from away. Every surface is a `tailscale
serve` listener on spark-1's `100.66.191.118`, every request arrives from a
`100.x` source, and no rule anywhere keys on the underlay. The phone's
cellular session on 2026-11-24 is the off-LAN control: its packets arrived
from 174.239.160.143 and every request was answered. The one thing
not yet re-run **after** the fix from off the LAN is the phone itself: see
below.

## Left for Max

No sudo is needed for any of this.

1. **Phone, 1 minute, on cellular** (Wi-Fi off, Tailscale on): open
   `https://spark-1.tail4109f9.ts.net`, pull to refresh (or sign out and in),
   and pick `DeepSeek-V4.1-Flash-1017`. Send one message. That is the
   off-LAN confirmation of the fix. If the picker is still empty, the
   phone's page is stale: close the tab and reopen it.
2. **NVIDIA Sync on the laptop** still names the Sparks `spark-3dc6.local` /
   `spark-9f26.local` in its own device settings. Terminal `ssh spark-1` no
   longer cares (our blocks sit before Sync's `Include`, so a Sync rewrite of
   its file does not undo them). If you use **Sync's app** (its terminal,
   JupyterLab or port forwards) away from home, edit each device in Sync and
   set the hostname to `spark-1.tail4109f9.ts.net` /
   `spark-2.tail4109f9.ts.net`.
3. **An ssh app on the phone** (if you use one): use the tailnet names,
   never `.local` and never `192.168.40.x`.
4. **The plaintext gate-key copies on spark-1**
   (`~/.config/openbeast/gate-keys/`, the E7 known issue). `macbook.key`
   there is stale: it no longer works (401), and the Mac has the current key.
   `phone.key` still works. When the phone is confirmed, run
   `shred -u ~/.config/openbeast/gate-keys/*.key`.
5. **The rig's dashboard** `https://beast.tail4109f9.ts.net:8447` answers
   502 right now. That is expected: the rig's command center is stopped
   while the Inkling/Basalt eval holds the GPU, and the dashboard comes back
   when the rig's stack starts. We did not touch it.

One process note: the WebUI end-to-end check signed in as Max's own account.
Its password was copied from spark-1's `~/.config/openbeast/webui-max-password`
into a 0600 temp file on the Mac for the duration of one script, then
deleted. It was never printed.
