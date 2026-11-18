# Serve the prepared models on the Spark pair

Run server commands on the head Spark as the account that owns OpenBeast. The head coordinates both GPUs. The copy of this note on the worker is for reference. No internet or 5090 is required for local generation once the runtime is staged. Both Sparks, their local files, and the inter-Spark network must be available.

## Check what is running

```sh
systemctl --user is-active deepseek-v41-inference.service spark-hydra-inference.service openbeast.service
systemctl --user is-enabled deepseek-v41-inference.service spark-hydra-inference.service
python3 ~/openbeast/agents/island.py status
curl -fsS --max-time 3 http://127.0.0.1:8000/health
```

The first two units are DeepSeek (serving whichever pack `local.sh` selects; Mia's EAQ v1.1 "-v2" since 2026-11-17) and GLM respectively. Since 2026-11-18 GLM is the default (enabled for boot) and DeepSeek the staged alternative. One inference unit normally owns both GPUs. OpenBeast is the separate command center. The health endpoint checks backend readiness. For an end-to-end check, refresh the selected rig on your client and send a short prompt through the usual authenticated endpoint.

## Start or stop the currently selected model

When DeepSeek is selected, use:

```sh
systemctl --user start deepseek-v41-inference.service
journalctl --user -fu deepseek-v41-inference.service
```

Ctrl-C exits the log viewer. To stop serving DeepSeek, run `systemctl --user stop deepseek-v41-inference.service`. When GLM is selected, substitute `spark-hydra-inference.service` in those commands. A manual stop leaves the enabled boot selection in place. Starting a unit alone is for the already configured model.

## Switch models

Finish active requests first. Switching releases both GPUs and interrupts inference while the next model loads. A prepared DeepSeek launch took about 40 seconds on the running pair; service checks and client refresh add time.

**DeepSeek to GLM** — use the installed rollback helper:

```sh
bash ~/openbeast-recipes/deepseek-prep/rollback/restore-glm.sh
```

**GLM to DeepSeek** — stop GLM before activating the prepared DeepSeek service (it serves whichever DeepSeek pack `local.sh` selects):

```sh
systemctl --user stop spark-hydra-inference.service &&
  bash ~/openbeast-recipes/deepseek-prep/activate-stock.sh
```

The helpers set the managed service and matching command-center configuration. The DeepSeek helper also checks the prepared runtime, runs a chat and conformance checks, and sets the boot selection on success. If activation fails, inspect its output and journal; the GLM rollback helper above restores the prior GLM configuration. After either switch, run `openbeast-client use spark-hydra` on an enrolled OpenBeast client with that rig name. Confirm the model shown in your client and send a short prompt.

**One DeepSeek pack for another** (for example the stock EAQ v1 pack and Mia's experimental EAQ v1.1 "-v2" pack, the default since 2026-11-17). The pair serves only DeepSeek and GLM, and the pack is the only part of DeepSeek you swap:

```sh
cd ~/openbeast-recipes/deepseek-prep
./spark-deepseek-pack.sh list
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --bench   # or: use stock
```

It mirrors the folder to the worker, prepares both ranks, restarts, runs the chat and conformance checks, and rolls back by itself on failure. Details are in `~/openbeast/docs/SPARK_HYDRA.md`, "DeepSeek packs".

## Configuration on the head

| Path | Purpose |
|---|---|
| `~/openbeast/openbeast.conf` | Live command-center settings, including backend, advertised model, slots, thinking default and island policy. May contain credentials. |
| `~/openbeast/openbeast.conf.example` and `~/openbeast/scripts/lib/conf.sh` | Example settings and default-loading logic. |
| `~/openbeast-recipes/deepseek-prep/runtime/scripts/local.sh` | DeepSeek launch overrides. Recipe defaults are in the adjacent config.sh. The `spark-deepseek-pack` block selects the pack; edit it only through that script (previous copy: `local.sh.prev`). |
| `~/openbeast-recipes/glm53/scripts/local.sh` | GLM launch overrides. |
| `~/.config/systemd/user/` | Installed user units that run the recipes. Use systemctl --user cat UNIT to inspect a unit. |

## During a WAN outage

In the default auto mode, the watcher islands the stack after 90 seconds of failed internet probes. A staged model can continue generating locally; internet-backed tools become unavailable. If tailnet access fails, use the documented native LAN SSH tunnel to the authenticated gate. See `~/openbeast/skills/tailscale/references/guide.md` and `~/openbeast/docs/BEAST_ISLAND.md`. Keep model files, rank caches and Docker images on disk for subsequent launches. A full power-off restart of both Sparks with WAN disconnected is still an unverified recovery scenario for the current DeepSeek deployment.
