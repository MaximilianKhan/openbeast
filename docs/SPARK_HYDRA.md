# spark-hydra: the DGX Spark launch runbook

**Live deployment, 2026-11-18.** The pair serves **GLM-5.3-Flash**, the default model at boot and in use; Max chose it on 2026-11-18 after testing DeepSeek. The alternative is `DeepSeek-V4.1-Flash-1017` on Mia's experimental EAQ v1.1 "-v2" pack, which supersedes the stock EAQ v1 pack ([DeepSeek packs](#deepseek-packs-spark-deepseek-pack)). Switch between them with `beastglm` and `beastds` ([shortcuts](#quickstart)). The pair serves these two models only. The command center and beast-gate run on spark-1. The GLM recipe, tuning measurements and rebuild examples below describe that earlier deployment.

This page takes you from bare boxes to a serving pair, start to finish, and then
covers day-2 operations and fully offline operation. Read it top to bottom the
first time. After that, jump to the section you need, or start at the
[Quickstart](#quickstart). Historical design background is in
[`DGX_SPARK_PLAN.md`](DGX_SPARK_PLAN.md) (§8a: the stack on a Spark; §14:
onboarding a model; §15: why GLM-5.3-Flash); where it and this page differ,
this page is current. Engine choice is in
[`ENGINES.md`](ENGINES.md). For the local model working on OpenBeast itself,
see [`SELF_DEVELOPMENT.md`](SELF_DEVELOPMENT.md).

**How to read the labels.** **Measured** means it ran on our pair and the
result is recorded here. **VERIFY ON HARDWARE** means the code exists and is
tested against stubs, but has never run on a real Spark. Every command on this
page exists in the code on `main` (`feat/spark-scale-1-8`, where it started,
is merged into it). Flags were checked against each script's `--help` on
2026-11-15, and again for the two checklists on 2026-11-16.

## DeepSeek deployment

On spark-1, the deployed runtime checkout lives at `~/openbeast-recipes/deepseek-prep/runtime`. The canary, conformance records and rollback helper sit under `~/openbeast-recipes/deepseek-prep/`, and its user unit is under `~/.config/systemd/user/`. These paths are host-local deployment artifacts.

The November 17 cutover used Mia's [two-Spark recipe at c7f2b85](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks/tree/c7f2b85100f0a7b099c92a4e21825e30d93c9c51). Both hosts ran the same locally built image, `tensorfold-dsv41:exl3-tp2-8a98ae503d85`, with one tensor-parallel rank per Spark over two RoCE rails. The advertised context was 524,288 tokens with four streams.

| Disk material | Verified at cutover |
|---|---|
| Mia EXL3 EAQ v1, 2.9 bpw | 39 shards at revision `45ee5d0fd106c94a3d77f721e09cbb2f50e5a16b`. All 64 manifest entries passed on spark-1. |
| Engram data | Base revision `1bae0e1088993b9b82de08f59de5be5a34e42170`, shards 47 and 48, totaling 203,073,077,576 bytes. Verified on both hosts. |
| Prepared rank caches | About 106 GB per rank. Both were prepared on the head after stopping GLM, then rank 1 was copied to spark-2. Each host loads its own rank cache. |
| Documents backup | spark-2 received 389,659,303,295 logical bytes from spark-1. The copy was verified with rsync checksums and metadata. A Documents backup and a prepared rank cache serve different purposes. |

**Acceptance.** The gate accepted an enrolled Mac device at `:8443`, forwarding to the authenticated backend on spark-1 loopback `:8000`. In OpenCode, `openbeast-rig/DeepSeek-V4.1-Flash-1017` answered 17 × 23 with 391. The conformance run passed chat, streaming, reasoning-field extraction, structured and parallel tool calls, tool results and output-token limits. Four concurrent requests completed in 1.5 seconds. The record on spark-1 is `~/openbeast-recipes/deepseek-prep/conformance-4/latest.json`. Context overflow was skipped. Cold boot without WAN and a v5 capability score are still open.

**Local interactive baseline, November 17.** From the Mac through the authenticated tailnet gate, six short temperature-zero prose probes requested 256 completion tokens each. With thinking on (including one request using the gate default), single-stream decode was 70.1–71.7 tok/s, all reasoning tokens. With `chat_template_kwargs.enable_thinking=false`, it was 53.7–63.2 tok/s, median 60.7, all visible answer tokens. The median first streamed token arrived in 232 ms with thinking and 156 ms without. Four simultaneous thinking-off requests delivered 1,024 completion tokens in 8.74 s, or 117.1 tok/s aggregate end to end. Decode rates use tokens after the first divided by time from first to last content-bearing chunk. Use thinking for multi-step work and disable it per request for quick answers. This short-prompt test does not establish long-context performance or a quality-optimal setting.

**Rollback on this pair.** The GLM unit `spark-hydra-inference` is disabled while DeepSeek owns the ranks. To switch both ranks and restore the saved serving configuration, run the installed `~/openbeast-recipes/deepseek-prep/rollback/restore-glm.sh` on spark-1. Then run `openbeast-client use spark-hydra` on the client to refresh its model and check discovery and a real completion.

### DeepSeek packs: spark-deepseek-pack

A *pack* is any EXL3 quantization of DeepSeek-V4.1-Flash-1017 with the same tensor layout as Mia's EAQ v1 2.9 bpw build: the same `model.safetensors.index.json` keys and `config.json` architectures. Engram's tables (base shards 47 and 48) are shared by every pack. `scripts/backends/spark-deepseek-pack.sh` swaps the served pack offline, verifies it, and rolls back on failure. Install it on spark-1 next to the other helpers, together with `spark-deepseek-prepare-local.sh` (the generalized form of `prepare-stock-local.sh`):

```bash
# On the Mac, from the repo
scp scripts/backends/spark-deepseek-pack.sh scripts/backends/spark-deepseek-prepare-local.sh \
    spark-1:openbeast-recipes/deepseek-prep/

# On spark-1
cd ~/openbeast-recipes/deepseek-prep
./spark-deepseek-pack.sh list                                   # serving pack, staged packs, previous
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --dry-run  # checks + plan, changes nothing
./spark-deepseek-pack.sh use ~/Documents/<pack-folder> --bench    # swap, verify, A/B bench
./spark-deepseek-pack.sh use stock                              # back to Mia EAQ v1 @ 45ee5d0f
```

To bring in a new pack, copy its folder into `~/Documents` on spark-1, for example from a drive. Offline, the script does the rest:

1. It refuses a folder whose tensor index or architecture differs from the serving pack's. Such a model is not a drop-in and needs its own recipe.
2. It mirrors the folder to spark-2 at the same path, which is the backup convention. Shards shared with the serving pack become hard links.
3. It hard-links the folder into `$HF_CACHE/hub` on both hosts as `models--ORG--NAME/snapshots/REV`. A local pack has no Hub revision, so REV is the sha1 of its shards' `size name` lines, and the same folder always gets the same REV. If a cache snapshot already has that REV, its name is reused. Otherwise the ID is `local/<folder>`, or whatever `--id ORG/NAME` sets.
4. It rewrites the managed block in `runtime/scripts/local.sh` (`MODEL_ID`, `MODEL_REVISION`, `WORKER_CACHE_DIR`, `MODEL_SRC`) and keeps the old file as `local.sh.prev`.
5. It stops the unit, then builds both rank files on the head. Each is about 106 GB and takes about 15 minutes the first time, seconds afterwards. Rank 1 goes to spark-2. The unit then restarts, which took about 40 s.
6. It runs `verify-chat.py` (17 × 23 = 391) and `conformance.sh`. On any failure it restores `local.sh.prev`, re-prepares the previous pack (seconds, since its files are kept) and restarts it.
7. With `--bench`, it runs `tools/bench.py` (prefill 2K/8K/32K, decode, 1/2/4 streams, thinking off) into `runtime/runs/packs/REV8.json` and compares it with the previous pack's run.

**Rank-1 cache per revision (a trap).** The engine names its rank files after the tensor *index*, not the weights. Two packs with one layout therefore both produce `rank1of2-f4b9c7e9bd4f6316d1b4.bin`. With a shared `WORKER_CACHE_DIR`, prepare would find the old pack's rank-1 file "ready" and the pair would serve mixed weights. The script therefore uses `worker-rank-cache-REV8`. The stock pack keeps its original `worker-rank-cache`.

The served name stays `DeepSeek-V4.1-Flash-1017`, so the gate, the command center and enrolled clients need no change. `activate-stock.sh` (GLM → DeepSeek) brings up whatever pack `local.sh` selects. Disk cost per pack is its folder, plus about 212 GB of rank files on spark-1 and about 106 GB on spark-2. Old rank files are never deleted automatically.

**Run record, 2026-11-17: EAQ v1.1 "-v2".** Max brought the folder `~/Documents/Mia-AiLab-DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw-v2` from a drive. It is Mia's experimental EAQ v1.1 build: 39 shards, with shards 1–10 and 35–39 identical to EAQ v1 and 11–34 rewritten. Its config, index, tokenizer, template and README are byte-identical to v1's. Its `SHA256SUMS` is v1's file carried over, so it fails on the 24 new shards and cannot verify them either way. The cache ID is `Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1.1-2.9-bpw-v2` at local REV `04c13f12b83d57487912d87d0f1677b076810954`. It loaded in 40 s and passed the canary and conformance (`conformance-v2/` on spark-1). The round trip v2 → `use stock` → v2 through the script also passed both times. Bench medians, thinking off, the same session:

| Metric | EAQ v1 (45ee5d0f) | EAQ v1.1 -v2 (04c13f12) |
|---|---|---|
| Decode, 1 stream | 59.6 / 60.2 tok/s | 60.6 / 62.3 tok/s |
| 2 streams aggregate | 86.6 / 85.6 tok/s | 90.2 / 90.8 tok/s |
| 4 streams aggregate | 120.4 / 121.7 tok/s | 118.4 / 120.5 tok/s |
| Prefill 8K | 2,331 / 2,296 tok/s | 2,384 / 2,376 tok/s |
| Prefill 32K | 2,419 / 2,274 tok/s | 2,434 / 2,436 tok/s |
| Errors | 0 | 0 |

Each cell gives two runs. The differences are inside run-to-run noise. A first v1 run measured 2K prefill at 1,322 tok/s, but that was cold-server warm-up: the later v1 run gave 1,990 against v2's 2,035–2,049. The verdict is a drop-in replacement, and v2 is the default. A quality A/B (the v5 eval) has not been run.

## Quickstart

Three paths. Every command below is from the runbook body; the linked section
has the detail. All run from the Mac unless marked "on spark-1".

**Everyday shortcuts: beast-spark.** On spark-1, `~/.local/bin/beast-spark` (from `scripts/backends/spark-beast.sh`) and its bash aliases cover the daily operations:

| Alias | Command | Does |
|---|---|---|
| `beastup` | `beast-spark up` | Starts the selected model's unit, waits for `/v1/models`, then starts the command center |
| `beastdown` | `beast-spark down` | Stops the command center and both inference units. The boot selection stays as it is |
| `beastglm` | `beast-spark glm` | Switches to GLM-5.3-Flash through `rollback/restore-glm.sh` |
| `beastds` | `beast-spark ds` | Switches to DeepSeek on the EAQ v1.1 `-v2` pack: stops GLM, re-selects `-v2` with `spark-deepseek-pack.sh` if another pack is selected, then runs `activate-stock.sh` (canary, conformance, command center, boot selection) |
| `beaststatus` | `beast-spark status` | Shows the selected model and pack, the three units, and what `:8000` serves |

"Selected" means the enabled inference unit, which is the one that starts at boot. GLM is the default; `beastup` with nothing selected picks GLM. The pair serves only these two models. The `-v2` pack supersedes the stock EAQ v1 pack, which stays on disk only as a fallback (`spark-deepseek-pack.sh use stock`). To install or update after a pull, run on the Mac: `scp scripts/backends/spark-beast.sh spark-1:.local/bin/beast-spark`. The aliases are in spark-1's `~/.bashrc`, under `# beast-spark shortcuts`. After a switch, clients with `openbeast-client autosync` follow within 30 s; otherwise run `openbeast-client refresh-config`.

**(a) Everyday, on the deployed pair.** On spark-1 the enabled user unit `deepseek-v41-inference` drives both ranks. It retries a failed launch after 60 seconds. The command center has its own user unit, `openbeast`.

```bash
# On spark-1
systemctl --user status deepseek-v41-inference openbeast --no-pager
journalctl --user -u deepseek-v41-inference --since today
curl -fsS http://127.0.0.1:8000/health

# On the enrolled Mac
openbeast-client use spark-hydra
openbeast-client status
curl -fsS https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool
```

The model ID should be `DeepSeek-V4.1-Flash-1017`. Some optional capacity and busy fields may be null. Start, stop or restart both ranks through the head unit ([G3](#g3-restarts-only-through-systemctl---user-while-the-unit-is-enabled)).

**(b) Use it from a client.** Everything goes through beast-gate on
`https://spark-1.tail4109f9.ts.net:8443/v1`, with a per-device key. The model
itself listens on spark-1's loopback only.

1. **Browser (any tailnet device):** Open WebUI at
   `https://spark-1.tail4109f9.ts.net`, with a WebUI login
   ([E6](#e6-publish-on-the-tailnet-setup-tailscalesh-as-tailscaleds-operator)).
2. **A device key** (on spark-1; [E7](#e7-device-keys-for-beast-gate)). The key
   prints once. Copy it straight into a 0600 file on the device (on the Mac:
   `~/.config/openbeast/spark-hydra-gate.key`), never into a shared log:
   `cd ~/openbeast && ./scripts/clients.sh enroll macbook --label "Max's MacBook"`.
3. **Mac or Linux client mode** ([F2](#f2-client-mode-one-or-several-rigs)). This
   gives OpenCode on the client, with the tools acting on the client's own
   files, and switches between this pair and the `beast` rig:

   ```bash
   ./scripts/setup-client.sh --host spark-1.tail4109f9.ts.net --name spark-hydra --api-key-stdin < ~/.config/openbeast/spark-hydra-gate.key
   openbeast-client status                     # reads :8444/api/slot; "active rig: spark-hydra"
   openbeast-client rigs                       # every registered rig; use <rig> switches
   cd <any project> && opencode                # pick an 'openbeast-rig' (active) or 'openbeast-<rig>' model
   scripts/client-e2e.sh                       # end-to-end check from the Mac
   ```

   The opencode v2 layout is written correctly; there is no fix-up any more
   ([F3](#f3-what-the-client-writes-into-opencode)).
4. **Raw OpenAI-compatible API** (any tool that takes a base URL and a key).
   Without a key, the gate answers 401:

   ```bash
   curl -s https://spark-1.tail4109f9.ts.net:8443/v1/chat/completions \
     -H "Authorization: Bearer $(cat ~/.config/openbeast/spark-hydra-gate.key)" \
     -H 'Content-Type: application/json' \
     -d '{"model": "DeepSeek-V4.1-Flash-1017", "messages": [{"role": "user", "content": "hello"}]}'
   ```

**(c) First build from bare boxes (GLM deployment).** Follow
[Install everything, fresh](#install-everything-fresh): 21 ordered steps,
each with its host, the online command, the offline variant and a link to the
detail. In outline:

1. [B1](#b1-first-boot) first boot.
2. [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark) root setup, on each Spark.
3. [B3](#b3-firewall-spark-guard) spark-guard, and [B4](#b4-fabric-mtu-9000-spark-fabric-mtu) the fabric MTU staged before the one reboot.
4. [C](#c-weights) weights, on both.
5. [D1](#d1-the-mia-recipe-what-serves-today) the recipe and the `spark-hydra-inference` unit.
6. [E1](#e1-bootstrap)–[E7](#e7-device-keys-for-beast-gate) the command center, boot units, WebUI admin, tailnet publishing and device keys.
7. [F](#f-the-mac-client) the Mac client, then the optional toolchains, docs mirror, ark drive and eval harness host.

The short form is [Rebuild from scratch](#rebuild-from-scratch-short-form).
With no internet at all, follow [H9](#h9-the-order-for-a-pair-built-entirely-offline)
instead.

**(d) Update a built pair to the latest `main`.** Follow
[G6](#g6-updating): read the diff, back up Open WebUI if its pin moves, pull
on both Sparks, `pydeps.sh install`, restart the command center, then the
per-change steps (spark-guard, MTU, `ARTIFACT_LOGIN_FROM`, the seed filter,
zig, the docs mirror). G6 also lists what is still pending on the live pair.

| Section | What it gets you |
|---|---|
| [Quickstart](#quickstart) | everyday checks and restarts, using the pair from a client, the build order |
| [Install everything, fresh](#install-everything-fresh) | the ordered checklist from bare DGX OS to the full pair, online and offline, per host |
| [0. The pair at a glance](#0-the-pair-at-a-glance) | names, addresses, what runs where |
| [A. What you need](#a-what-you-need) | hardware, cabling for 1 / 2 / 3 / 4-8 Sparks, accounts, the offline staging checklist |
| [B. Bare boxes to root setup](#b-bare-boxes-to-root-setup) | `spark-root-setup.sh`, `spark-guard.sh`, checking them from another device, fabric MTU 9000 (`spark-fabric-mtu.sh`) |
| [C. Weights](#c-weights) | fetch or copy, lock, hard-link into the recipe's HF-cache layout, carry to workers |
| [D. Inference](#d-inference) | D1 the Mia recipe (what serves today), D2 native TensorFold through `spark-node.sh` / `spark-cluster.sh`, D3 the engine plane |
| [E. The command center on spark-1](#e-the-command-center-on-spark-1) | bootstrap, `openbeast.conf`, boot units, WebUI admin, tailnet publishing, device keys |
| [F. The Mac client](#f-the-mac-client) | the "From the Mac" quick reference, `setup-client.sh`, several rigs (`openbeast-client rigs / use`), what opencode gets, `client-e2e.sh`, beast-apps, beast-docs, the eval harness host |
| [G. Day-2 operations](#g-day-2-operations) | health, logs, restarts, memory, watchdog, the "update everything" checklist, keys, troubleshooting |
| [H. Fully offline operation](#h-fully-offline-operation) | island and `OFFLINE=true`, what keeps working, staging every artifact |
| [Reference](#reference) | serving numbers, scaling notes, parity checks, known issues, not done yet |

---

## Install everything, fresh

This checklist rebuilds the GLM rollback deployment. For the currently served checkpoint, use the [DeepSeek deployment record](#deepseek-deployment).

One ordered pass from bare DGX OS to the full working pair, including every
optional piece. Each step names the host, the online command, the offline
variant and the section with the detail. Commands run in a shell **on the
host named** (ssh in first, and quote remote commands: [F1](#f1-ssh-from-the-mac)).
Steps marked **sudo** need Max's password at a terminal on that box
(`ssh -t`). Updating a pair that is already built is the other checklist,
[G6](#g6-updating).

**What DGX OS already brings.** Present on both Sparks on 2026-11-16, and not
installed by OpenBeast: Ubuntu 24.04.5 aarch64, NVIDIA driver 580.178.04
(CUDA 13.0), docker-ce 29.6 with the compose and buildx plugins,
nvidia-container-toolkit 1.20, python3.12 3.12.3 with `python3-venv` and
`pip`, gcc / g++ 13.3, build-essential, cmake, git, jq, rsync, sqlite3,
smartmontools, netplan 1.1.2, nftables, ufw (installed, inactive) and the RDMA
tools (`ibdev2netdev`, `ibv_devinfo`, perftest). OpenBeast adds exactly two
system packages, in step 4: `libnccl2 2.28.9-1+cuda13.0` (held) and
`tailscale 1.104.7`. Everything else lives in `max`'s home: the Python lock
in `~/.local` (pip `--user`, 43 packages, the `hf` CLI among them), native
TensorFold in `~/.local/opt` and `~/.local/bin`, the compose images in Docker,
and the user units in `~/.config/systemd/user`. Not installed on either Spark
today: zig, go, rust, node, clang, opencode, a beast-docs mirror, a beast-ark
drive (steps 15-18 add them).

**0. Offline only: stage everything first** (any connected box, plus one
online Spark for the aarch64 pieces). Fill a drive from the
[A4](#a4-the-artifact-checklist-what-to-stage-for-an-offline-build) list:
the .debs ([H3](#h3-debs-libnccl2-and-tailscale)), `openbeast.bundle`
([H2](#h2-the-repo)), the weights and DFlash2 ([C1](#c1-fetch-online-or-verify-a-local-copy),
[C4](#c4-the-drafter-dflash2)), the recipe as a git bundle plus its image
tarball ([H4](#h4-container-images)), native TensorFold
([H5](#h5-native-tensorfold-and-the-engine-plane)), the command-center bundle
built **on an online Spark** ([H6](#h6-the-command-center-wheels-and-compose-images)),
the test wheels ([H7](#h7-test-and-language-toolchains)), a docs mirror
tarball ([H8](#h8-beast-docs-mirror)) and an ark drive with zig, go and rust
([H8a](#h8a-beast-ark-the-rest-of-the-software-on-a-drive)). Online, skip
this step: each later step fetches its own piece.

**1. Cable, boot, NVIDIA Sync** (both Sparks + the Mac;
[A2](#a2-cabling-and-addressing-for-1-2-3-and-4-8-sparks),
[B1](#b1-first-boot)). Create the user `max` (in `sudo` and `docker`),
connect the LAN, pair each Spark with NVIDIA Sync so it addresses both fabric
rails, then check on each Spark: `ibdev2netdev` shows the cabled ports `(Up)`
and `ip -4 -o addr show | grep 10.100` shows the rail addresses.

**2. The repo at one commit** (both; [B1](#b1-first-boot) step 3):

```bash
git clone https://github.com/MaximilianKhan/openbeast ~/openbeast && git -C ~/openbeast log --oneline -1   # online, main
git clone /media/usb/openbeast.bundle ~/openbeast && git -C ~/openbeast log --oneline -1                   # offline (H2)
```

The two `log` lines must print the same commit.

**3. The head's ssh key to the worker** (spark-1; [B1](#b1-first-boot) step 4):
`ssh-keygen -t ed25519` if it has none, `ssh-copy-id max@10.100.216.2`, then
`ssh max@10.100.216.2 true`. No network beyond the cable.

**4. Root setup, guard included, sudo** (each Spark;
[B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark),
[B3](#b3-firewall-spark-guard)):

```bash
cd ~/openbeast
sudo scripts/backends/spark-root-setup.sh spark-1 --guard                 # on spark-1, online
sudo scripts/backends/spark-root-setup.sh spark-2 --guard                 # on spark-2, online
scripts/backends/spark-root-setup.sh --check-debs ~/debs                  # offline: no root, sha256 vs the pins
sudo scripts/backends/spark-root-setup.sh spark-N --debs ~/debs --guard   # offline (H3)
```

The first run prints a Tailscale login URL. Offline, `tailscale up` cannot
finish until there is a route; the script says so and goes on.

**5. Fabric MTU 9000, staged, sudo** (each Spark;
[B4](#b4-fabric-mtu-9000-spark-fabric-mtu)). Fully local, so the online and
offline commands are the same:

```bash
cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --stage
```

Staging changes nothing live. Doing it now means the reboot in step 6 also
brings the rails up at 9000, so you reboot once instead of twice. **Measured:**
our pair has run at 9000 since 2026-11-17 (B4, `--verify` passed on both);
its speed gain is not measured yet. To build without it, skip this step;
`--stage` can run on any later day, followed by a reboot of both.

**6. Reboot both, once** (worker first, then head). This is the docker-group
gotcha from [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark),
and it activates step 5. Then check on each Spark:

```bash
systemd-run --user --wait --pipe docker info >/dev/null && echo user-manager-sees-docker
dpkg-query -W libnccl2 && loginctl show-user "$USER" -p Linger && tailscale ip -4
sudo ~/openbeast/scripts/backends/spark-guard.sh --status         # "rules: current", unit active
~/openbeast/scripts/backends/spark-fabric-mtu.sh --verify <peer rail-1 IP> <peer rail-2 IP>   # if step 5 ran: VERIFIED (all peer IPs, one call)
```

**7. Weights** (spark-1, then spark-2; [C1](#c1-fetch-online-or-verify-a-local-copy)–[C4](#c4-the-drafter-dflash2)).
On spark-1, online, put the HF token in `~/.config/openbeast/hf-token`
(0600) and run `model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold`.
Offline, copy the tree from the drive and run the same command with
`--verify`. Then, both ways: the HF-cache hard links (C2), one `rsync -aH`
to spark-2 and `--verify` there (C3), DFlash2 copied and checked with the
blob loop on both (C4). Online, DFlash2 comes from
`hf download incoai/GLM-5.3-Flash-DFlash2 --revision bf582e4eacc1810f76656d1811693ff6c6737d2a`
(the `hf` CLI is in `~/.local/bin` once step 11 has installed the Python
lock; run step 11's `bootstrap.sh` first if it is missing).

**8. The recipe and its image** (spark-1, image on both;
[D1](#d1-the-mia-recipe-what-serves-today), [H4](#h4-container-images)):

```bash
# on spark-1. Online:
git clone https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold.git ~/openbeast-recipes/glm53
git -C ~/openbeast-recipes/glm53 checkout 33b50fd
# offline: git clone /media/usb/glm53-recipe.bundle ~/openbeast-recipes/glm53 (a `git bundle create … --all` of the same repo), then the same checkout
# write ~/openbeast-recipes/glm53/scripts/local.sh exactly as in D1 (untracked; the recipe's config.sh sources it)

# the image, on BOTH Sparks. Online, the prebuilt image prepare.sh would pull, by digest:
IMG=ghcr.io/miaai-lab/glm-5.3-flash-exl3-2x-dgx-sparks-tensorfold@sha256:cbb4b3c66273e2965dd40a7227e7a5243db333fe250113fb3987462ad4f12588
docker pull "$IMG" && docker tag "$IMG" tensorfold-glm53:v0.6.0
# or on spark-1 only, then over the cable: docker save tensorfold-glm53:v0.6.0 | ssh max@10.100.216.2 docker load
# offline: docker load -i tensorfold-glm53-v0.6.0.tar on each Spark (H4)
docker image inspect -f '{{.Id}}' tensorfold-glm53:v0.6.0   # sha256:ead0174052ac16b0… on both
```

The recipe's own `scripts/prepare.sh` does the same image pull and can also
fetch weights and copy to the worker; read the recipe's README before running
it. On our pair, OpenBeast staged the weights (step 7) and only the image
came from the recipe. The recipe lives on spark-1 only: `start.sh` runs rank 1
on spark-2 with `docker run` over ssh.

**9. The inference unit** (spark-1; [D1](#d1-the-mia-recipe-what-serves-today)).
The `sed … spark-hydra-inference.service` block in D1, then
`systemctl --user enable --now spark-hydra-inference`. Loading takes 2-6 min.
`curl -s http://127.0.0.1:8000/health` must show `"ok": true`. Online and
offline are the same: the unit runs with `PREPARE=0` and `HF_HUB_OFFLINE=1`.

**10. Native TensorFold, optional** (each Spark; [D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh),
[D3](#d3-the-engine-plane-scriptsenginesh)). The challenger engine; the pair
serves without it.

```bash
cd ~/openbeast
scripts/engine.sh install tensorfold --version 1.2.7          # online: download, verify, pin (or install-native.sh --version 1.2.7)
scripts/engine.sh install tensorfold --from /media/usb/tf      # offline (H5)
cp scripts/backends/spark.env.example scripts/backends/spark.env    # then edit it per node (D2 table)
cp scripts/backends/cluster.env.example scripts/backends/cluster.env  # spark-1 only: CLUSTER_NODES="local max@10.100.216.2"
sudo scripts/backends/spark-guard.sh                           # RE-RUN after writing spark.env: it guards spark.env's master ports
```

The key file `spark.env` names in `TENSORFOLD_API_KEY_FILE` must exist (0600)
on **every** node before a D2 launch. The re-run of the guard matters: both
live Sparks went `STALE` because `spark.env` (with `VLLM_MASTER_PORT=29501`)
was written an hour after the guard ([B3](#b3-firewall-spark-guard)).

**11. The command center** (spark-1; [E1](#e1-bootstrap), [E2](#e2-openbeastconf)).
Write `openbeast.conf` first (0600). The keys spark-1 runs with, secrets
aside:

```bash
cd ~/openbeast && cp openbeast.conf.example openbeast.conf && chmod 600 openbeast.conf
# then set, each on its own line (no trailing comments: the parser keeps them):
#   INFERENCE_BACKEND=tensorfold   INFERENCE_URL=http://127.0.0.1:8000
#   INFERENCE_MODEL=GLM-5.3-Flash-EXL3   INFERENCE_SLOTS=4
#   EDGE_GATE=true   WEBUI_AUTH=true   TS_HOSTNAME=spark-1   EXTENSIONS="dashboard"
#   BEAST_CHAT=true   CHAT_OPERATORS=<you>   CHAT_LOGIN_FROM=unix
#   BEAST_ARTIFACT=true   ARTIFACT_OPERATORS=<you>   ARTIFACT_ADMINS=<you>   ARTIFACT_LOGIN_FROM=unix
#   EDGE_READ_TIMEOUT=3600          (only if an eval harness will run against the pair: F6)
#   LANG_PACKS=off   LANG_PACK_CONTEXT=off   BEAST_ASSIST=0   BEAST_ESCALATE=0
#   INFERENCE_THINKING=on   AGENT_MAX_TOKENS=16384   (the v5 serving default: E2a)
./bootstrap.sh --preflight      # read-only: what it will do and what it cannot reach
./bootstrap.sh --no-start       # online: the Python lock, the pinned compose images (Open WebUI 0.13.3)
```

Offline: `./scripts/bundle.sh verify` and `install` the Spark-built bundle,
`echo 'OFFLINE=true' >> openbeast.conf`, then `./bootstrap.sh --no-start`
([H6](#h6-the-command-center-wheels-and-compose-images)). Leave
`EDGE_MAX_INFLIGHT` unset: the gate then admits `INFERENCE_SLOTS` (4)
generations per device. `WEBUI_RANDOM_SEED` defaults to on.

**12. Boot units** (spark-1; [E3](#e3-boot-units)): the `sed … openbeast.service`
block, `systemctl --user enable --now openbeast`. The first start installs
the watchdog and logrotate timers and runs `configure-webui.sh`, which wires
the tool servers and installs the random seed filter and, with
`INFERENCE_THINKING` set, the thinking default filter
([G6](#g6-updating), [E2a](#e2a-recommended-serving-configuration-from-v5)). Expect `healthcheck.sh` to say "All 9 services
healthy" once the model has loaded ([E4](#e4-the-healthy-state)).

**13. WebUI admin, then publish** (spark-1; [E5](#e5-webui-auth-and-the-admin-account-before-publishing),
[E6](#e6-publish-on-the-tailnet-setup-tailscalesh-as-tailscaleds-operator)).
Log in over an ssh tunnel, create your admin, then:

```bash
cd ~/openbeast
./scripts/setup-tailscale.sh --publish-slot --publish-chat --publish-artifact --publish-searxng
./scripts/setup-tailscale.sh --status
```

With `ARTIFACT_LOGIN_FROM=unix` and `CHAT_LOGIN_FROM=unix` set before this
run, `:8446` and `:8445` proxy to the 0600 sockets, not to loopback TCP.
`tailscale serve` needs the box joined to the tailnet (step 4), and joining
needs a route to Tailscale's coordination server once.

**14. Device keys** (spark-1; [E7](#e7-device-keys-for-beast-gate)):
`./scripts/clients.sh enroll macbook --label "Max's MacBook"` prints the key
once. Put it straight into `~/.config/openbeast/spark-hydra-gate.key` (0600)
on the Mac. One key per device; the eval harness host gets its own.

**15. The Mac client** (the Mac; [F1](#f1-ssh-from-the-mac)–[F4](#f4-beast-apps-external-apps-through-mcp-servers)):
the ssh config with `HostKeyAlias`, then
`./scripts/setup-client.sh --host spark-1.tail4109f9.ts.net --name spark-hydra --api-key-stdin < ~/.config/openbeast/spark-hydra-gate.key`,
then `scripts/client-e2e.sh` (F2). Offline client install:
[BEAST_APPS.md](BEAST_APPS.md) § Offline client.

**16. zig 0.16.0 and the other language toolchains, optional** (spark-1, and
the harness host; [H7](#h7-test-and-language-toolchains)). Needed for
`language_reference` on zig and for the zig / go / rust eval variants. From an
ark drive: `ark restore zig`, `ark restore go`, `ark restore rust` (offline
and online alike). Online without a drive: the pinned zig tarball in H7.

**17. beast-docs mirror, optional** (sync on the Mac, unpack on spark-1;
[F5](#f5-beast-docs-platform-docs-mirrored), [H8](#h8-beast-docs-mirror)).
`docs.sh sync` + `pack` on a connected box, `docs.sh unpack` on spark-1. The
unpack is offline.

**18. beast-ark drive, optional** ([H8a](#h8a-beast-ark-the-rest-of-the-software-on-a-drive)).
`./scripts/ark.sh fetch --to <drive> --profile core --platform linux-aarch64`
on a connected box; `DRIVE/ark verify DRIVE` and `DRIVE/ark restore <name>`
on the Spark, offline.

**19. pytest, optional** (any Spark; [H7](#h7-test-and-language-toolchains)).
The hash-pinned venv `~/.venvs/openbeast-test`; offline from a wheelhouse.

**20. The eval harness host, optional** (the Mac or a Linux box;
[F6](#f6-the-eval-harness-host-benchmarking-the-pair)):
`./scripts/eval-host.sh setup` (zig 0.16.0, rustc 1.100.0, go 1.23.3, the
python 3.12 eval venv; `--from` an ark drive offline), gcc / g++ / git from
the OS, its own device key, and `EDGE_READ_TIMEOUT=3600` on spark-1 (step 11).

**21. Prove it** (spark-1 and the Mac; [G1](#g1-health)):

```bash
~/openbeast/scripts/healthcheck.sh          # on spark-1: "All 9 services healthy"
~/openbeast/scripts/doctor.sh               # on spark-1: exit 0 (no FAIL row)
sudo ~/openbeast/scripts/backends/spark-guard.sh --status   # on each Spark: "rules: current"
curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool | head -30   # from the Mac
nc -z -w 3 100.66.191.118 29551 && echo STILL OPEN || echo closed                           # from the Mac: closed
curl -s -o /dev/null -w '%{http_code}\n' https://spark-1.tail4109f9.ts.net:8443/v1/models    # from the Mac, no key: 401
```

---

## 0. The pair at a glance

### Names and addresses

| | spark-1 (head) | spark-2 (worker) |
|---|---|---|
| Role | rank 0 + API + command center | rank 1, nothing else |
| Tailnet | `spark-1` 100.66.191.118 (`spark-1.tail4109f9.ts.net`) | `spark-2` 100.110.143.15 (`spark-2.tail4109f9.ts.net`) |
| LAN (home) | 192.168.40.153 | 192.168.40.151 |
| ConnectX-7 rail 1 / rail 2 | 10.100.216.1 / 10.100.217.1 | 10.100.216.2 / 10.100.217.2 |
| Hostname / mDNS | `spark-3dc6` / `spark-3dc6.local` | `spark-9f26` / `spark-9f26.local` |
| NVIDIA Sync ssh alias | `spark-1` → `spark-3dc6.local` | `spark-2` → `spark-9f26.local` |

`spark-hydra` names the **pair**, never one box.

How to reach each box:

- **At home:** `ssh spark-1` works through NVIDIA Sync's `.local` alias.
- **Right after a reboot:** mDNS sometimes does not resolve. Use the LAN IP:
  `ssh -o HostName=192.168.40.153 spark-1`.
- **Away from home:** `.local` names never resolve. Use the tailnet names; see
  the ssh config in [F](#f1-ssh-from-the-mac).
- **spark-1 to spark-2:** use the fabric address, `ssh max@10.100.216.2`.
  Control traffic between the ranks never needs the LAN.

### What runs where

This table describes the GLM configuration. The active inference unit and container are listed in [DeepSeek deployment](#deepseek-deployment).

| Layer | Where | What |
|---|---|---|
| Inference | spark-1, `systemctl --user` unit `spark-hydra-inference` | Runs `~/openbeast-recipes/glm53/start.sh` in the FOREGROUND. This is Mia's recipe v1.8 (`33b50fd`): TensorFold v0.6.0 plus patches, in image `tensorfold-glm53:v0.6.0` (= `ghcr.io/miaai-lab/glm-5.3-flash-exl3-2x-dgx-sparks-tensorfold@sha256:cbb4b3c6…`). It starts rank 1 on spark-2 over ssh. Its settings are in `scripts/local.sh` ([D1](#d1-the-mia-recipe-what-serves-today)) |
| API | spark-1 | `http://127.0.0.1:8000/v1`, model `GLM-5.3-Flash-EXL3`. **Loopback only: v0.6.0 has no API key** |
| Command center | spark-1, `systemctl --user` unit `openbeast` | `~/openbeast`, a git checkout: WebUI :3000, tools :3001, SearXNG :8888, beast-gate :8090, beast-chat, beast-artifact, the dashboard (beast-slot), the island watcher. `openbeast.conf`: [E2](#e2-openbeastconf) |
| Weights | both | `~/openbeast-models/glm53-flash-ablit-exl3` (175.7 GB, 83 shards), hard-linked into `~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit/snapshots/57edefd2…`. Verified against the Hub revision (sha256, 0 mismatches) and locked in `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.lock` ([C](#c-weights)) |
| Drafter | both | `incoai/GLM-5.3-Flash-DFlash2 @ bf582e4e` (CC BY-NC-ND: personal use, Max's call). It lives in the HF cache only, in `hf download` layout (snapshot symlinks into `blobs/`). **No OpenBeast lock covers it**; [C4](#c4-the-drafter-dflash2) shows how to check it |
| Native TensorFold 1.2.7 | both | `~/.local/bin/tensorfold` (signed release). A challenger engine, **not serving** ([D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)) |
| spark-guard | both | `openbeast-spark-guard.service` (system unit, `active (exited)`): an nftables table that keeps the engines' rendezvous ports on the fabric ([B3](#b3-firewall-spark-guard)) |

**Checkouts, 2026-11-17 (evening).** Both Sparks are on one commit,
`d3727185` (#203, the thinking default). #204 changed only the Mac client
and docs, so neither Spark needs it to serve. (On 2026-11-16 evening they
were skewed, spark-1 at `29e5241a` (#181) and spark-2 at `de931dfe` (#176);
on 2026-11-15 spark-1 was at `ecf50a2` and spark-2 at `0988641`.) Keep them
on one commit: the "same repo
on every node" rule is what `spark-cluster.sh check` enforces, and
[G6](#g6-updating) step 4 pulls both. Run this on each Spark, quoting the command
for ssh:
`ssh spark-2.tail4109f9.ts.net 'cd ~/openbeast && git pull --ff-only'`.
Restarting the command center afterwards (`systemctl --user restart openbeast`)
is what makes a new commit live. Do it at a quiet moment.

---

## A. What you need

### A1. Hardware

| Item | Per Spark | Notes |
|---|---|---|
| NVIDIA DGX Spark (GB10, 128 GB unified memory, aarch64, DGX OS = Ubuntu 24.04) | 1 | the driver is R580 = CUDA 13.0. `nvidia-smi --query-gpu=memory.*` reports `[N/A]`, and that is how `scripts/lib/hardware.sh` classifies the host as `spark` |
| Disk free | ≥ 400 GB on the head, ≥ 250 GB on a worker | the GLM checkpoint is 175.7 GB. The HF-cache hard links cost nothing extra. DFlash2 is 2.3 GB, the recipe image tens of GB, and a carried image tarball the same again while it is staged |
| QSFP / ConnectX-7 cable (200 GbE) | see A2 | the fabric: NCCL over RoCE, the ranks' rendezvous, weight sync |
| Ethernet to the LAN | 1 | first boot, ssh at home, apt. Not needed by the ranks |
| A Mac (or Linux laptop) | — | NVIDIA Sync for first-boot addressing and the ssh alias, plus OpenBeast client mode |
| USB stick / external SSD | — | only for offline staging ([H](#h-fully-offline-operation)). Size it for the weights (≥ 256 GB) if you carry them |

### A2. Cabling and addressing for 1, 2, 3 and 4-8 Sparks

The scripts handle **1 to 8** Sparks, one GPU each, one tensor-parallel rank per
Spark. A profile's `TENSOR_PARALLEL_SIZE` therefore equals the node count.
**Only N = 1 and N = 2 have run on hardware.** Our pair is the N = 2 case.
N ≥ 3 is built and tested against stubs: VERIFY ON HARDWARE.

| Sparks | Fabric | Addressing | Notes |
|---|---|---|---|
| 1 | none | none | `TENSOR_PARALLEL_SIZE=1`, rank 0 only. `spark-guard.sh --no-fabric` makes the engine ports loopback-only |
| 2 | one QSFP cable between the two Sparks | NVIDIA Sync addresses both PCIe "rails" of the port (`10.100.216.0/24` and `10.100.217.0/24` on our pair) | both rails carry the all-gathers when both twins are addressed. **Measured** |
| 3 | a switch, or one fabric subnet that every Spark is on | every node on one subnet | a cabled ring with a subnet per cable cannot rendezvous through `spark-node.sh`, because every rank meets at one `SPARK_HEAD_IP`. `spark-cluster.sh check` flags this. The GLM recipe's `start-tp3.sh` (experimental) does handle a ring: LAN rendezvous plus `NCCL_IB_SUBNET_AWARE_ROUTING=1`. That is the measured 3-Spark path for GLM today |
| 4-8 | a RoCE-capable 200 GbE switch | every node on the fabric subnet(s) | each Spark has two QSFP ports, so no cabled topology reaches past 3 |

After cabling, check on each Spark that the links are Up and addressed:

```bash
ibdev2netdev                      # each ConnectX netdev and "(Up)"
ip -4 -o addr show | grep 10.100  # the fabric addresses NVIDIA Sync assigned
```

### A3. Accounts and credentials

| What | Why | Where it lives (never in git, never on argv) |
|---|---|---|
| Linux user (`max`) on every Spark, in `sudo` and `docker` | everything runs as this user; root only for [B](#b-bare-boxes-to-root-setup) | — |
| An ssh key from the Mac, and one from spark-1 to every worker | NVIDIA Sync installs the Mac's key. The head's key is what lets `start.sh` and `spark-cluster.sh` reach the workers over the fabric | `~/.ssh/` |
| Hugging Face account and token | downloading the weights. Only needed once, while the box is online | `~/.config/openbeast/hf-token` (0600). The recipe's `local.sh` reads it from there. `spark.env` names it with `HF_TOKEN_FILE` |
| Tailscale account (the tailnet `tail4109f9.ts.net`) | remote access, client mode, the published URLs | joined by `spark-root-setup.sh` (`tailscale up`, which prints a login URL on first run) |
| GitHub access to the repo | cloning `openbeast` | — |
| Open WebUI admin | the browser chat | created in [E5](#e5-webui-auth-and-the-admin-account-before-publishing) |
| beast-gate device keys | every client that calls inference | `scripts/clients.sh enroll` prints each key once ([E7](#e7-device-keys-for-beast-gate)) |

### A4. The artifact checklist (what to stage for an offline build)

Everything a Spark needs that does not come over the fabric, where it comes
from, and what checks it when it arrives. Section [H](#h-fully-offline-operation)
has the commands.

| Artifact | Size | Comes from | Checked by | Section |
|---|---|---|---|---|
| `libnccl2 2.28.9-1+cuda13.0` and `tailscale 1.104.7` .debs | ~200 MB | `apt-get download` on an online Spark | sha256 pins in `spark-root-setup.sh` (`--check-debs`) | [H3](#h3-debs-libnccl2-and-tailscale) |
| The OpenBeast repo at one commit | ~100 MB | `git clone` / `git bundle` | the commit id | [H2](#h2-the-repo) |
| GLM-5.3-Flash Ablit EXL3 weights | 175.7 GB | the Hub at `57edefd2…` | `model-fetch.sh --verify` against the committed lock | [C](#c-weights) |
| DFlash2 drafter | 2.3 GB | the Hub at `bf582e4e…` | the blob-hash loop | [C4](#c4-the-drafter-dflash2) |
| Recipe image `tensorfold-glm53:v0.6.0` | tens of GB | `prepare.sh`, or `docker pull` from ghcr | image ID `sha256:ead01740…` after `docker load` | [H4](#h4-container-images) |
| The recipe checkout `~/openbeast-recipes/glm53` | small | git (MiaAI-Lab) | commit `33b50fd` | [D1](#d1-the-mia-recipe-what-serves-today) |
| Native TensorFold 1.2.7 (optional) | ~100 MB | GitHub release | ed25519 signature + SHA256SUMS (`install-native.sh --stage` / `--from`) | [H5](#h5-native-tensorfold-and-the-engine-plane) |
| Python wheels for the command center | ~200 MB | `pydeps.sh wheelhouse` or `bundle.sh build` **on a Spark** | `agents/requirements.lock` hashes | [H6](#h6-the-command-center-wheels-and-compose-images) |
| Compose images (open-webui, searxng, extensions) | a few GB | `docker save` on an online Spark | digest pins; `bundle.sh install` rewrites them to the content ID | [H6](#h6-the-command-center-wheels-and-compose-images) |
| pytest (to run the test suites) | small | `.github/ci-requirements/test.txt` | `--require-hashes` | [H7](#h7-test-and-language-toolchains) |
| beast-docs mirror | MBs | `scripts/docs.sh sync` + `pack` | `unpack` re-hashes against `BUNDLE.json` | [H8](#h8-beast-docs-mirror) |
| zig 0.16 aarch64 (optional) | ~50 MB | ziglang.org | sha256 `ea4b09bf…` | [H7](#h7-test-and-language-toolchains) |

---

## B. Bare boxes to root setup

### B1. First boot

1. Boot each Spark, create the user (`max`), and connect the LAN.
2. Pair each Spark with NVIDIA Sync on the Mac. Sync addresses the ConnectX
   rails (it owns `/etc/netplan/99-nvidia-sync-cluster.yaml`; nothing in
   OpenBeast edits it) and installs the ssh alias and key.
3. Clone the repo on **every** Spark, at the **same commit**:

   ```bash
   git clone https://github.com/MaximilianKhan/openbeast ~/openbeast
   cd ~/openbeast && git checkout feat/spark-scale-1-8   # or whatever branch/commit the cluster runs
   git log --oneline -1                                 # write it down; every node must match
   ```

4. Give the head an ssh key that reaches every worker over the fabric. The
   recipe's `start.sh` and `spark-cluster.sh` use it:

   ```bash
   # on spark-1
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ''        # only if it has none
   ssh-copy-id max@10.100.216.2
   ssh max@10.100.216.2 true && echo fabric-ssh-ok
   ```

### B2. `spark-root-setup.sh`: the only root step (once per Spark)

```bash
cd ~/openbeast
sudo scripts/backends/spark-root-setup.sh spark-1 --guard     # on spark-1 (the head)
sudo scripts/backends/spark-root-setup.sh spark-2 --guard     # on spark-2 (each worker: spark-N)
```

The argument is the **tailnet name** the box joins as. Run it as yourself
through sudo, because it needs `SUDO_USER`. It is idempotent. It does four
things and nothing else:

1. **libnccl2 `2.28.9-1+cuda13.0`**, installed and held. The native TensorFold
   binary dlopens `libnccl.so.2`; without it, `tensorfold capabilities --json`
   shows NCCL `found:false`. The driver is R580 = CUDA 13.0, so this is the
   +cuda13.0 build, not apt's newest +cuda13.5.
2. **`loginctl enable-linger` for you**, so `systemctl --user` services run
   without a login session and survive logout.
3. **Tailscale from its signed apt repo.** The repo key is checked against the
   pinned fingerprint `2596A99EAAB33821893C0A79458CA832957F5868` before apt
   trusts it. Then it runs `tailscale up` with the given name and
   `--operator=$SUDO_USER`, so you can run `tailscale serve` without sudo.
   The first run prints a login URL.
4. **ufw allow rules** for each ConnectX link that is Up, from that link's own
   private subnet, for the day ufw gets enabled. **ufw is installed but
   disabled on DGX OS**, and this script does not enable it, because its
   default deny would also drop LAN ssh. While ufw is off those rules filter
   nothing, and the script prints a warning that says so. `--guard` then runs
   `spark-guard.sh` ([B3](#b3-firewall-spark-guard)), which is
   what actually closes the engine ports.

Other flags:

- `--no-tailscale` skips step 3, for a box with no tailnet to join.
- `--debs DIR` and `--check-debs DIR` are the offline path ([H3](#h3-debs-libnccl2-and-tailscale)).
- `--check-keyring FILE` checks a Tailscale keyring file against the pin, without root.
- `--serve [--port N]` adds a ufw rule for a **keyed** engine on `tailscale0`.
  Leave it off. Inference stays on loopback behind beast-gate.

What it never touches: netplan, the MTU, the driver, Docker. The fabric MTU
has its own opt-in root script, `spark-fabric-mtu.sh`
([B4](#b4-fabric-mtu-9000-spark-fabric-mtu)), which adds one netplan drop-in
for the next reboot and still leaves NVIDIA Sync's file alone.

**Expected output.** This is an excerpt assembled from the script's own
messages, not a captured transcript:

```
==> 1/4 libnccl2 2.28.9-1+cuda13.0
already installed                                   # or apt's install log, then the hold
libnccl.so.2 visible to the loader

==> 2/4 linger for max

==> 3/4 Tailscale as 'spark-1'
  Tailscale repo key verified (2596A99EAAB33821893C0A79458CA832957F5868)
tailnet IPv4: 100.66.191.118

==> 4/4 ufw (fabric allow rules) and exposure
  ufw fabric rules on 2 link(s)

  !! ufw is inactive — the ufw rules above are recorded but FILTER NOTHING.
  ...
  !!   sudo …/spark-guard.sh            (or re-run this with --guard)

==> spark-guard
== spark-guard installed: /etc/openbeast/spark-guard.nft, openbeast-spark-guard.service (enabled; loads before the network at boot)
  note: ufw is inactive. spark-guard does not need it and does not enable it.
Verify from the Mac: nc -z -w 3 <this Spark's tailnet IP> 29551   # must FAIL while a rank listens

Done on spark-3dc6 as tailnet 'spark-1' (100.66.191.118).
```

**Verify:**

```bash
dpkg-query -W libnccl2                          # 2.28.9-1+cuda13.0
apt-mark showhold | grep nccl                   # libnccl2
loginctl show-user "$USER" -p Linger            # Linger=yes
tailscale status | head -3                      # this box, online
tailscale ip -4
```

**The docker group and the user manager (reboot once).** If you were added to
the `docker` group during setup (`sudo usermod -aG docker max`), the systemd
**user** manager that runs `openbeast` and `spark-hydra-inference` was started
before the group change and cannot reach Docker. The symptoms are WebUI and
SearXNG DOWN, and `.run/stack.log` saying "docker daemon not ready". Logging
out is not enough, because linger keeps the user manager alive. **Reboot once.**
Then check:

```bash
systemd-run --user --wait --pipe docker info >/dev/null && echo user-manager-sees-docker
```

### B3. Firewall: spark-guard

**Why it exists (found 2026-11-15).** ufw is installed but **disabled** on both
Sparks (`/etc/ufw/ufw.conf` has `ENABLED=no`), so nothing was filtered. Rank 0's
TensorFold TCPStore listens on `*:29551` with no authentication, and it answered
`nc -z 100.66.191.118 29551` from the Mac over the tailnet (very likely the LAN
too). Enabling ufw would not have closed the tailnet path, because Tailscale's
own `ts-input` chain accepts `tailscale0` traffic before ufw's rules run. sshd
also accepts passwords on `0.0.0.0:22`.

**What it does.** `scripts/backends/spark-guard.sh` closes the engine ports and
touches nothing else:

- It loads one nftables table of its own, `inet openbeast_guard`, with an input
  hook at priority `filter - 10`. That runs ahead of iptables-nft (ufw, Docker)
  and Tailscale.
- It drops TCP to 29501-29502, 29551-29552 and 29583-29584 (plus spark.env's
  master ports, each with its +1, and any `--port N`). Every rendezvous port is
  guarded with its +1, whether it is a default or comes from spark.env, so the
  same port never renders two ways. It also drops TCP to the Spark's own fabric
  addresses, where NCCL's ephemeral bootstrap listeners are. Both drops apply
  **unless** the packet arrives on `lo` or on a ConnectX netdev
  (`ibdev2netdev`).
- It never flushes the ruleset. Ubuntu's `nftables.service` would flush it,
  wiping Docker's and Tailscale's rules, so that service is not used. Instead
  its own oneshot unit, `openbeast-spark-guard.service`, reloads
  `/etc/openbeast/spark-guard.nft` before `network-pre.target` at boot.

Unaffected: RoCE (RDMA bypasses netfilter), the NCCL bootstrap on the fabric,
ssh, `tailscale serve` (:443, :8443-8447, :8889) and Docker networking.

```bash
# on EACH Spark, from ~/openbeast at a commit that has it
scripts/backends/spark-guard.sh --render          # read the ruleset first (no root)
sudo scripts/backends/spark-guard.sh              # load + persist (idempotent); same as --apply
sudo scripts/backends/spark-guard.sh --status     # current or STALE, table, counters, listeners, ssh posture
sudo scripts/backends/spark-guard.sh --remove     # undo: table, unit and rules file gone
#   --port N (repeatable)  --iface IF (repeatable)  --no-fabric (one Spark, no peer: loopback-only)
```

What `--render` prints on spark-1 (2026-11-16, current code):

```
# fabric: enp1s0f0np0 enp1s0f1np1 enP2p1s0f0np0 enP2p1s0f1np1   fabric IPv4: 10.100.216.1 10.100.217.1   guarded TCP: 29501-29502 29551-29552 29583-29584
# args:
table inet openbeast_guard {
	chain input {
		type filter hook input priority filter - 10; policy accept;
		iifname "lo" accept
		iifname { "enp1s0f0np0", "enp1s0f1np1", "enP2p1s0f0np0", "enP2p1s0f1np1" } accept comment "ConnectX-7 fabric"
		tcp dport { 29501-29502, 29551-29552, 29583-29584 } counter drop comment "engine rendezvous: fabric and loopback only"
		ip daddr { 10.100.216.1, 10.100.217.1 } meta l4proto tcp counter drop comment "fabric addresses (NCCL bootstrap): fabric only"
	}
}
```

`--status` without root still shows the unit and the listeners. It says
`loaded: unknown (listing nftables needs root …)` for the table. It also
re-renders the ruleset from the current code, spark.env and fabric (with the
`--port` / `--iface` / `--no-fabric` inputs the install recorded on its
`# args:` line) and compares it with the installed file: `rules: current`, or
`rules: STALE` with the lines that differ and the command to re-run. On
2026-11-16 both Sparks showed STALE: their files, written 2026-11-15 before
`VLLM_MASTER_PORT=29501` reached spark.env, guard `29501` but not `29502`.
The fix is one `sudo scripts/backends/spark-guard.sh` on each Spark (an
operator step, offline-safe: it reads only local files):

```
== spark-guard on spark-3dc6
rules file: /etc/openbeast/spark-guard.nft
rules: STALE — the installed file differs from what this checkout renders now (code, spark.env ports or fabric changed). Re-run: sudo scripts/backends/spark-guard.sh
  installed: # fabric: … guarded TCP: 29501 29551-29552 29583-29584
  …
  now:       # fabric: … guarded TCP: 29501-29502 29551-29552 29583-29584
unit: openbeast-spark-guard.service enabled, active
TCP listeners (what each could be reached on):
  0.0.0.0:22
  100.66.191.118%tailscale0:443      … :8443 :8444 :8445 :8446 :8889   (tailscale serve)
  10.100.216.1:35335 …                                               (NCCL bootstrap, fabric only)
  127.0.0.1:3000 :3001 :3002 :3003 :3004 :8000 :8090 :8888           (the stack, loopback)
```

**Verify from another device** (the Mac, while the pair is serving):

```bash
nc -z -w 3 100.66.191.118 29551 && echo STILL OPEN || echo closed    # must print closed (measured 2026-11-15: closed)
nc -z -w 3 192.168.40.153 29551 && echo STILL OPEN || echo closed    # LAN, from home
nc -z -w 3 100.66.191.118 22 && echo ssh-still-reachable              # ssh must stay open
curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | head -c 80 # tailscale serve still works
```

The pair keeps serving through the load, because the fabric is accepted before
the drops. After a reboot, `systemctl status openbeast-spark-guard` shows
`active (exited)`, and the counters in `sudo … --status` count the drops.
Re-run the guard whenever the fabric addresses change (NVIDIA Sync
re-addressing, a new switch), after a pull that changes the guard, and after
editing a master port in spark.env. `--status` says `STALE` when one of those
happened and the guard was not re-run.

**sshd hardening is opt-in and OFF by default.**
`sudo scripts/backends/spark-guard.sh --harden-ssh --yes` writes
`/etc/ssh/sshd_config.d/00-openbeast-guard.conf` (`PasswordAuthentication no`,
`KbdInteractiveAuthentication no`), validates it with `sshd -t`, and reloads
sshd. It refuses unless your account has a key in `authorized_keys`. Both
Sparks have one: the Mac uses the NVIDIA Sync key, and spark-1 to spark-2 uses
spark-1's own key. Its warning explains how not to lock yourself out:

1. Run it from an open ssh session.
2. From the Mac, test a NEW key login before closing that session.
3. From spark-1, also test `ssh max@10.100.216.2 true`.

`--unharden-ssh` undoes it.

### B4. Fabric MTU 9000: spark-fabric-mtu

**Status, 2026-11-17: LIVE on both Sparks since 14:20.** Staged with
`--stage` on each, both rebooted (worker, then head), then `--verify` passed
on both: netdev MTU 9000 and RoCE `active_mtu 4096` on every rail, and
8972-byte don't-fragment pings to the peer over both rails. `--status` on
each Spark (no sudo) reads `MTU(now) 9000`, `active_mtu 4096` on all four
ConnectX netdevs. **The performance A/B is not measured yet** (the plan is
below); until it is, no speed gain is claimed anywhere.

Before 2026-11-17 (Max, 2026-11-16: "We absolutely need to fix this on the
next restart.") the pair ran at netdev MTU 1500 on the ConnectX rails, which
gives RoCE `active_mtu 1024` (seen live on spark-1: `ip -d link` said
`mtu 1500 … maxmtu 9978`, and `ibv_devinfo -d rocep1s0f0` said
`max_mtu 4096 (5)`, `active_mtu 1024 (3)`). The target was netdev MTU 9000,
which gives `active_mtu 4096`.

**Why it matters.** RoCE carries each RDMA message in packets of
`active_mtu` bytes. At 1024, a bulk transfer is 4x as many packets as at
4096, with the per-packet headers, completions and NIC work that go with
them. Two kinds of traffic on this pair are bulk:

- **Prefill all-gathers.** A long prompt (tens of thousands of tokens) moves
  large activation tensors between the two ranks at every layer. This is
  where the TTFT of a big prompt goes, and where a bigger MTU should show.
- **Weight-sync and other bulk copies** over the fabric (C3's carry to the
  worker, any NCCL broadcast of large buffers).

TP-2 decode all-reduces are small (one token's activations) and
latency-bound, so the 4-stream decode tok/s should barely move. That is a
prediction, not a measurement: the A/B below is how we find out.

**What the script does.** `scripts/backends/spark-fabric-mtu.sh` (root, one
Spark per run, like `spark-guard.sh`):

- Finds the fabric netdevs with `ibdev2netdev`: every ConnectX RoCE port, Up
  or Down (the cable uses different ports on the two boxes: spark-1's
  `enp1s0f0np0` / `enP2p1s0f0np0` are Up, spark-2's `enp1s0f1np1` /
  `enP2p1s0f1np1`). It leaves out any netdev that carries a default route and
  any netdev netplan does not already manage, and refuses if none is left.
  The LAN NIC `enP7s7` is not a ConnectX port, so it is never a candidate.
- Writes ONE file, `/etc/netplan/99-zz-openbeast-fabric-mtu.yaml` (0600
  root):

  ```yaml
  network:
    version: 2
    ethernets:
      enp1s0f0np0:
        mtu: 9000
      enp1s0f1np1:
        mtu: 9000
      enP2p1s0f0np0:
        mtu: 9000
      enP2p1s0f1np1:
        mtu: 9000
  ```

  It sorts after NVIDIA Sync's `99-nvidia-sync-cluster.yaml`, and netplan
  merges a later file's keys into the same interface: Sync's addresses stay,
  only `mtu` is added, and Sync's file is never edited.
- Runs `netplan generate` to validate the merged config and write the
  boot-time backend files (on the pair: NetworkManager keyfiles in
  `/run/NetworkManager/system-connections/netplan-<dev>.nmconnection`). It
  then checks that netplan rendered the same interfaces with the same backend
  as before and `mtu=9000` on each. If not, it puts the old state back and
  says so. Checked against the real netplan 1.1.2 (the pair's version) with a
  copy of Sync's layout: the drop-in merges, the address is kept.
- **Never runs `netplan apply`.** `generate` only writes `/run` configs and
  reloads udev rules and systemd units. The live links keep MTU 1500 until
  the reboot, so the two ends of the cable never disagree under a running job.

**Why both Sparks must reboot.** If one end of the cable is at 9000 and the
other at 1500, every frame over 1500 bytes is dropped on that link. ping and
ssh keep working (small packets), so it looks healthy, while NCCL's bulk
transfers stall or hang the collective. Stage both, reboot both, verify both.
Until the reboot, a NetworkManager restart or a fabric link flap could bring
the new MTU up early on one box. `--status` shows it (the "MTU(now)" column).
If you see 9000 on one Spark only, reboot the other one too.

**Step 1: stage on each Spark (from the Mac).** `-t` gives sudo a terminal
for the password:

```bash
! ssh -t spark-1.tail4109f9.ts.net 'cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --stage'
! ssh -t spark-2.tail4109f9.ts.net 'cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --stage'
```

(The leading `!` runs the line in a shell from a Claude Code prompt; drop it
in a plain terminal.) Each checkout must have this script: `git pull` first
if it does not. `--dry-run` (no sudo) prints the file and the actions first.
Expected output ends with:

```
== netplan generate (validates the merged config; the live links are not touched)
   enp1s0f0np0: boot config now mtu 9000 (NetworkManager)
   ...
STAGED on spark-3dc6. Nothing changed on the live links (still MTU 1500).
It takes effect on the NEXT REBOOT, and that reboot must happen on BOTH Sparks:
```

Check either box any time, no sudo needed for the live columns:

```bash
ssh spark-1.tail4109f9.ts.net '~/openbeast/scripts/backends/spark-fabric-mtu.sh --status'
#   NETDEV          IBDEV          STATE MTU(now)  active_mtu  boot config   in drop-in
#   enp1s0f0np0     rocep1s0f0     Up    1500      1024        (sudo)        no
```

The boot-config and drop-in columns need sudo to read (the files are 0600);
with sudo they show `9000` / `yes` once staged.

**Step 2: measure A (before), still at MTU 1500.** See the A/B plan below.
Staging does not change the live links, so A can run before or after Step 1.

**Step 3: reboot both.** Stop inference first, so no collective is in flight
while one box is at 9000 and the other at 1500. Then reboot the worker,
then the head:

```bash
ssh spark-1.tail4109f9.ts.net 'systemctl --user stop spark-hydra-inference'
! ssh -t spark-2.tail4109f9.ts.net 'sudo reboot'
! ssh -t spark-1.tail4109f9.ts.net 'sudo reboot'
```

Nothing else is needed. `spark-hydra-inference` is enabled on spark-1, starts
by itself at boot, and starts rank 1 on spark-2 over ssh. If spark-2 is not
up yet, the unit fails and retries every 30 s (`Restart=on-failure`). Loading
takes 2-6 min. spark-guard (B3) loads its own table before the network comes
up, as on any boot. Then `scripts/healthcheck.sh` on spark-1 should report
all 9 services healthy (E4).

**Step 4: verify, on each Spark.** `--verify` takes the **peer's** fabric
IPv4 on every Up rail, **all of them in one call**. Running it once per IP
fails: each run checks every local Up rail, and a rail with no peer IP given
is a FAIL. Read the IPs on the peer, do not copy them from here (Sync can
re-address the rails):

```bash
ssh spark-2.tail4109f9.ts.net "ip -br addr | grep -E '^enp1|^enP2'"   # the peer's rail addresses
ssh spark-1.tail4109f9.ts.net '~/openbeast/scripts/backends/spark-fabric-mtu.sh --verify <spark-2 rail-1 IP> <spark-2 rail-2 IP>'
ssh spark-1.tail4109f9.ts.net "ip -br addr | grep -E '^enp1|^enP2'"
ssh spark-2.tail4109f9.ts.net '~/openbeast/scripts/backends/spark-fabric-mtu.sh --verify <spark-1 rail-1 IP> <spark-1 rail-2 IP>'
```

No sudo is needed. For each Up rail it checks netdev MTU 9000 and RoCE
`active_mtu 4096` (`ibv_devinfo -d <ibdev>`). For each peer IP, it checks
that the IP routes over a fabric rail, then runs
`ping -M do -s 8972 -c 3 -W 2 -I <rail> <peer>`. That is a 9000-byte frame
with don't-fragment set, which only gets through if both ends are at 9000. A
rail with no peer IP given is a failure, so give one per rail. It prints
`VERIFIED` and exits 0, or names each `FAIL` and exits 1.

**The A/B measurement plan.** Same model, same settings, same prompts, only
the MTU differs. Run on spark-1 against the loopback API (no key, no gate;
stdlib Python, works offline). Two numbers:

1. **TTFT on a ~45K-token prompt.** A non-streaming request with
   `max_tokens: 1` takes prefill plus one token, so its wall time is the TTFT.
   Each run gets a fresh nonce at the **start** of the prompt, so no prefix
   cache can serve it. 5 runs; the first one is a warmup, report the median
   of the other 4.
2. **4-stream decode tok/s.** Four concurrent requests with a short prompt
   and `max_tokens: 512`, at temperature 0. Report the aggregate (sum of
   `completion_tokens` / wall time) and the per-stream rate. 3 rounds, median.
   DFlash2's speedup depends on the text, so the prompts must be identical
   between A and B (they are fixed below).

```bash
ssh spark-1.tail4109f9.ts.net 'python3 - ' <<'PY'
import json, statistics, threading, time, urllib.request, uuid
URL, MODEL = "http://127.0.0.1:8000/v1/chat/completions", "GLM-5.3-Flash-EXL3"
def call(content, max_tokens):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": content}],
                       "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=900) as r:
        u = json.load(r)["usage"]
    return time.monotonic() - t, u
filler = "".join(f"Record {i}: sensor {i % 97} read {i * 37 % 1009} units at step {i}.\n" for i in range(2500))
ttft = []
for n in range(5):
    dt, u = call(f"[{uuid.uuid4()}]\n{filler}\nReply with one word.", 1)
    print(f"ttft run {n}: {dt:.2f} s, prompt_tokens {u['prompt_tokens']}")
    if n: ttft.append(dt)
print(f"TTFT median (runs 1-4): {statistics.median(ttft):.2f} s")
prompts = [f"Write a detailed Python implementation of {t}, with comments." for t in
           ("a red-black tree", "an LRU cache", "a JSON tokenizer", "Dijkstra's algorithm")]
aggs = []
for rnd in range(3):
    res = [None] * 4
    def run(i): res[i] = call(prompts[i], 512)
    th = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    t = time.monotonic(); [x.start() for x in th]; [x.join() for x in th]; wall = time.monotonic() - t
    toks = sum(u["completion_tokens"] for _, u in res)
    aggs.append(toks / wall)
    print(f"round {rnd}: {toks} tokens in {wall:.1f} s = {toks / wall:.1f} tok/s aggregate, "
          + ", ".join(f"{u['completion_tokens'] / dt:.1f}" for dt, u in res) + " per stream")
print(f"4-stream aggregate median: {statistics.median(aggs):.1f} tok/s")
PY
```

**Status of the A/B, 2026-11-17: not run.** No MTU-1500 "A" was recorded
before the reboot, so the B run at 9000 needs a baseline: either an `--unstage`
window (reboot both into 1500, run A, `--stage` and reboot again) or, weaker,
the 2026-11-13 numbers below, which differ in more than the MTU (the KV pool
size since 2026-11-15). Record which one was used.

Check that `prompt_tokens` is near 45K (2500 filler lines is an estimate of
~18 tokens a line; change `range(2500)` until it is, then keep it the same for A and B).
Record both runs here, under "Serving configuration", with the date and
`active_mtu` (1024 for A, 4096 for B). For reference, the 2026-11-13 numbers
are ~145 / 224 tok/s aggregate (prose / code), TTFT ~160-260 ms on short
prompts. Expect B's TTFT to drop on the long prompt and decode to be about
flat. If B is not better, or worse, keep the numbers anyway and decide
whether to roll back. A failed experiment is still a result.

**Rollback.** Any time, on both Sparks, then reboot both:

```bash
! ssh -t spark-1.tail4109f9.ts.net 'cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --unstage'
! ssh -t spark-2.tail4109f9.ts.net 'cd ~/openbeast && sudo scripts/backends/spark-fabric-mtu.sh --unstage'
```

`--unstage` removes the drop-in and re-runs `netplan generate`. Like
`--stage`, it takes effect at the next reboot, so reboot both. If the fabric
does not come up after the reboot, and you cannot reach a Spark over the
fabric or the tailnet, the LAN still works: `enP7s7` was never touched. Use
`ssh -o HostName=<LAN IP> spark-N` (0. The pair at a glance). Failing that,
use the box's own console (keyboard and monitor). From there:

```bash
sudo rm /etc/netplan/99-zz-openbeast-fabric-mtu.yaml
sudo netplan generate && sudo reboot
```

**Offline.** None of this needs a network beyond the boxes themselves. The
script, netplan, `ibdev2netdev`, `ibv_devinfo` and `ping` are all local. The
verify pings travel the direct QSFP cable, and the A/B runs on loopback. On a
pair with no tailnet, run the same commands over LAN ssh or at the console.

**Tests.** `tests/test_spark_fabric_mtu.sh` (CI's spark shell job and
`tests/run_tests.sh`) runs every mode against fakes of `ibdev2netdev`,
`netplan`, `ip`, `ibv_devinfo` and `ping`, under a scratch
`SPARK_FABRIC_ROOT`. It covers the file's content and perms, that `apply` is
never called, refusing the default-route NIC, the rollbacks, unstage, status
and verify failures. On Ubuntu it also checks the merge with the real `netplan generate
--root-dir`. Test seams: `SPARK_FABRIC_ROOT` (a path prefix, also passed to
netplan as `--root-dir`), `SPARK_FABRIC_NETPLAN_DIR`, `SPARK_FABRIC_NM_DIR`,
`SPARK_FABRIC_NETWORKD_DIR`, and `IBDEV2NETDEV` / `NETPLAN` / `IP` /
`IBV_DEVINFO` / `PING` for the commands.

---

## C. Weights

The recipe and OpenBeast's own launchers read weights in two different places.
Getting this wrong is the most common way to serve unverified bytes, so here is
the model:

- **OpenBeast's copy** is `~/openbeast-models/<profile>`. `model-fetch.sh`
  writes it, verifies it against the Hub's own hashes for one commit, and
  **locks** it: `scripts/backends/models/<profile>.lock` records each file, its
  size and its sha256. The lock is committed.
- **The recipe's copy** is the HF-cache layout,
  `~/.cache/huggingface/hub/models--<org>--<name>/snapshots/<rev>/`. The
  recipe's `snapshot()` reads only that, and it checks `config.json` there on
  every rank.
- **Hard links make them the same bytes.** Then the lock vouches for what the
  recipe serves. A re-download, or an editor save that replaces a file by
  rename, breaks the link silently. Check `stat -c %h` (≥ 2) after anything
  touches the cache.

### C1. Fetch (online) or verify a local copy

Profile: `scripts/backends/models/glm53-flash-ablit-exl3-tensorfold.env`, with
its lock committed next to it.

```bash
cd ~/openbeast
# online, from the Hub: downloads only the pinned REVISION, verifies, renames into place
HF_TOKEN_FILE=~/.config/openbeast/hf-token scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold

# a copy that is already on disk (a USB SSD, rsync from another box): re-hash against the committed lock
scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify
scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --locate   # what launchers call: fast check, prints the dir
```

Its guarantees:

- Only the profile's `REVISION` (a commit SHA) is ever requested.
- Every file is checked against the Hub's hash for that commit: LFS files by
  sha256, small files by git blob SHA-1.
- Nothing appears under the final name until every file has passed. Downloads
  stage in `MODELS_DIR/.NAME.partial`, and an interrupted run resumes.
- The lock is written on the first fetch and never rewritten. Every later
  fetch or verify must reproduce it exactly.
- Pickles, GGUF and `original/` copies are skipped unless `FETCH_INCLUDE` names
  them.

Exit codes:

- `--verify` and `--locate` exit **3** when there is no lock. Run them in a
  checkout that has the committed lock.

`--verify` (and recording a local lock) hashes 4 files at once
(`MODEL_FETCH_HASH_WORKERS`, `1` = serial). On a GB10 one thread hashes about
1.8 GB/s and four about 5.5 GB/s (measured on spark-2, 2026-11-16), so the
164 GB tree takes about half a minute instead of about a minute and a half.
The report is in the lock's order whatever the timing.
- For a local `SOURCE` with no lock, `--verify --trust-first-use` records a
  lock from the bytes on disk. That is "trust on first use": say so in the
  commit.

`--verify` used to fail with "unlocked file .cache/huggingface/…" on a copy
made with `hf download --local-dir`. This is fixed: hidden paths are skipped by
both lock and verify.

### C2. Hard-link into the HF-cache layout the recipe reads

On spark-1:

```bash
REV=57edefd2f5d9b371c8345883304d5af68b52fa24
C=~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit
mkdir -p "$C/refs" "$C/snapshots/$REV"
printf %s "$REV" > "$C/refs/main"
cp -al ~/openbeast-models/glm53-flash-ablit-exl3/. "$C/snapshots/$REV/"   # hard links, no copy
stat -c '%h %i %n' ~/openbeast-models/glm53-flash-ablit-exl3/config.json "$C/snapshots/$REV/config.json"  # same inode, links >= 2
```

### C3. Carry the weights to every worker

There are two ways, and both keep the hard links.

**By hand (what built our pair).** Send both trees in ONE rsync. `-H` only
links files within one transfer, and `--relative` with `/./` keeps the paths
under the remote home:

```bash
rsync -aH --partial --relative ~/./openbeast-models/glm53-flash-ablit-exl3 \
  ~/./.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold-Ablit max@10.100.216.2:
ssh max@10.100.216.2 'cd ~/openbeast && scripts/backends/model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify'
```

`--verify` on spark-2 needs the lock in spark-2's own checkout. If it is
missing, `--verify` fails rather than recording one. The verify vouches for the
snapshot only while the links hold, so re-run the `stat` line from C2 on each
Spark after anything rewrites the cache.

**With `spark-cluster.sh sync-weights`.** This is the general 1-8 node path. It
needs `scripts/backends/cluster.env` on the head (see
[D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)):

```bash
scripts/backends/spark-cluster.sh sync-weights --profile glm53-flash-ablit-exl3-tensorfold [--dry-run]
```

What it does:

- It refuses unless the head's lock pins every artifact (the model, plus the
  drafter if the profile names one).
- It checks that each worker's profile and lock hash-match the head's. A worker
  that has no lock gets the head's.
- It copies over the fabric, verifies on arrival, and confirms the worker's own
  launcher will find the copy.
- It copies each directory as `SYNC_STREAMS` (default 4, 1..16) parallel
  whole-file rsyncs, the files split by size. One ssh stream is
  cipher-bound on a GB10: measured spark-1 → spark-2 on 2026-11-16, one
  stream did 0.44-0.48 GB/s (about 6 min for the 164 GB tree) and four did
  1.73 GB/s together (about 1.6 min). Four leaves the 200 Gb link room for
  the RoCE all-gathers while the pair serves. The hand-made rsync above
  stays one stream on purpose: `-H` keeps hard links only within one
  transfer.

It syncs `~/openbeast-models/<profile>` (OpenBeast's layout). For the recipe,
still make the HF-cache hard links on each worker as in C2. On our pair the
hand-made rsync above did both at once. Untested on our pair, since the pair
has never run `sync-weights`, though spark-1 does have a `cluster.env`
(written 2026-11-15: `CLUSTER_NODES="local max@10.100.216.2"`,
`CLUSTER_NAME=spark-hydra`, `CLUSTER_REPO=~/openbeast`). Tested against stubs
in `tests/test_spark_cluster.sh`.

### C4. The drafter, DFlash2

`incoai/GLM-5.3-Flash-DFlash2 @ bf582e4eacc1810f76656d1811693ff6c6737d2a`
lives only in the HF cache, in `hf download` layout, and no OpenBeast lock
covers it. Copy the whole repo dir; its relative symlinks into `blobs/` survive
`rsync -a`. Then check every blob against its own name. LFS blobs are named by
their sha256, small files by their git blob SHA-1:

```bash
D=.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2
rsync -a --partial --relative ~/./$D max@10.100.216.2:     # or from a USB copy of the same dir
cd ~/$D/blobs && for b in *; do
  case ${#b} in
    64) [ "$(sha256sum < "$b" | cut -c1-64)" = "$b" ] || echo "BAD $b" ;;
    40) [ "$(git hash-object "$b")" = "$b" ] || echo "BAD $b" ;;
  esac
done; echo checked
readlink ~/$D/snapshots/bf582e4eacc1810f76656d1811693ff6c6737d2a/model.safetensors
# -> ../../blobs/b038e1d9d1e7833fa3880c2c0135ba9b673013f03da1b29fb831931584759dac (2.34 GB)
```

That proves the blobs are the bytes their names claim. That the snapshot is the
pinned revision rests on the names above (read off spark-1, 2026-11-15).

---

## D. Inference

There are three ways to run the engine. **D1 is what serves the pair today.**
D2 is OpenBeast's own general launcher, the challenger. D3 is the one-command
engine plane for single-host and pin management.

<a id="d1-the-mia-recipe-what-serves-today"></a>

### D1. The GLM recipe (rollback deployment)

The recipe is MiaAI-Lab's GLM-5.3-Flash 2× DGX Spark recipe, v1.8, commit
`33b50fd`, cloned to `~/openbeast-recipes/glm53` on spark-1. Its `start.sh`
starts rank 1 on the worker over ssh, then rank 0 locally. It stays attached to
rank 0's log, and when either rank ends it stops the other. `start-tp3.sh` is
its experimental 3-Spark variant.

**`~/openbeast-recipes/glm53/scripts/local.sh` on spark-1 (2026-11-15, the
live values).** `scripts/config.sh` sources it, and
`scripts/local.sh.example` documents every knob.

```bash
# OpenBeast spark-hydra settings (written 2026-11-13). Sourced by scripts/config.sh.
WORKER=max@10.100.216.2        # spark-2 over the ConnectX-7 link (rank 1)
ABLIT=1                        # Mia's abliterated checkpoint @ 57edefd2 (staged locally, not re-downloaded)
DRAFTER=dflash2                # Max 2026-11-13: DFlash2 (CC BY-NC-ND, personal use) — 4 streams
# v0.6.0 has no API key: listen on loopback only. Everything outside reaches it
# through spark-hydra's beast-gate (:8443, per-device keys). 8888 is SearXNG's.
HOST=127.0.0.1
PORT=8000
# The token lives in a 0600 file, never in this file or on argv.
[[ -r "$HOME/.config/openbeast/hf-token" ]] && HF_TOKEN="$(<"$HOME/.config/openbeast/hf-token")"
# 2026-11-15 (adversarial review, ops lens): keep memory headroom for the command
# center beside rank 0 on unified memory. A fixed pool instead of "whatever is free
# at start"; a bigger host reserve; a stalled rank exits so systemd restarts the pair.
KV_POOL_GIB=10
MEMORY_RESERVE_GIB=17.5
export TF_GLM_MULTI_WATCHDOG_EXIT=1
```

What the three memory lines do. These values are live: the running container's
environment shows `TF_GLM_CACHE_GIB=10`, `TENSORFOLD_MEMORY_RESERVE_GIB=17.5`
and `TF_GLM_MULTI_WATCHDOG_EXIT=1`.

- **`KV_POOL_GIB=10`** becomes `TF_GLM_CACHE_GIB`. It caps the KV pool at a
  fixed size instead of "whatever is free at start". Unified memory freezes
  rather than OOMs, so a pool that grows into the command center's share is a
  hang, not an error.
- **`MEMORY_RESERVE_GIB=17.5`** becomes `TENSORFOLD_MEMORY_RESERVE_GIB`. It is
  the host memory TensorFold leaves free at start, for the command center
  beside rank 0. The recipe's default is 14.5 plus extras.
- **`TF_GLM_MULTI_WATCHDOG_EXIT=1`**: a rank whose collective stalls past the
  watchdog (`TF_GLM_MULTI_WATCHDOG_S`, 300 s) exits instead of hanging.
  `start.sh` then stops the other rank, and systemd's `Restart=on-failure`
  restarts the pair.

Even with these values, spark-1 has only about **9.7-9.9 GiB** of MemAvailable
while serving with the full command center up (healthcheck, 2026-11-15).
[G4](#g4-memory-headroom-and-the-gb10-stack-cap) covers what that means.

**First run (online).** `scripts/prepare.sh` is the recipe's own staging
script. It pulls or builds the image, downloads weights and the drafter, and
copies to the worker. Read the recipe's README before running it. On our pair,
the weights were staged by OpenBeast ([C](#c-weights)) and only the image came
from the recipe.

**Install the unit (on spark-1):**

```bash
mkdir -p ~/.config/systemd/user
sed "s|@RECIPE@|$HOME/openbeast-recipes/glm53|" ~/openbeast/scripts/backends/spark-hydra-inference.service \
  > ~/.config/systemd/user/spark-hydra-inference.service
systemctl --user daemon-reload
systemctl --user enable --now spark-hydra-inference
journalctl --user -fu spark-hydra-inference      # loading takes 2-6 min
curl -s http://127.0.0.1:8000/health             # {"ok": true, "backend": "tensorfold", ... "streams": {... "max": 4}}
```

What the unit sets:

- `Environment=FOREGROUND=1 PREPARE=0`. With `PREPARE=0` the unit never reaches
  the Hub, ghcr or the worker's setup at boot. `HF_HUB_OFFLINE=1` is the
  recipe's own default.
- `TimeoutStartSec=infinity`, `Restart=on-failure`, `RestartSec=30`.

Before starting it, two things must be true:

- **Both Sparks are guarded** ([B3](#b3-firewall-spark-guard)).
- **The user manager sees the docker group** (reboot once, [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark)).

**Changing a recipe setting:**

1. Back it up: `cp scripts/local.sh scripts/local.sh.bak-$(date +%Y%m%d%H%M)`.
2. Edit `~/openbeast-recipes/glm53/scripts/local.sh`.
3. Restart: `systemctl --user restart spark-hydra-inference`. Loading takes 2-6 min.

**While the unit is enabled, start and stop the pair only through
`systemctl --user`.** Never call the recipe's `start.sh` or `stop.sh` by hand,
and never `docker stop` a rank. systemd would see the exit and restart the pair
behind you (`Restart=on-failure`), or the unit would think the pair is up when
it is not.

### D2. Native TensorFold through `spark-node.sh` / `spark-cluster.sh`

OpenBeast's own launcher. It is general (any profile, 1 to 8 ranks), keyed
(`--api-key-file`), and launches only verified local weights. On our pair it is
the **challenger**: native TensorFold 1.2.7 is installed on both Sparks but does
not serve. Serving GLM-5.3-Flash on the pair through D2 is VERIFY ON HARDWARE,
and so is comparing it with the recipe ("Not done yet").

**Install the engine** on every Spark. `install-native.sh` verifies three
things, all required: the `.sha256` names exactly this archive, its ed25519
signature checks against the vendored `release-key.pem`, and every unpacked file
matches `SHA256SUMS`.

```bash
scripts/backends/tensorfold/install-native.sh                     # latest, download + verify + install
scripts/backends/tensorfold/install-native.sh --version 1.2.7     # a specific version
tensorfold capabilities --json | head                             # NCCL must be found:true (root setup installs it)
```

**Per-node settings: `scripts/backends/spark.env`**, copied from
`spark.env.example` on **each** node and parsed, never sourced. The keys that
matter:

| Key | Meaning |
|---|---|
| `TENSORFOLD_ENGINE` | `native` (default) or `container` (the frozen 0.x engine, which pip-installs from GitHub/PyPI at every start: needs the internet every time). A real container run also needs its dependencies pinned: `TENSORFOLD_IMAGE_PREBUILT=true` (an image that already holds them, pinned by `TENSORFOLD_IMAGE_ID`; nothing installed at start, so it is also the offline route), `TENSORFOLD_PIP_LOCK=FILE` (hash-locked requirements), or the acknowledged `TENSORFOLD_ALLOW_UNPINNED_DEPS=true` |
| `TENSORFOLD_VERSION`, `TENSORFOLD_BIN` | `1.2.7`, `~/.local/bin/tensorfold` |
| `TENSORFOLD_API_KEY_FILE` | 0600 key file; the native engine is keyed |
| `SPARK_IFACE`, `SPARK_IB_HCA` | this node's fabric netdev(s) and RoCE device(s); several rails comma-separated |
| `SPARK_HEAD_IP` | rank 0's fabric address. **The same on every node** (`check` enforces it) |
| `SPARK_NODE_IP` | this node's fabric address |
| `SPARK_SERVE_HOST`, `SPARK_SERVE_PORT` | rank 0's HTTP bind. Keep `127.0.0.1` when the command center is on the head; `SPARK_ALLOW_WILDCARD_BIND=false` refuses the wildcard in any spelling (`0.0.0.0`, `::`, `0`, `::0`, `[::0]` …). A server with no API key (the container engine, or `SPARK_NO_API_KEY=true`) binds loopback only unless `SPARK_ALLOW_KEYLESS_BIND=true` |
| `MODELS_DIR`, `HF_CACHE`, `HF_TOKEN_FILE` | where verified weights live |
| `VLLM_IMAGE`, `VLLM_IMAGE_DIGEST`, `VLLM_IMAGE_ID`, `TENSORFOLD_IMAGE*` | container engines. `*_IMAGE_ID` wins over a digest, which is what makes a `docker load`ed image work offline ([H4](#h4-container-images)) |
| `VLLM_MASTER_PORT`, `TENSORFOLD_MASTER_PORT` | rendezvous ports; spark-guard guards them (each +1) too. It reads them from this file only, never from the environment or another `--env` file, so move a port here and re-run `sudo spark-guard.sh`. With the guard installed, `spark-node.sh` refuses a master port its rules do not cover |

**One rank by hand** (start ranks N-1..1 FIRST, then rank 0):

```bash
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 1 --master 10.100.216.1 --print   # print the exact command, run nothing
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 1 --master 10.100.216.1           # on spark-2
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0 --master 10.100.216.1           # then on spark-1
scripts/backends/tensorfold/spark-node.sh --profile NAME --rank 0 --stop                          # stop this rank (idempotent)
```

**The whole cluster from the head.** List the nodes in rank order in
`scripts/backends/cluster.env` (copy `cluster.env.example`), for example
`CLUSTER_NODES="local max@10.100.216.2"`, `CLUSTER_NAME=spark-hydra`,
`CLUSTER_REPO=~/openbeast`. A leading `~/` is sent as-is and expands on each
node. Then:

```bash
scripts/backends/spark-cluster.sh check                          # every node: aarch64 + GB10, docker, NCCL, same commit, same engine build + spark.env pins, fabric pairing, SPARK_HEAD_IP reach
scripts/backends/spark-cluster.sh sync-weights --profile NAME    # model + drafter over the fabric, verified vs the HEAD's lock
scripts/backends/spark-cluster.sh sync-image IMAGE               # docker save | load where it differs; prints the *_IMAGE_ID pin
scripts/backends/spark-cluster.sh save-image IMAGE FILE          # an image to a file, for a cluster with no registry
scripts/backends/spark-cluster.sh start --profile NAME [--engine tensorfold|vllm]
scripts/backends/spark-cluster.sh status                         # per rank: free memory, openbeast-* and spark-hydra-* user units, containers
scripts/backends/spark-cluster.sh stop [--engine tensorfold|vllm]   # every rank, rank 0 first; both engines by default
#   --cluster FILE   --dry-run (print every command, run nothing)
```

How start and stop behave:

- `start` launches ranks N-1..1 and then 0, each with its own
  `spark-node.sh --rank R`. If a later rank fails, it stops the ranks it
  started.
- `stop` stops rank 0 first, and walks every rank even if one is already down.

**Do not run D2 on the ports D1 is using** while the pair serves: both use
:8000 and the 29551 range.

**Which engine scales how far** (2026-11-15):

| Engine | Ranks | |
|---|---|---|
| Mia-AiLab GLM recipe (TensorFold v0.6.0 + patches) | 2 (`start.sh`), 3 (`start-tp3.sh`, experimental) | what serves our pair; its own scripts drive the ranks |
| native TensorFold ≥ 1.0 (1.2.7) | GLM-5.3-Flash up to 16 (`TF_GLM_WORLD`); MiniMax-M3.1-Flash 2 only (`TF_MINIMAX_*`); Flash Next 1-2; Nemotron 1; other families `--tp` 2/4/8 | from the 1.2.7 binary's own messages and `capabilities --json`; `spark-node.sh` refuses sizes the engine refuses |
| container TensorFold 0.x (@9cd52ab) | per `data/tensorfold.json`: GLM 2, most families 1-2, qwen3_5_moe 1 | the launcher refuses an excluded size at that commit, and warns at any other |
| vLLM (native multi-node) | `--nnodes N`, TP = N | the model's head count must divide by N |

Whether a model *fits* at N is a memory question. `model-inspect.sh` reports the
smallest N that holds the weights with KV room, judges ranks against the native
TensorFold engine, and lists the container engine's ranks separately.
Onboarding a new model: `DGX_SPARK_PLAN.md` §14 (`model-inspect.sh <repo@sha>
--write-profile NAME`, then `model-fetch.sh --profile NAME`, then
`conformance.sh`).

**Several smaller groups instead of one big one.** For example, 4 Sparks can run
as two independent pairs, each its own hydra node (`[nodes.pairA]`,
`[nodes.pairB]`, both in one route). That trades the long single-request context
of TP 4 for twice the concurrent capacity.

### D3. The engine plane: `scripts/engine.sh`

`engine.sh` is one command to list, swap, install, update, pin and roll back the
engine ([ENGINES.md](ENGINES.md) has the full matrix):

```bash
scripts/engine.sh list                         # what this host can run; installed / pinned / active
scripts/engine.sh status                       # active engine: version, pin, endpoint readiness, model served
scripts/engine.sh update tensorfold            # latest release: download, verify, pin
scripts/engine.sh install tensorfold --version 1.2.9
scripts/engine.sh rollback tensorfold          # previous pin, offline when still on disk
scripts/engine.sh stage tensorfold --version 1.2.9 --platform linux-aarch64 ./carry   # connected box
scripts/engine.sh install tensorfold --from ./carry                                   # closed box
scripts/engine.sh history
scripts/engine.sh use <engine|default> --profile NAME --dry-run   # print the conf change + launch command
```

On a Spark:

- `install`, `update` and `rollback` also rewrite `scripts/backends/spark.env`
  (`TENSORFOLD_VERSION`, `VLLM_IMAGE` / `VLLM_IMAGE_DIGEST` / `VLLM_IMAGE_ID`), so `spark-node.sh`
  launches the pinned build. **Every rank must run the same build**: update
  each Spark, then restart all ranks.
- `engine.sh use` manages only a **single-host, TP 1** engine
  (`INFERENCE_MANAGED=true`). A multi-Spark job stays unmanaged: `start.sh`
  waits for it and never launches it, and you run it with D1 or D2. Record it
  in the conf with `--unmanaged`, which the head needs because rank 0 answers
  on its own loopback, and a loopback URL alone reads as managed:
  `scripts/engine.sh use tensorfold --unmanaged --url http://127.0.0.1:8000 --profile NAME`
  writes `INFERENCE_BACKEND`, `INFERENCE_URL`, `INFERENCE_MANAGED=false` and,
  from the profile, `INFERENCE_MODEL` / `INFERENCE_SLOTS` (offline: it reads
  only local files).
- `engine.sh` does not manage the recipe (D1). The recipe pins its own image tag.

`engine.sh` is on both live checkouts ([0](#what-runs-where)), but
no swap has run there yet; it is tested hermetically (`tests/test_engines.sh`):
VERIFY ON HARDWARE.

---

## E. The command center on spark-1

### E1. Bootstrap

```bash
cd ~/openbeast
cp openbeast.conf.example openbeast.conf 2>/dev/null || touch openbeast.conf
chmod 600 openbeast.conf
# write the keys in E2 first: INFERENCE_BACKEND=tensorfold makes bootstrap skip llama.cpp and the GGUF
./bootstrap.sh --preflight       # what it will do, and what it cannot reach
./bootstrap.sh --no-start        # install deps, compose images, frontends; do not start yet
```

`INFERENCE_MANAGED` is forced false for TensorFold, so `./bootstrap.sh` builds
no llama.cpp and downloads no GGUF (DGX_SPARK_PLAN §8 row 19). All three compose
images are multi-arch at their pinned index digests, so the same
`docker-compose.yml` pulls arm64 on a Spark. The Python lock installs as-is on
aarch64 / python 3.12.3 (43 packages, measured on spark-1).

### E2. `openbeast.conf`

These are the keys in use on spark-1 on 2026-11-15. Secret values are not shown.

| Key | Value on spark-1 | Why |
|---|---|---|
| `INFERENCE_BACKEND` | `tensorfold` | the model is the recipe's TensorFold server |
| `INFERENCE_URL` | `http://127.0.0.1:8000` | same box, loopback. Spawned agents follow it (conf.sh exports `OPENBEAST_AGENT_INFERENCE_URL=$INFERENCE_URL/v1`) |
| `INFERENCE_MODEL` | `GLM-5.3-Flash-EXL3` | the served name; exported as `OPENBEAST_INFERENCE_MODEL`, which `agents/runner.py` sends |
| `INFERENCE_SLOTS` | `4` | matches the recipe's 4 streams; beast-slot reports it. With `EDGE_MAX_INFLIGHT` unset it is also beast-gate's per-device cap, so one device (the Mac running parallel agents) may hold all 4 streams. Before the 2026-11-16 review the gate admitted 2 per device whatever the engine served; the audit showed `max_inflight` 429s with streams idle, each costing an agent turn ~7 s. Live since the 2026-11-16 restart: `/api/slot` reports `admission.per_device_inflight: 4` |
| `EDGE_GATE` | `true` | beast-gate (:8090) is the one inference door (:8443) |
| `EDGE_READ_TIMEOUT` | `3600` | seconds the gate waits for each byte from the model (default 600). A non-streaming completion sends nothing until it is done, so a long eval turn hit the gate's 504 at 600 s. Needed on any pair an eval harness targets ([F6](#f6-the-eval-harness-host-benchmarking-the-pair)) |
| `TS_HOSTNAME` | `spark-1` | the tailnet name `setup-tailscale.sh` joins as. A Spark has no default and the script refuses without it ([E6](#e6-publish-on-the-tailnet-setup-tailscalesh-as-tailscaleds-operator)) |
| `WEBUI_AUTH` | `true` | the WebUI login wall; required before :443 is published |
| `WEBUI_ADMIN_EMAIL`, `WEBUI_ADMIN_PASSWORD` | *(secret)* | the admin `configure-webui.sh` uses to apply tool-server config on restarts |
| `SEARXNG_SECRET` | *(secret)* | SearXNG's secret key |
| `EXTENSIONS` | `"dashboard"` | the dashboard serves beast-slot (`/api/slot`) |
| `BEAST_CHAT` | `true` | the operator console |
| `CHAT_OPERATORS` | `maxjkh@gmail.com` | tailnet logins that may read beast-chat |
| `CHAT_LOGIN_FROM` | `unix` | beast-chat honours the login header only on `.run/chat.sock`; `:8445` proxies to that socket (`setup-tailscale.sh --publish-chat`), so the host-network containers below cannot claim a login ([BEAST_CHAT.md](BEAST_CHAT.md)) |
| `BEAST_ARTIFACT` | `true` | the artifact gallery and `publish_artifact` |
| `ARTIFACT_OPERATORS`, `ARTIFACT_ADMINS` | `maxjkh@gmail.com` | who reads and administers artifacts |
| `LANG_PACKS`, `LANG_PACK_CONTEXT` | `off` | language assistance OFF on the pair (below) |
| `BEAST_ASSIST`, `BEAST_ESCALATE` | `0` | ditto |
| `INFERENCE_THINKING` | `on` | thinking on by default for every client that does not choose: beast-gate, the WebUI filter, this host's agents. The best v5 arm (C, 51/71 vs 36/71 off). Since 2026-11-17 ([E2a](#e2a-recommended-serving-configuration-from-v5)) |
| `AGENT_MAX_TOKENS` | `16384` | per-turn cap for this host's own agents: arm C's cap. `REASONING_BUDGET` is a llama.cpp flag ([E2a](#e2a-recommended-serving-configuration-from-v5)) |

Not set, so the defaults apply:

- `ISLAND` = `auto`.
- `OFFLINE` = `false`.
- `MEM_RESERVE_GB` = `3`. This is the GiB the stack's cap leaves beside the
  model on unified memory ([G4](#g4-memory-headroom-and-the-gb10-stack-cap)).
- `MEM_LIMIT_PCT` = `75`.
- `EDGE_MAX_INFLIGHT` unset, so it follows `INFERENCE_SLOTS`: beast-gate
  admits 4 concurrent generations per device key. The eval harness's agents
  all share one key; `run_eval` reads the cap from the gate (a `n=1024` chat
  request the gate refuses before forwarding anything) and clamps `--jobs`
  to it, so a `--jobs 4` profile from the Mac runs 4 workers. Before review
  1116 the default was 2: half the generations got 429, each burned an agent
  iteration, and failing units became unbanked `server_error`s. Set
  `EDGE_MAX_INFLIGHT` only to hold one device **below** the server's slots
  when other clients share the pair.
- `ARTIFACT_LOGIN_FROM` = `loopback`. **Pending:** set it to `unix`, restart,
  and re-run `setup-tailscale.sh --publish-artifact` ([G6](#g6-updating)
  step 6), as `CHAT_LOGIN_FROM` already is.
- `WEBUI_RANDOM_SEED` = `true`: `configure-webui.sh` installs the random seed
  filter ([G6](#g6-updating)).
- `WATCHDOG_AUTOINSTALL` and `LOGROTATE_AUTOINSTALL` = `true`.
- `BIND_HOST` = `127.0.0.1`.

**Language assistance is OFF on the pair** (Max, 2026-11-15). Packs, the pack
context, beast-assist and escalation were measured for the 27B and help it.
GLM-5.3-Flash needs far less help. `language_reference` stays available as a
tool the model can choose to call. Also not enabled on the pair: `AGENT_ROUTER`,
an intent classifier that exists because the 27B would not call `start_agent`
on its own, and `HYDRA` / `INSTINCT`, whose scorer is the rig's 27B.

### E2a. Recommended serving configuration (from v5)

**Thinking on, by default, for every client.** This is the configuration the
2026-11-16/17 v5 arms found best for GLM-5.3-Flash on this pair, and since
2026-11-17 it is how spark-1 serves:

| Arm (v5, 71 units, one seed) | What changed | Scored |
|---|---|---|
| L0 | baseline, thinking off | 26/71 |
| A | runner v2 | 31/71 |
| B | + wall-scaled turns | 36/71 |
| **C** | **+ thinking on** | **51/71 (54 raw)** |
| E | C + diagnostics, edit hints, verify-done, packs, env block, wall warnings | 50/71 |

C is the best arm. E's additions were a wash and stay opt-in. Runner v2,
thinking-stall recovery (#194), the eval turn caps and the integrity seal
were already on main. The serving default adds one thing: thinking.
Evidence: [RESULTS.md](RESULTS.md), [HARNESS_NOTES.md](HARNESS_NOTES.md),
[MODEL_CAMPAIGN.md](MODEL_CAMPAIGN.md).

**Why a switch is needed at all.** Thinking is a per-request chat-template
switch, `chat_template_kwargs.enable_thinking`. The TensorFold server thinks
only when a request asks. A request that does not say gets thinking OFF. We
do not reconfigure the inference unit. The default lives in the OpenBeast
layer in front of it, keyed by one conf key.

**The knobs** (`openbeast.conf` on spark-1, both appended 2026-11-17):

| Key | spark-1 | Default elsewhere | What it does |
|---|---|---|---|
| `INFERENCE_THINKING` | `on` | unset: nothing injected anywhere | The host's thinking default for a chat request that names none. **beast-gate** (:8443, which serves opencode, the Mac client, phones and every keyed device) sets `chat_template_kwargs.enable_thinking`. The **WebUI "Thinking Default" filter** does the same for Open WebUI chats (WebUI talks to TensorFold on loopback, not through the gate). **This host's own agents** (`agents/runner.py`, `start_agent`) send it. The local `/gate/health` reports `thinking_default` |
| `AGENT_MAX_TOKENS` | `16384` | unset: `REASONING_BUDGET` + 12288 (32768) | Per-turn output cap for this host's own agents. It matches arm C's measured cap: ended-on-their-own thinking turns were p50 ~0.4K, p90 ~2.3K, p99 ~12.4K tokens, so 16K leaves room. A turn capped mid-thought keeps its reasoning tail and is told to act (#194). The 32K fallback would double the cost of a runaway: about 14 vs 28 minutes per stalled turn at the ~19 tok/s a stream decodes under 4-way load. `REASONING_BUDGET` is a llama.cpp flag and means nothing to TensorFold, so this key is the cap here |

**What a request can still choose.** Anything explicit wins and is
forwarded untouched:

- `chat_template_kwargs.enable_thinking` (true or false), or `.thinking`;
- a top-level `enable_thinking`, `thinking`, `reasoning`,
  `reasoning_effort`, `reasoning_budget_tokens` or `thinking_budget`.

Only `/v1/chat/completions` is touched, never `/v1/completions` or
`/v1/embeddings`.

**Switching thinking off, for latency:**

- **Per request:** send `"chat_template_kwargs": {"enable_thinking": false}`.
  In opencode, set it in the model's `options` in `opencode.json`. In Open
  WebUI, use the chat's Controls → Custom Parameters, key
  `chat_template_kwargs`, value `{"enable_thinking": false}`. For a
  thinking-off model entry for everyone, put the same key in Admin → Models
  → GLM → Advanced Params; the filter steps aside for it.
- **Per agent:** `OPENBEAST_AGENT_THINKING=off`.
- **For the whole host:** `INFERENCE_THINKING=off` (or delete the line),
  then `systemctl --user restart openbeast`. That restarts the gate and
  re-runs `configure-webui.sh`; it never touches `spark-hydra-inference`.

**Eval runs are unaffected.** Every eval-mode request the runner sends
carries `X-OpenBeast-Gate-Defaults: off`. The gate forwards such a request
exactly as sent and strips the header. The runner also ignores
`INFERENCE_THINKING` in eval mode, so an eval run that names no thinking
mode still gets each server's own default. That is the condition every
baseline was measured under: off on TensorFold, on for llama.cpp's Qwen
templates. Thinking arms set `OPENBEAST_AGENT_THINKING=on` as before. This
moved the eval era only through the `runner.py` hash
([evals/CHANGELOG.md](../evals/CHANGELOG.md)).

**What it costs.** Measured on the pair, 2026-11-17: single stream through
:8443 from the Mac, streaming, three everyday prompts, each run with
thinking off and then on.

| | First token | First answer token | Total | Completion tokens |
|---|---|---|---|---|
| thinking off (median) | 0.21 s | 0.2 s | 1.9 s | 126 |
| thinking on (median) | 0.22 s | 5.0 s | 6.1 s | 395 |
| thinking on, range | 0.20-0.23 s | 1.9-38.7 s | 2.4-41.5 s | 171-3,026 |

The first streamed token arrives just as fast either way, because the
reasoning streams first. WebUI shows it live in a collapsed "Thought for N
seconds" block. What grows is the wait for the answer. A trivial question
thinks for about 2 s. A coding question ("longest palindromic substring")
thought for about 9K characters (3,026 tokens) and answered at 39 s instead
of 3 s. Decode stays about 75 tok/s single-stream.

On agentic work the cost is larger. Arm C averaged 29.0 min per unit
against arm B's 11.7, about 2.3x B's wall time per unit. 14 of C's first 18
passes still finished inside B's original wall. For interactive chat where
latency matters more than the last points of quality, turn thinking off
per request or per client as above.

**Deploy and check** (what was run on 2026-11-17):

```bash
# spark-1
cd ~/openbeast && git pull --ff-only
printf '%s\n' INFERENCE_THINKING=on AGENT_MAX_TOKENS=16384 >> openbeast.conf
systemctl --user restart openbeast     # never spark-hydra-inference
./scripts/configure-webui.sh           # (also run by the restart) installs the Thinking Default filter
curl -s -H "X-OpenBeast-Local: $(cat .run/edge-local.token)" http://127.0.0.1:8090/gate/health
#   ... "thinking_default":"on"
```

From a keyed client: a chat request with no thinking params returns
`reasoning_content`, and one with `enable_thinking: false` returns none.

### E3. Boot units

```bash
mkdir -p ~/.config/systemd/user
sed "s|@REPO@|$HOME/openbeast|" scripts/openbeast.service > ~/.config/systemd/user/openbeast.service
systemctl --user daemon-reload
systemctl --user enable --now openbeast
```

- `openbeast.service` is a oneshot with `RemainAfterExit`. It runs
  `start.sh -d`, which runs the stack inside its own memory-capped transient
  scope (`openbeast-stack`).
- The unit does not order against `spark-hydra-inference`. `start.sh` waits up
  to `OPENBEAST_LLAMA_LOAD_GRACE` for `INFERENCE_URL` and brings the rest of the
  stack up either way.
- `start.sh -d` installs and enables `openbeast-watchdog.timer`
  (`healthcheck.sh --restart` every 5 min) and `openbeast-logrotate.timer`
  (daily) when they are missing.

What is enabled on spark-1 (`systemctl --user list-unit-files | grep -E 'openbeast|spark|hydra'`):

```
openbeast.service              enabled
spark-hydra-inference.service  enabled
openbeast-watchdog.timer       enabled
openbeast-logrotate.timer      enabled
openbeast-stack.service        transient
```

Then reboot once. This is the docker-group gotcha from
[B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark). After the
reboot, everything comes back by itself:

```bash
systemctl --user status spark-hydra-inference openbeast --no-pager | grep -E 'Active|●'
~/openbeast/scripts/healthcheck.sh        # expect "All 9 services healthy" once the model has loaded
```

### E4. The healthy state

```
Stack health check — 2026-11-15 13:03:38
  OK   TensorFold (tensorfold @ http://127.0.0.1:8000)
  OK   Tool server
  OK   Open WebUI
  OK   SearXNG
  OK   beast-gate
  OK   beast-chat console
  OK   beast-artifact
  OK   Dashboard (beast-slot)
  --   Network: online (watching: islands after 90s without a route); internet reachable at last probe
  OK   Tailscale (remote access)

  Unified memory: 9751 MiB available of 124607 MiB (the model's share is in the total)

All 9 services healthy.
```

### E5. WebUI auth and the admin account, before publishing

Do this **before** `setup-tailscale.sh` publishes :443. Publishing an auth-off
WebUI makes every tailnet device an admin, with `bash`.

1. Put `WEBUI_AUTH=true` in `openbeast.conf` and (re)start the stack:
   `systemctl --user restart openbeast`.
2. On the first auth-on start, Open WebUI's built-in `admin@localhost` default
   password (`admin`) is rotated to a random one, saved as
   `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD` in `openbeast.conf` (0600).
   `setup-tailscale.sh` also checks this.
3. Reach the WebUI over an ssh tunnel (it binds loopback):
   `ssh -L 3000:127.0.0.1:3000 spark-1.tail4109f9.ts.net`, then open
   `http://localhost:3000`. Log in with the rotated credentials and create your
   own admin in Admin Panel → Users. New signups land as `pending`; "first
   signup becomes admin" applies only to a WebUI database that has never had a
   user.
4. If you make your own account the one `configure-webui.sh` uses, put it in
   `WEBUI_ADMIN_EMAIL` / `WEBUI_ADMIN_PASSWORD`. The rotated built-in password
   then moves to `WEBUI_DEFAULT_ADMIN_PASSWORD`.

On spark-1:

- The admin is `maxjkh@gmail.com`. Its initial password was left in
  `~/.config/openbeast/webui-max-password` (0600). Change it in Settings →
  Account, then delete that file (`shred -u`).
- The WebUI database is spark-1's own: accounts and chats from the beast rig do
  not carry over.

### E6. Publish on the tailnet (`setup-tailscale.sh`, as tailscaled's operator)

`spark-root-setup.sh` set `--operator=max`, so this needs no sudo on a Spark:

```bash
cd ~/openbeast
grep -q '^TS_HOSTNAME=' openbeast.conf || echo 'TS_HOSTNAME=spark-1' >> openbeast.conf
./scripts/setup-tailscale.sh --publish-slot --publish-chat --publish-artifact --publish-searxng
./scripts/setup-tailscale.sh --status        # read-only: which surface sits on which port
```

`TS_HOSTNAME` (conf key, env overrides) names the box on the tailnet. On a Spark it
has no default: the script refuses to join until it is set, because two Sparks
both defaulting to `beast` would collide. It applies only to the first join; a
Spark already on the tailnet keeps its name, and the script prints a note (never
a rename) when the conf names it differently. Re-running it on a joined Spark is
therefore safe. The script refuses to
publish :443 unless the **running** WebUI enforces auth. It refuses :8443 unless
beast-gate (`EDGE_GATE=true`) or a shared `LLAMA_API_KEY` is in front. The
overrides `--i-accept-open-webui` and `--i-accept-open-inference` exist; do not
use them here. Undo one surface with `--unpublish-<name>`.

The published URLs (live 2026-11-15):

| URL | What | Auth |
|---|---|---|
| `https://spark-1.tail4109f9.ts.net` | Open WebUI | WebUI login (`WEBUI_AUTH=true`) |
| `https://spark-1.tail4109f9.ts.net:8443/v1` | inference, **through beast-gate** | per-device key (`scripts/clients.sh`); 401 without one (checked from the Mac 2026-11-15) |
| `https://spark-1.tail4109f9.ts.net:8444/api/slot` | beast-slot discovery | open, read-only |
| `https://spark-1.tail4109f9.ts.net:8445` | beast-chat console | tailnet login to read, chat-scoped device key to act |
| `https://spark-1.tail4109f9.ts.net:8446` | beast-artifact gallery | tailnet login (`ARTIFACT_OPERATORS`) |
| `https://spark-1.tail4109f9.ts.net:8889` | SearXNG for thin clients | open to the tailnet |

The model itself still listens on `127.0.0.1:8000` only. `:8443` is the one
inference door, and it always goes through the gate. Public exposure (funnel)
is never offered.

**Not yet:** a stable `svc:spark-hydra` name. That is a Tailscale Service, which
has to be defined in the admin console (and spark-1 tagged) before
`tailscale serve --service=svc:spark-hydra` can advertise it. Until then the
pair is reached by spark-1's name.

### E7. Device keys for beast-gate

```bash
cd ~/openbeast
./scripts/clients.sh enroll macbook --label "Max's MacBook"     # prints the key ONCE; the registry keeps only its SHA-256
./scripts/clients.sh enroll phone --label "phone" --scope chat  # chat scope = may act in beast-chat
./scripts/clients.sh list
./scripts/clients.sh revoke <id>         # a lost device; hot-reloaded, no restart
./scripts/clients.sh rotate <id>         # new key for the same device
```

The registry is `.run/clients.json` (0600, gitignored).

Carry each key to its device **without printing it into a shared log**: copy it
straight into a 0600 file on the device. On the Mac it is
`~/.config/openbeast/spark-hydra-gate.key`.

**Known issue:** plaintext copies of the `macbook` and `phone` keys are in
`~/.config/openbeast/gate-keys/` on spark-1 (0600). They defeat the registry's
hash-only design. They are readable by `max`, the uid the admin-tier `bash` /
`read_file` tools run as, so a prompt-injected admin chat could read a working
device key. Once each device holds its key, delete the copies:
`shred -u ~/.config/openbeast/gate-keys/*.key`. If one is lost, re-enroll to
recover it.

---

## F. The Mac client

**From the Mac: quick reference.** Every command here runs on the Mac.

```bash
openbeast-client status                      # green = env, venv, tailnet, the active rig's :8443 + key, /api/slot, search, opencode
openbeast-client rigs                        # all rigs, * = active, state from each rig's :8444/api/slot
openbeast-client use spark-hydra             # point opencode, the tools and web_search at the pair
openbeast-client use beast                   # ... or at the 5090 rig (refused, nothing changed, while it is down)
openbeast-client rig add beast --api-key-stdin < ~/.config/openbeast/beast-gate.key   # beast's device key, once
cd <any project> && opencode                 # model picker: openbeast-rig/… (active) or openbeast-<rig>/…
opencode run -m openbeast-spark-hydra/GLM-5.3-Flash-EXL3 "…"   # one-shot, a chosen rig
openbeast-client agent "task"                # the OpenBeast CLI agent, tools here, thinking on the active rig
scripts/client-e2e.sh                        # the end-to-end check (F2), PASS/FAIL per line
openbeast-client refresh-config              # also re-copies the soul into opencode's instructions (F3)
brew upgrade opencode && opencode service restart   # opencode >= 2.2.0 loads `instructions`; 2.0.25 ignores them
curl -s https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool   # raw discovery, no key
```

### F1. ssh from the Mac

NVIDIA Sync's `.local` names resolve only on the home LAN. For away from home,
the Mac's `~/.ssh/config` has tailnet entries that reuse the Sync key and pin
the known host keys with `HostKeyAlias`:

```
Host spark-1.tail4109f9.ts.net
  HostKeyAlias spark-3dc6.local
  User max
  IdentityFile <the NVIDIA Sync key>
Host spark-2.tail4109f9.ts.net
  HostKeyAlias spark-9f26.local
  User max
  IdentityFile <the NVIDIA Sync key>
```

Without `HostKeyAlias`, the first tailnet login asks to trust a "new" host
whose key is in fact already known under the `.local` name.

**Quote remote commands.** `ssh spark-1 cat ~/.config/openbeast/x` expands `~`
on the **Mac** (`/Users/…`) before ssh sends it. Write
`ssh spark-1 'cat ~/.config/openbeast/x'`. The same goes for `$HOME`, globs and
`&&` chains.

### F2. Client mode: one or several rigs

The Mac talks to **two rigs**: `spark-hydra` (this pair, through spark-1's
gate) and `beast` (the RTX 5090 llama.cpp rig). Client mode installs once;
after that, rigs are added, listed and switched with `openbeast-client`.

**Install** (once, from a checkout on the Mac):

```bash
cd ~/Documents/projects/openbeast          # a checkout on the Mac
./scripts/setup-client.sh --host spark-1.tail4109f9.ts.net --name spark-hydra \
  --api-key-stdin < ~/.config/openbeast/spark-hydra-gate.key
openbeast-client status                     # green; "active rig: spark-hydra"
```

`--name` is optional: without it the rig is named after the key file that
holds the same key (`spark-hydra-gate.key` → `spark-hydra`), else after the
host's first label. The installer also registers your own tailnet peer named
`beast` (same tailnet, same owner; never another user's), keyless.

**Add a rig's key, list, switch:**

```bash
openbeast-client rig add beast --api-key-stdin < ~/.config/openbeast/beast-gate.key   # key → 0600 file
openbeast-client rig add beast --key-file ~/.config/openbeast/beast-gate.key          # or reference a file in place
openbeast-client rig add lab --host lab.tail4109f9.ts.net --api-key-stdin < lab.key   # a new rig
openbeast-client rigs                       # every rig: state from its :8444/api/slot, model, slots, key
openbeast-client use beast                  # switch: env + opencode.json, atomically; catalog refreshed
openbeast-client use spark-hydra            # and back
openbeast-client use beast --force          # switch even though beast is down / rejects the key
openbeast-client rig remove lab             # the key file is kept
openbeast-client rig detect                 # register the tailnet's 'beast' if it appeared later
openbeast-client refresh-config             # re-probe every rig, rewrite the catalog
```

`openbeast-client rigs` on the Mac, 2026-11-17 (beast offline, no key yet):

```
    RIG            HOST                           STATE        MODEL                    SLOTS  KEY
    beast          beast.tail4109f9.ts.net        offline      ?                        -      NONE
  * spark-hydra    spark-1.tail4109f9.ts.net      healthy      GLM-5.3-Flash-EXL3       0/4    ~/.config/openbeast/spark-hydra-gate.key
```

**What a switch does.** `use <rig>` probes every rig in parallel (`/api/slot`,
public; `/v1/models` with that rig's key). If the target answers nothing, is
down, or rejects the key, it prints why, says `active rig unchanged`, changes
**nothing**, and exits 1. `--force` switches anyway. Otherwise it rewrites,
each by an atomic rename at 0600:

- `~/.openbeast-client.env`: `OPENBEAST_RIG`, the inference, slot and search
  URLs, the key. Lines it does not own (`OPENBEAST_SEARXNG_SECRET`, your own)
  are kept.
- `~/.config/opencode/opencode.json`: the providers, the `openbeast-tools`
  environment, the default model and the soul's `instructions` entries (F3).
  The previous file is kept as `opencode.json.openbeast-prev`.
- `~/.config/openbeast/rigs.json`: the registry, `active` set.

**The registry**, `~/.config/openbeast/rigs.json` (0600, `scripts/lib/client_rigs.py`):
per rig the host, the URLs, the **path** of its 0600 key file (never the
key), the default model (follows what the rig serves, unless pinned with
`rig add <rig> --model <id>`), an optional `output_limit`, and the last
`/api/slot` answer, so an offline rig keeps its catalog. Keys never go on
argv and are never printed. A single-rig install made before this (an env
file, no registry) is migrated on the first `openbeast-client` command.

**Which rig does what.** The **active** rig gets the tool arsenal's own
traffic: `web_search` (its `:8889`) and sub-agents (`start_agent`). In
opencode, any registered rig's model can be picked per session (F3). Tools
always run on the Mac.

What you get, whichever rig is active:

- OpenCode on the Mac, with the full tool arsenal running **locally**: a stdio
  MCP subprocess (`agents/mcp_server.py`) in `~/.openbeast-client/venv`.
  `bash`, `edit_file` and the rest act on the Mac's files.
- Inference from the rig's `:8443/v1` (on the pair: through the gate).
- `web_search` through the rig's `:8889`. The alternatives are
  `--local-search` or `--no-search` (setup), or `rig add <rig> --search local|none`.

File contents the agent reads are sent to the rig as model context. The
promise is "nothing leaves the tailnet", not "nothing leaves the Mac".

**Testing from the Mac.** `scripts/client-e2e.sh` checks the whole path
against the real active rig with a few short requests, one PASS/FAIL line
each, exit status = failures:

```bash
scripts/client-e2e.sh --expect-model GLM-5.3-Flash-EXL3 --expect-slots 4
scripts/client-e2e.sh --skip d               # without the switching test
scripts/client-e2e.sh --keep                 # keep the temp project + opencode logs
```

| | checks |
|---|---|
| (a) | `openbeast-client status` green, names the active rig, the model and the slot count |
| (f) | opencode accepts the config: `opencode models` lists `openbeast-rig/<model>` and `openbeast-<rig>/<model>`; opencode's own parse of the row has `limit.context` = `/api/slot` `model.ctx` |
| (b) | `opencode run` in a fresh temp dir: the agent writes a python file with `openbeast-tools_write_file` and runs it with `openbeast-tools_bash`; the file must exist **on the Mac** and print the expected line (tools local, inference on the rig) |
| (c) | `web_search` through the rig's SearXNG (the MCP tool, called directly) |
| (d) | `use <other rig>`: refused with nothing changed when it is offline, `--force` switches and `status` fails gracefully, `use <rig>` comes back green (a real round trip when the other rig is up). opencode.json is backed up first (`opencode.json.bak-e2e-<time>`, 0600) and a trap restores the rig |
| (e) | the `openbeast-tools` MCP server lists its tools |
| (g) | the soul: opencode's own parse of the config lists `~/.openbeast-client/prompts/system-prompt.md` (and the tool guide) in `instructions`, the installed opencode loads that setting (not 2.0.x), and a fresh `opencode run` asked "who do you serve?" answers Max, which only the soul tells it |

**Measured on the Mac, 2026-11-17** (against spark-1, beast offline, thinking
on at the gate): 6 passed, 0 failed. (a) green, `GLM-5.3-Flash-EXL3`, 0/4
busy, 1024K shared. (f) opencode's parsed limit on both providers:
`{"context": 1048576, "output": 32768}`. (b) 53 s: the agent called
`openbeast-tools_write_file` and `openbeast-tools_bash`, the file ran on the
Mac and printed the expected line, with 2 reasoning parts and 2,185 reasoning
tokens. (c) 3 results through `:8889` in 0.8 s. (d) `use beast` refused
("nothing answers"), files unchanged; `--force` switched, `status` exit 1;
`use spark-hydra` green again. (e) 19 tools.

### F3. What the client writes into opencode

The Mac runs **opencode v2.0.25**. Its global config is
`~/.config/opencode/opencode.json`. Client mode writes only its own entries,
and leaves everything else in the file alone:

```jsonc
{
  "mcp": { "servers": {
    "openbeast-tools": { "type": "local", "codemode": false,
      "command": ["…/.openbeast-client/venv/bin/python3", "…/agents/mcp_server.py"],
      "environment": { "OPENBEAST_AGENT_INFERENCE_URL": "<active rig>/v1", "SEARXNG_URL": "<active rig>:8889", "OPENBEAST_API_KEY": "…" } } } },
  "provider": {
    "openbeast-rig":         { "name": "OpenBeast rig (active: spark-hydra, …)", "options": { "baseURL": "…", "apiKey": "…" }, "models": { … } },
    "openbeast-spark-hydra": { "name": "OpenBeast spark-hydra (spark-1.tail4109f9.ts.net)", "options": { … },
                               "models": { "GLM-5.3-Flash-EXL3": { "name": "GLM-5.3-Flash-EXL3  [live on spark-hydra]",
                                                                    "limit": { "context": 1048576, "output": 32768 } } } },
    "openbeast-beast":       { … }                       // once beast has a key
  },
  "model": "openbeast-rig/GLM-5.3-Flash-EXL3",
  "small_model": "openbeast-rig/GLM-5.3-Flash-EXL3",
  "instructions": [
    "/Users/<you>/.openbeast-client/prompts/system-prompt.md",        // the soul
    "/Users/<you>/.openbeast-client/prompts/system-prompt-tools.md",  // the tool guide
    "/Users/<you>/.openbeast-client/prompts/opencode-client.md",      // client corrections
    "…your own entries, untouched"
  ]
}
```

- **The soul.** Every opencode session loads the same system prompt Open WebUI
  runs with on the rigs: `system-prompt.md` (the soul) and
  `system-prompt-tools.md` (the tool conventions), the two files
  `scripts/configure-webui.sh` concatenates into the WebUI model's prompt.
  They are **copied** to `~/.openbeast-client/prompts/` (a moved or deleted
  checkout cannot break them, and opencode skips a missing instruction file
  without a word) and listed **first** in `instructions`; every entry that
  does not point there is yours and is never touched or reordered. Every sync
  re-copies them (setup, `use`, `rig add/remove`, `refresh-config`, `update`),
  rewriting a file only when its content changed: opencode watches
  instruction files and announces each change to running sessions.
  `--uninstall` removes our entries and the copies. The source files are
  owner-maintained and never edited by the client.
- **Why the tool guide is included, plus a third file.** Most of
  `system-prompt-tools.md` holds on the client: the same `agents/mcp_server.py`
  serves `read_file`, `edit_file`, `bash`, `web_search`, `fetch`,
  `start_agent`, `skill` and the rest here. Two things do not, and
  `opencode-client.md` (written by `scripts/lib/client_rigs.py`, a few lines)
  says so: in opencode the tools are named `openbeast-tools_<tool>`, and the
  "Where files go" paragraph (a private chat workspace with a
  `.manifest.jsonl` index) is Open WebUI's: here relative paths resolve
  against the tool server's working directory, so the note asks for absolute
  paths.
- **opencode version.** `instructions` is in the v2 schema, but **2.0.25
  does not load it**: its instruction plugin reads only `AGENTS.md` files.
  Measured 2026-11-17 against a stub provider that records each request: on
  2.0.25 the system message carries neither file; on 2.2.0 and 2.3.0 it
  carries both, absolute and `~/` paths alike. So on 2.0.x the entries are
  written but inert, and `setup-client.sh`, `openbeast-client status` and
  `client-e2e.sh` (g) say so. The fix is `brew upgrade opencode` (or
  `opencode upgrade`), then `opencode service restart`, which ends the
  running sessions, so pick the moment. No `~/.config/opencode/AGENTS.md` is
  written as a fallback: that file is the user's, and on a current opencode
  it would load the soul twice.

- **One provider per rig**, `openbeast-<rig>`, for every rig with a key (a
  keyless rig gets none until `rig add <rig> --api-key-stdin`: it would only
  401). **`openbeast-rig` is the active rig**, the id every earlier install
  and doc uses; `use` repoints it. Pick per session in the TUI's model
  picker, or `opencode run -m openbeast-beast/<model> …`.
- **Model rows.** A llama.cpp rig (beast) serves any id (llama-server ignores
  it), so it carries the checkout's catalog plus its live row. Any other
  backend (the pair's TensorFold, vLLM, hydra) gets exactly the ids it serves:
  a llama alias there is a 404 or silently the same model.
- **Limits.** opencode's `limit` takes `{context, output}` on the model row
  (both required; the v2 file layout still reads it from `provider.<id>.models`,
  checked with `opencode debug config` on 2026-11-17). `context` is the rig's
  `/api/slot` `model.ctx`: 1,048,576 on the pair, beast's `-c`. That is the
  window of **one conversation**. `capacity.ctx_total` (1,986,560 on the pair)
  is **one** KV pool shared by the 4 slots, not 4 x 1M. `output` is 32768:
  thinking is on by default at the gate and opencode has no reasoning-tail
  recovery, so it gets more headroom than the runner's 16384
  `AGENT_MAX_TOKENS`. Per rig: `openbeast-client rig add spark-hydra --output-limit 16384`
  (`0` = back to the default).
- **The MCP layout.** Everything MCP lives under `mcp.servers.<name>`:
  opencode v2 does **not** merge a v1 entry (`mcp.<name>` with
  `enabled: true`) with v2 ones, and a mixed file loses the v1 ones.
  `setup-client.sh` writes the v2 shape when `opencode --version` is 2.x or the
  file already has `mcp.servers`, and moves other v1 entries in with it (so no
  hand fix-up after a run any more). `codemode: false` turns off v2's Code Mode
  for our server: the model gets `openbeast-tools_bash`,
  `openbeast-tools_write_file` and the rest as direct tools, which is what the
  evals and the conformance checks measure.
- The provider ids `openbeast-*` and the MCP key `openbeast-tools` are distinct
  from the checkout's own `./opencode.json` (`llama-cpp`, `local-tools`), so
  running `opencode` inside the repo cannot shadow them.
- Permissions in v2 are a list:
  `permissions: [{action: "<server>_<tool>", resource: "*", effect: "ask"}]`.
  The last match wins.

**Reasoning (thinking) output.** GLM streams its thinking as
`reasoning_content`; opencode's `@ai-sdk/openai-compatible` provider turns it
into reasoning parts with no extra config (verified 2026-11-17 against the
pair with thinking on: a reasoning part, then the tool call, then the
answer). The TUI shows it as a collapsible thinking block;
`opencode run --thinking` prints it. opencode sends it back as
`reasoning_content` on the assistant turns of the same session, which the GLM
template expects for interleaved thinking. Nothing to set per model: no
`reasoning`/`interleaved` keys are written, and thinking on or off is the
gate's default (`INFERENCE_THINKING`), not a client setting.

**Two opencode v2 behaviors to know:**

- `opencode run` sends its **first** request before local MCP servers finish
  connecting (about 0.8 s), so a one-step prompt may see only opencode's
  built-in tools; from the second step on, `openbeast-tools_*` are there.
  Prompts that need the arsenal right away should name the tools (as
  `client-e2e.sh` does). Likewise `opencode models` from a directory the
  service has not seen yet can list none of the configured providers on the
  first call, and all of them two seconds later.
- The TUI and `opencode run` (without `--standalone`) go through opencode's
  **background service**. It watches its config dir, so `use` is normally
  picked up; if a running session still shows the old rig, `opencode reload`.
  On 2026-11-17 `opencode mcp list` printed "No MCP servers configured" while the log
  (`~/.local/share/opencode/log/opencode.log`) says
  `mcp connected server=openbeast-tools tools=19` is that service's CLI view,
  not a broken server: `client-e2e.sh` (e) lists the tools directly.

`scripts/apps.sh` (F4) writes the same v2 layout for the external-app
servers ([BEAST_APPS.md](BEAST_APPS.md) "opencode").

### F4. beast-apps: external apps through MCP servers

```bash
scripts/apps.sh list                  # every capability, its status here, enabled or not
scripts/apps.sh check ios-build       # probes + the exact fix for anything missing
scripts/apps.sh enable ios-build --dry-run
scripts/apps.sh enable ios-build      # merges into mcp.servers (v2), backs the old file up first
scripts/apps.sh disable ios-build     # removes exactly what enable wrote
```

The Mac on 2026-11-15:

- READY: `ios-build` (enabled, `mobilebuildmcp`), `android-build`,
  `mobile-ui`, `sim-ros`.
- MISSING: `blender`, `figma`, `3d-print` (off by default: risk).

Offline, `check` and `disable` never touch the network; `enable` stages an
npm server from the local npm cache only, and `refresh` runs what is staged
(npm) or `uvx --offline`. Staging servers for an offline Mac and installing
client mode with no internet: [BEAST_APPS.md](BEAST_APPS.md) § Staging
servers for an offline machine, § Offline client. Skill: `app-control`.

### F5. beast-docs: platform docs, mirrored

```bash
./scripts/docs.sh sources                                   # what can be mirrored
./scripts/docs.sh sync swiftui --max-pages 300 --accept-terms
./scripts/docs.sh search NavigationStack
./scripts/docs.sh status                                    # pages, age, STALE flags
./scripts/docs.sh pack mirror.tar.gz                        # carry it; writes mirror.tar.gz.sha256
```

The store is `~/.openbeast/docs` (or `OPENBEAST_DOCS_DIR`). spark-1 has
**no mirror** yet: every source reads "not mirrored". To give the pair one, sync
on the Mac, `pack`, copy, and `unpack` on spark-1 ([H8](#h8-beast-docs-mirror)).
Models reach it through the `platform_docs` MCP tool or `scripts/docs.sh`.
Skill: `platform-docs`.

### F6. The eval harness host: benchmarking the pair

The v5 benchmark runs **on a harness host** (the Mac is the documented one;
a Linux box works with the caveats in [evals/README.md](../evals/README.md)
"Residual gaps") and sends only chat calls to the pair. The agents' tools,
the validators and every compiler run on the harness host, never on the
Sparks, so the toolchains belong there.

**The repo provisions them.** `evals/toolchains.lock` pins what the host
needs, per platform (darwin-arm64, linux-aarch64, linux-x86_64), with a
sha256 for every file; `scripts/eval-host.sh` checks, installs and exports
them. The versions are the ones the v5 units were proven with
(`tests/prove_task.py`, 2026-11):

| Tool | Pin | Online source | Offline (`--from DIR`) |
|---|---|---|---|
| zig | 0.16.0 | ziglang.org, community mirrors first (`@mirrors`) | ark `files/zig/0.16.0/<platform>/` |
| rust | rustc 1.100.0 | rustup-init 1.29.3, then `--profile minimal --default-toolchain 1.100.0` | ark `files/rust/1.100.0/<platform>/`, the standalone installer |
| go | 1.23.3 | dl.google.com | a `fetch` mirror (the ark carries go 1.27.4, not this pin) |
| python | 3.12.16 + `agents/requirements.lock` | python-build-standalone (the ark catalog's pin), then `pydeps.sh install --require-hashes` | ark `files/cpython-3.12/3.12.16/<platform>/` + `--wheelhouse DIR` |
| gcc / g++ / git | present | **not installed by us**: `xcode-select --install` on a Mac, `sudo apt install build-essential clang git` on Linux | the distro's .debs |
| a device key of its own | | `./scripts/clients.sh enroll eval-host --label "eval harness"` on spark-1 ([E7](#e7-device-keys-for-beast-gate)), into a 0600 file | same |
| the gate's read timeout | | `EDGE_READ_TIMEOUT=3600` in spark-1's `openbeast.conf`, then `systemctl --user restart openbeast` | same |

```bash
./scripts/eval-host.sh check                  # one line per tool: ok / missing / wrong / system / blocked
./scripts/eval-host.sh setup                  # install what is missing or wrong (idempotent; exit 3 = sha256 mismatch)
eval "$(./scripts/eval-host.sh env)"          # PATH, RUSTUP_HOME, CARGO_HOME, OPENBEAST_EVAL_PY
# offline: from an ark drive, or a mirror made on a connected box
./scripts/eval-host.sh fetch --to /Volumes/ARK/eval-host --platform all [--wheels]
./scripts/eval-host.sh setup --from /Volumes/ARK/eval-host --wheelhouse ~/wheels
```

Everything lands in `~/.local/opt/openbeast-eval` (`--prefix` /
`OPENBEAST_EVAL_PREFIX`): `bin/` links, `zig-0.16.0/`, `go-1.23.3/`,
`rustup/` + `cargo/`, `cpython-3.12.16/` and `venv/`. Nothing outside the
prefix is written and nothing runs as root. A tool that is already on PATH
at the pinned version is used as it is (the Mac's Homebrew go1.23.3). The
copy that counts is the one the validators will pin
(`vlib.pin_toolchain`): the first root-owned copy on PATH, else the first.
A root-owned copy of the wrong version (an apt `go` in `/usr/bin`) outranks
the prefix, and `check` says `blocked`: remove it.

**A live run does this itself.** `run_eval.py`, `profile.py` and
`benchmark_all.py` run `check` first and `setup` when it fails, put the
prefix first on PATH before the validator toolchain is pinned, and re-exec
under the eval venv's python (`$OPENBEAST_EVAL_PY`) when started by another
one. `--no-provision` (or `OPENBEAST_EVAL_NO_PROVISION=1`) only reports;
profile.py's preflight still refuses a host that fails the check
(`HOST_TOOLS`: `go`, `gcc`, `g++`, `rustc`, `zig`, `git`; zig must print
exactly `0.16.0`). Two guards then keep a broken interpreter off the
board: a live run refuses to start when its python cannot import the
stdlib and the runner's packages, and an agent that dies within 2 s on an
interpreter-startup error is an `interpreter_error` row (never cached,
never scored), followed by a re-check that aborts the run when the
interpreter is broken. That is the 2026-11-16 failure: a venv in a scratch
dir whose python symlink was rewritten mid-run, 22 units scored as FAILs
([HARNESS_NOTES.md](HARNESS_NOTES.md), "Eval host provisioning").

**Why the read timeout.** The gate waits `EDGE_READ_TIMEOUT` seconds (default
600) for each byte from the model. For a non-streaming completion that is the
whole generation, so a long agent turn ends as a gate 504 that the harness
records as a server error, not as the model's answer. spark-1 runs 3600.

**The in-flight cap.** Every agent of a run shares the one key, and the gate
admits `EDGE_MAX_INFLIGHT` generations per key. Unset, that is
`INFERENCE_SLOTS` (4 on the pair), so `--jobs 4` runs 4 workers. `run_eval`
reads the cap from the gate and clamps `--jobs` to it, and `profile.py`
refuses a profile when 4 concurrent requests would draw a 429.

**Run it** (from a full checkout on the harness host):

```bash
export OPENBEAST_SPARK_API_KEY="$(cat ~/.config/openbeast/spark-hydra-gate.key)"   # benchmark_all's glm-5.3-flash entry reads it
./scripts/eval-host.sh setup && eval "$(./scripts/eval-host.sh env)"            # what preflight will check, provisioned
"$OPENBEAST_EVAL_PY" evals/profile.py --model glm-5.3-flash --budget-h 4 --jobs 4   # evals/README.md "How to run a 4-hour profile"
"$OPENBEAST_EVAL_PY" evals/benchmark_all.py --models glm-5.3-flash                  # the plain sweep
```

(`python3 evals/profile.py …` works too: it provisions and re-execs under
the eval venv itself.) Never point the harness at toolchains or a venv in a
scratch or temp directory: the harness pins each compiler's install tree
when it starts and refuses a validation when that tree changes, and a venv
dies with its base interpreter. The prefix is durable on purpose.

---

## G. Day-2 operations

### G1. Health

```bash
# on spark-1
~/openbeast/scripts/healthcheck.sh               # expect "All 9 services healthy" + the unified-memory line
curl -s http://127.0.0.1:8000/health             # ok, streams, pool tokens, requests_total
~/openbeast/scripts/doctor.sh                    # 39 ok, 1 warning on 2026-11-15 (SSD wear needs a sudoers rule)
~/openbeast/start.sh --status                    # stack + island state
python3 ~/openbeast/agents/island.py status      # online / island, and why
systemctl --user status deepseek-v41-inference openbeast --no-pager
# from the Mac
curl -fsS https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -m json.tool | head -30
openbeast-client status && openbeast-client rigs   # the client's view (F2)
```

### G2. Logs

```bash
journalctl --user -fu deepseek-v41-inference          # rank 0 + start.sh
docker logs -f dsv41-1017-exl3-tf                        # rank 0 container
ssh max@10.100.216.2 'docker logs -f dsv41-1017-exl3-tf' # rank 1 (quote it)
tail -f ~/openbeast/.run/stack.log                   # the command center's supervisor
journalctl --user -u openbeast --since today
```

### G3. Restarts: only through `systemctl --user` while the unit is enabled

```bash
systemctl --user restart deepseek-v41-inference       # both ranks
systemctl --user stop deepseek-v41-inference
systemctl --user restart openbeast                   # the command center (WebUI, tools, gate, …)
./scripts/healthcheck.sh --restart                   # restart only what is DOWN (the watchdog does this every 5 min)
```

Never `docker stop dsv41-1017-exl3-tf`, the recipe’s stop script or
`./stop.sh` by hand while the matching unit is enabled. systemd restarts the
pair behind you (`Restart=on-failure`), or the oneshot `openbeast` unit stays
"active" over a stopped stack.

After a mid-stream generation error on several ranks, restart **all** of them:
restart the unit, never one rank.

### G4. Memory headroom and the GB10 stack cap

The following measurements and recipe settings apply to GLM. GB10 memory is **unified**: the model's ~80 GiB per Spark is inside MemTotal
(124,607 MiB). Two caps keep the box from freezing. Unified memory freezes
rather than OOMs.

- **The engine side** ([D1](#d1-the-mia-recipe-what-serves-today)):
  `KV_POOL_GIB=10` fixes the KV pool, and `MEMORY_RESERVE_GIB=17.5` leaves host
  memory for the command center.
- **The stack side** (`start.sh`): `start.sh -d` runs the stack in a
  memory-capped systemd scope. On a unified-memory box running its own model,
  the cap is re-sized once the model is up, to **MemAvailable minus
  `MEM_RESERVE_GB`** (default 3), added on top of what the stack already
  holds. The chat jobs' cap is derived the same way, and comes only out of
  that spare: the stack is never capped below its own current usage. When
  less than the jobs' 1 % of RAM is spare, start.sh prints a "no headroom"
  warning instead of shrinking the stack under what it holds.
  75 % of a MemTotal the model already holds would protect nothing.
  `healthcheck.sh` and `doctor.sh` warn when MemAvailable falls below
  `MEM_RESERVE_GB`.

Watch the `Unified memory: N MiB available` line in `healthcheck.sh`. With the
pair serving at `PARALLEL=4` and the full stack up, it read about **9.7-9.9 GiB**
on 2026-11-15, and spark-2 (rank 1 only) about 11 GiB. Before adding anything to
spark-1 (an extension, a second model, a heavy build), check that line. Do not
try `PARALLEL=8` without raising the reserve. The recipe's notes measured rank
0's lowest free memory at 7.0 GiB for `PARALLEL=8`.

### G5. The watchdog and self-healing

- **`openbeast-watchdog.timer`** runs `healthcheck.sh --restart` every 5 min.
  It restarts DOWN compose services. A down managed engine is only reported,
  never restarted. The unmanaged TensorFold pair is systemd's job, not the
  watchdog's.
- **`TF_GLM_MULTI_WATCHDOG_EXIT=1`**: a stalled rank exits, and the unit
  restarts the pair.
- **`healthcheck.sh`** also catches a stalled TensorFold pair that still says
  `ok`, via the `.run/tf-liveness` record.
- **beast-island** (`ISLAND=auto`): if the internet goes away, the web tools
  refuse at once after 90 s, and the stack rejoins by itself.

### G6. Updating

**Update everything, in order.** This moves a built pair to the latest
`main` and brings along every component a recent change added. Commands run
on the host named; **sudo** steps need Max at a terminal on that box. Building
from scratch is the other checklist, [Install everything, fresh](#install-everything-fresh).
On the Sparks, `scripts/update.sh` is **not** the update: it moves upstream
pins (llama.cpp to `master`, images to their moving tags) and rewrites tracked
files, and a bare run dies at its first step on a Spark, which has no
`llama.cpp/` clone. It is a maintainer tool ([UPDATING.md](UPDATING.md)).

**1. Pick a quiet moment.** Restarting the command center restarts
beast-gate, which drops every generation in flight through `:8443`. A
benchmark running from the Mac loses its in-flight units. Check
`slots.busy` first (from the Mac):
`curl -sk https://spark-1.tail4109f9.ts.net:8444/api/slot | python3 -c 'import json,sys; print(json.load(sys.stdin)["slots"])'`.
Inference itself (`spark-hydra-inference`) is not restarted by any step
below unless the recipe or the engine changes.

**2. Read what is coming** (spark-1):

```bash
cd ~/openbeast && git fetch && git status -sb             # never pull over local edits
git log --oneline HEAD..origin/main                        # what the pull brings
git diff --stat HEAD origin/main -- docker-compose.yml agents/requirements.lock extensions/ scripts/backends/
```

Read [UPDATING.md](UPDATING.md) "Upgrade notes" for any line the diff
touches. Offline, `git fetch /media/usb/openbeast.bundle main` instead of
`git fetch`, and compare against `FETCH_HEAD` instead of `origin/main`.

**3. Back up Open WebUI when its pin moves** (spark-1). If the diff touches
the `open-webui` line of `docker-compose.yml`, the new image migrates the
database at first start and cannot be downgraded past it (the 0.11.0 → 0.13.3
sweep, [UPDATING.md](UPDATING.md) "Upgrade notes: the 2026-11 dependency
sweep"). Stop the stack through its unit, then copy the volume:

```bash
systemctl --user stop openbeast                    # the command center only; inference keeps running
mkdir -p ~/backups
docker run --rm -v openbeast_open-webui-data:/data:ro -v ~/backups:/backup alpine \
  tar czf /backup/open-webui-data-$(date +%F-%H%M).tgz -C /data .
```

spark-1 did this before the 0.13.3 sweep:
`~/backups/open-webui-data-2026-11-16-0024.tgz`. `alpine:latest` is in
spark-1's image store, so the command also works offline. On a box without
it, any image already present that has `tar` does the same job. The stack
stays down until step 5's start; skip the stop if the pin did not move.

**4. Pull on both, Python deps on the head** (both Sparks):

```bash
cd ~/openbeast && git pull --ff-only && ./scripts/pydeps.sh install     # spark-1
ssh max@10.100.216.2 'cd ~/openbeast && git pull --ff-only'              # spark-2: same commit
git -C ~/openbeast log --oneline -1; ssh max@10.100.216.2 'git -C ~/openbeast log --oneline -1'   # must match
```

`pydeps.sh install` puts the lock this commit ships into `~/.local`, with
`--require-hashes`, and is a no-op when nothing moved. `start.sh` refuses to
start on a lock it does not match, so this comes before the restart. spark-2
needs no Python layer: it runs no OpenBeast service, but its checkout must
match for `--verify`, `spark-node.sh`, `spark-fabric-mtu.sh` and
`spark-cluster.sh check`. Offline:
`git pull --ff-only /media/usb/openbeast.bundle main` on each, and
`./scripts/pydeps.sh install --from /media/usb/wheels-spark` on spark-1, with
the wheelhouse filled by `pydeps.sh wheelhouse` on an online aarch64 box
([H6](#h6-the-command-center-wheels-and-compose-images)).

**5. Restart the command center** (spark-1):

```bash
systemctl --user restart openbeast       # or "start", after step 3's stop
~/openbeast/scripts/healthcheck.sh       # "All 9 services healthy"
~/openbeast/scripts/doctor.sh
```

`start.sh -d` pulls the image digests the new commit pins (online), restarts
beast-gate, the tool server, beast-chat, beast-artifact and the dashboard,
and runs `configure-webui.sh`, which re-applies the tool servers and installs
or updates the random seed filter. Offline, new images come from a bundle
built on an online Spark at the **same** commit:
`./scripts/bundle.sh verify` + `install` before the restart
([H6](#h6-the-command-center-wheels-and-compose-images)). `bundle.sh install`
rewrites `docker-compose.yml` to content IDs and keeps
`docker-compose.yml.pre-bundle`; `git checkout -- docker-compose.yml` before
the next pull, or that pull conflicts.

**6. The per-change steps.** Each row is a change a recent PR added; do the
ones whose "done when" is not yet true.

| Change | Where | Do | Done when |
|---|---|---|---|
| spark-guard rules (#168, and any change to spark.env ports or the fabric) | each Spark, **sudo** | `scripts/backends/spark-guard.sh --status` (the rules readout works without sudo); if `STALE`, `sudo scripts/backends/spark-guard.sh` ([B3](#b3-firewall-spark-guard)) | `rules: current` on both |
| the gate's in-flight cap follows `INFERENCE_SLOTS` (#168) | spark-1 | nothing beyond step 5, with `EDGE_MAX_INFLIGHT` unset | `/api/slot` `admission.per_device_inflight` is 4 (live since 2026-11-16) |
| `TS_HOSTNAME` (#160) | spark-1 (and any Spark that will run `setup-tailscale.sh`) | `grep -q '^TS_HOSTNAME=' openbeast.conf \|\| echo 'TS_HOSTNAME=spark-1' >> openbeast.conf` ([E6](#e6-publish-on-the-tailnet-setup-tailscalesh-as-tailscaleds-operator)) | the key is set (spark-1: yes) |
| Open WebUI 0.13.3, SearXNG, ntfy (#161) | spark-1 | step 3 before, step 5 does the rest | WebUI healthy, chats still there; `docker exec open-webui sh -c 'grep -m1 version /app/package.json'` says 0.13.3 |
| `ARTIFACT_LOGIN_FROM=unix` (#165) | spark-1 | `echo 'ARTIFACT_LOGIN_FROM=unix' >> openbeast.conf`, `systemctl --user restart openbeast`, then `./scripts/setup-tailscale.sh --publish-artifact` (as operator, no sudo) | `tailscale serve status` shows `:8446` → `unix:…/.run/artifact.sock` |
| engines hardening (#174) | each Spark | only if native TensorFold is in use: `scripts/engine.sh status`, then `scripts/backends/spark-cluster.sh check` from spark-1 | `check` passes (same commit, same engine pins) |
| fabric MTU 9000 (#178) | each Spark, **sudo**, then a reboot of both | `sudo scripts/backends/spark-fabric-mtu.sh --stage` on both, then the reboot and `--verify` (all peer IPs in one call) in [B4](#b4-fabric-mtu-9000-spark-fabric-mtu) | `--verify` prints `VERIFIED` on both (the pair: done 2026-11-17) |
| thinking on by default (#202, #203) | spark-1 | `printf '%s\n' INFERENCE_THINKING=on AGENT_MAX_TOKENS=16384 >> openbeast.conf`, then step 5 (which re-runs `configure-webui.sh`) ([E2a](#e2a-recommended-serving-configuration-from-v5)) | `/gate/health` says `"thinking_default":"on"`; Admin Panel → Functions → "Thinking Default" on (the pair: done 2026-11-17) |
| multi-rig client (#204) | each Mac / Linux client | step 7; an older single-rig install migrates on the first `openbeast-client` command ([F2](#f2-client-mode-one-or-several-rigs)) | `openbeast-client rigs` lists the rig with `*`; `scripts/client-e2e.sh` all PASS |
| random seed for Regenerate (#181) | spark-1 | step 5 (or `./scripts/configure-webui.sh` alone, no restart) | Admin Panel → Functions → "Random Seed", on and global |
| `EDGE_READ_TIMEOUT` | spark-1, when an eval harness targets the pair | `echo 'EDGE_READ_TIMEOUT=3600' >> openbeast.conf`, `systemctl --user restart openbeast` | set (spark-1: 3600) |
| zig 0.16.0 for `language_reference` | spark-1 (and the harness host) | `ark restore zig` from a drive, or the pinned tarball ([H7](#h7-test-and-language-toolchains)) | `zig version` prints 0.16.0 |
| beast-docs mirror | spark-1 | sync + pack on the Mac, `docs.sh unpack` on spark-1 ([H8](#h8-beast-docs-mirror)) | `docs.sh status` lists the sources, none `STALE` |
| beast-ark | the drive | `DRIVE/ark verify DRIVE`; a maintainer moves pins with `ark.sh update` online ([BEAST_ARK.md](BEAST_ARK.md) §10) | `verify` passes |

**7. Clients** (each client): `openbeast-client update` (it re-probes every
registered rig and rewrites the catalog, [F3](#f3-what-the-client-writes-into-opencode)),
then `scripts/client-e2e.sh`. Offline: pull the client's repo from a bundle, then
`pydeps.sh install --from` a wheelhouse for the client's platform
([UPDATING.md](UPDATING.md) "Clients update themselves").

**8. The OS** (each Spark, **sudo**, rarely). `libnccl2` is held, so
`sudo apt update && sudo apt upgrade` leaves it alone and moves tailscale and
the rest of DGX OS. A driver, kernel or firmware update needs the reboot
order from [B4](#b4-fabric-mtu-9000-spark-fabric-mtu) step 3: stop inference,
reboot the worker, then the head. Offline: carry new .debs and pins as in
[H3](#h3-debs-libnccl2-and-tailscale). OpenBeast never upgrades the driver.

**Pending on the live pair (read 2026-11-17, evening, over ssh, no sudo).**
What the checklist above still has to do on spark-1 and spark-2:

- **spark-guard `STALE` on both** (sudo, step 6): the installed rules guard
  `29501` but not `29502`.
- **`ARTIFACT_LOGIN_FROM` unset** on spark-1: beast-artifact still honours
  the login header from loopback, and `:8446` proxies to `127.0.0.1:3004`
  (step 6; [Known issues](#known-issues-and-errata-v190)).
- **No zig and no docs mirror** on spark-1 (step 6).
- **The MTU A/B** ([B4](#b4-fabric-mtu-9000-spark-fabric-mtu)): the MTU
  itself is done.
- From earlier: the plaintext gate keys in `~/.config/openbeast/gate-keys/`
  and `~/.config/openbeast/webui-max-password` are still on spark-1
  ([E7](#e7-device-keys-for-beast-gate), [E5](#e5-webui-auth-and-the-admin-account-before-publishing)).

Done since the 2026-11-16 reading: both Sparks pulled to one commit
(`d3727185`, #203); the command center restarted, so the random seed and
Thinking Default filters are live; `INFERENCE_THINKING=on` and
`AGENT_MAX_TOKENS=16384` in spark-1's `openbeast.conf`; MTU 9000 staged,
rebooted and verified on both.

The detail behind steps 4 and 6 follows.

**OpenBeast** on every node, the same commit everywhere. The worker runs no
OpenBeast services. Its checkout matters for the lock (`--verify`), for
`spark-node.sh`, `spark-fabric-mtu.sh` and `spark-cluster.sh check`.

**The engine:**

- Native TensorFold: `scripts/engine.sh update tensorfold` (or
  `install-native.sh --version V`) on every Spark, then restart all ranks.
  `spark-cluster.sh check` fails while the ranks disagree on the native
  binary or on any spark.env engine pin (`TENSORFOLD_VERSION`, `_REF`,
  `_IMAGE*`, `VLLM_IMAGE*`), so run it before `start`.
- The recipe: a new recipe version or image means a new `git pull` in
  `~/openbeast-recipes/glm53`, `prepare.sh` (online), the image carried to every
  rank, and `systemctl --user restart spark-hydra-inference`.
- Before moving any engine pin, read [ENGINES.md](ENGINES.md) "Research notes".

**A model:** a new profile is `model-inspect.sh` → `model-fetch.sh` (lock) →
`conformance.sh` → serve. The lock of a served model never changes. A new
revision is a new profile.

**Open WebUI's Regenerate on the GLM pair.** TensorFold falls back to a
fixed seed when a request names none: three unseeded requests at temperature
0.6 returned one text, seeds 1/2/3 three texts, seed 1 twice the same text
(probed 2026-11-16). So Regenerate in WebUI, which re-sends the same request
without a seed, returned the same answer. `configure-webui.sh` (run by every
`systemctl --user restart openbeast`) installs the global Open WebUI filter
`agents/webui_functions/random_seed.py`, which adds a random seed to every
WebUI chat request without one. To apply it after a `git pull` without a
restart, run only that script; it touches no inference unit:

```bash
cd ~/openbeast && ./scripts/configure-webui.sh     # idempotent; "Random seed filter … already installed" on a re-run
```

Check it in Admin Panel → Functions ("Random Seed", on, global). A seed set
in a chat's Controls or a model's params still wins, so a pinned seed stays
reproducible. It is WebUI-only: opencode, the Mac client, the eval runner
and anything else on `:8000` / `:8443` still get TensorFold's fixed default
(`seed` in the request is the fix there). `WEBUI_RANDOM_SEED=false` in
`openbeast.conf` switches the filter off at the next run.

### G7. Rotating keys and secrets

| Secret | Rotate with | Notes |
|---|---|---|
| a device key | `./scripts/clients.sh rotate <id>` | hot-reloaded; give the device its new key |
| a lost device | `./scripts/clients.sh revoke <id>` | immediate |
| WebUI admin password | WebUI Settings → Account, then update `WEBUI_ADMIN_PASSWORD` if it is the configured admin | `systemctl --user restart openbeast` so `configure-webui.sh` uses it |
| HF token | replace `~/.config/openbeast/hf-token` (0600) | only used online |
| TensorFold / vLLM API key files | rewrite the 0600 file named in `spark.env`, restart all ranks | the recipe (v0.6.0) has no key |

**A key that was printed.** Rotate at once, then check both sides:

```bash
cd ~/openbeast && ./scripts/clients.sh rotate macbook     # on spark-1: the new key prints once
# on the device: write the new key straight into its 0600 file (on the Mac,
# ~/.config/openbeast/spark-hydra-gate.key), then from the device:
curl -s -o /dev/null -w '%{http_code}\n' https://spark-1.tail4109f9.ts.net:8443/v1/models \
  -H "Authorization: Bearer $(cat ~/.config/openbeast/spark-hydra-gate.key)"   # 200 with the new key
```

The old key must now get 401 from the same call. `rotate` is hot-reloaded:
no restart of the command center is needed.

**Incident, 2026-11-17.** The Mac's gate key was printed into an agent
transcript: a BSD `sed` redaction that used `\|` alternation matched nothing
on macOS (BSD basic regexes have no `\|`), so the "redacted" output was the
raw key. The key was rotated the same minute with `clients.sh rotate
macbook`; the old key answers 401, the new one 200. Lessons, for anyone
(human or agent) handling secrets on these boxes:

- **Never print a secret to redact it afterwards.** Read keys from their
  0600 files inside the command that uses them (`$(cat …)`, as above,
  `--api-key-stdin <`, `--key-file`), so the value never reaches a terminal
  or a log.
- **Never redact with `sed` alternation.** `\|` is a GNU extension; BSD sed
  (macOS) silently treats it as a literal, matches nothing and passes the
  secret through. GNU and BSD also disagree on `-E`/`-r` and `\b`. If output
  must be filtered, use python (`re.sub`), which behaves the same on every
  box, and test the filter on a dummy value first.
- **Assume an agent transcript is a shared log.** It is kept, exported and
  sometimes published; a key that reached one is a leaked key. Rotate, do
  not argue.

### G8. When X fails: troubleshooting

Every row below was hit on the way to the live pair.

| Symptom | Cause | Fix |
|---|---|---|
| WebUI / SearXNG DOWN; `.run/stack.log`: "docker daemon not ready" | the systemd user manager started before `max` joined the `docker` group | reboot once; `systemd-run --user --wait --pipe docker info` proves it |
| `ssh spark-1` cannot resolve `spark-3dc6.local` | mDNS right after a reboot, or away from home | `ssh -o HostName=192.168.40.153 spark-1` at home; the tailnet name with `HostKeyAlias` away ([F1](#f1-ssh-from-the-mac)) |
| `ssh spark-1.tail4109f9.ts.net` asks to trust a new host key | no `HostKeyAlias` | add `HostKeyAlias spark-3dc6.local` to that `Host` block |
| `ssh spark-1 cat ~/.config/openbeast/x`: "No such file" | the Mac's shell expanded `~` to `/Users/…` | quote the remote command: `ssh spark-1 'cat ~/.config/openbeast/x'` |
| ranks cannot reach each other | a firewall drops the peer on the fabric (ufw, if someone enables it without the fabric rules) | `spark-root-setup.sh` adds a ufw rule per Up link for that link's subnet; `spark-guard.sh` always accepts the fabric. ufw itself is **disabled** on DGX OS (`/etc/ufw/ufw.conf` `ENABLED=no`) and was never what blocked or protected anything here |
| after a reboot, ping and ssh over the fabric work but the pair hangs loading or mid-prefill | a fabric MTU mismatch: one Spark at 9000 (`spark-fabric-mtu.sh` staged and rebooted), the other still at 1500, so frames over 1500 bytes are dropped | `scripts/backends/spark-fabric-mtu.sh --status` on both; reboot the one still at 1500 (stage it first), or `--unstage` the other and reboot it; `--verify` ([B4](#b4-fabric-mtu-9000-spark-fabric-mtu)) |
| `nc -z <tailnet IP> 29551` succeeds from a laptop | rank 0's TCPStore binds `*:29551`, ufw is off, and Tailscale's `ts-input` accepts tailnet traffic first | `sudo scripts/backends/spark-guard.sh` on each Spark ([B3](#b3-firewall-spark-guard)) |
| `tensorfold capabilities` shows NCCL `found:false` | no libnccl2 | `spark-root-setup.sh` installs 2.28.9+cuda13.0 and holds it |
| `model-fetch --verify`: "unlocked file .cache/huggingface/…" | `hf download --local-dir` metadata | fixed: hidden paths are skipped by both lock and verify |
| `model-fetch --verify` / `--locate` exit 3 | no lock in this checkout | pull the commit with the lock, or `--verify --trust-first-use` for a local SOURCE |
| the recipe serves, but the lock "does not cover" it | the HF-cache snapshot lost its hard links (re-download, rename-save) | re-run the `stat` check (C2); re-link with `cp -al` and re-verify |
| a container engine on an offline worker tries to pull | `*_IMAGE_DIGEST` pin; `docker save/load` drops the RepoDigest | pin `*_IMAGE_ID` instead (`sync-image` prints the line) ([H4](#h4-container-images)) |
| `TENSORFOLD_ENGINE=container` fails offline | it pip-installs TensorFold from GitHub and deps from PyPI at every start | use the native engine (default) or the recipe; or, on a connected box, build an image with TensorFold at `TENSORFOLD_REF` and its locked deps installed, carry it with `spark-cluster.sh sync-image` / `save-image`, pin `TENSORFOLD_IMAGE_ID` and set `TENSORFOLD_IMAGE_PREBUILT=true` |
| box freezes / ssh hangs under load | unified memory exhausted (it freezes rather than OOMs) | keep `KV_POOL_GIB` / `MEMORY_RESERVE_GIB` as in D1; watch `Unified memory` in healthcheck ([G4](#g4-memory-headroom-and-the-gb10-stack-cap)) |
| pair stops answering mid-stream, `/health` still `ok` | a stalled collective | `TF_GLM_MULTI_WATCHDOG_EXIT=1` makes the rank exit so the unit restarts both; healthcheck's liveness check flags it |
| `:443` not published after `setup-tailscale.sh` | the running WebUI still had auth off | `systemctl --user restart openbeast`, then re-run `setup-tailscale.sh` ([E5](#e5-webui-auth-and-the-admin-account-before-publishing)) |
| `:8443` answers 401 | no device key, or a revoked one | `clients.sh list`; give the client its key |
| Regenerate in Open WebUI returns the same answer, word for word | TensorFold uses a fixed seed when the request names none | `./scripts/configure-webui.sh` installs the random seed filter; check Admin Panel → Functions → "Random Seed" is on and global, and `WEBUI_RANDOM_SEED` is not `false` ([G6](#g6-updating)) |
| GLM answers with no reasoning (no "Thought for…" block in WebUI, no `reasoning_content` from :8443) | `INFERENCE_THINKING` unset, the gate not restarted since it was set, the WebUI filter off, or the client sent `enable_thinking: false` / another thinking control | the local `/gate/health` must say `"thinking_default":"on"` (else `systemctl --user restart openbeast`); Admin Panel → Functions → "Thinking Default" on and global (`./scripts/configure-webui.sh`); check the client's own params ([E2a](#e2a-recommended-serving-configuration-from-v5)) |
| opencode on the Mac lost `openbeast-tools` (or another server) | a v1 `mcp.<name>` entry next to v2 `mcp.servers.<name>`: v2 does not merge them | move everything under `mcp.servers`, drop `enabled` ([F3](#f3-what-the-client-writes-into-opencode)); `setup-client.sh` does this itself now |
| `tests/test_scripts.sh` failures on a copy without `.git` (7 seen on spark-2) | some suites need a git checkout | run from a git checkout |
| shell suites fail on the Mac but pass on a Spark | BSD vs GNU tools (`tests/test_engines.sh` needs GNU coreutils; `test_skill_import.sh` showed 5 BSD-only failures on 2026-11-15) | run shell suites on a Spark ([SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md) "Tests") |
| `python3 -m pytest` says no module | no pytest for that python (none on spark-1 or the Mac's default python, 2026-11-15) | the hash-pinned test venv ([H7](#h7-test-and-language-toolchains)) |
| `bootstrap.sh` offline asks for `bundle.sh build` "on a connected box" | the bundle must be built for the **target's** platform | build it on an online Spark (aarch64, same overlay2 store) ([H6](#h6-the-command-center-wheels-and-compose-images)) |
| web tools stall instead of refusing in a spawned agent | `agents/tools.py` (era-hashed) has no island guard; only `mcp_server.py` does | expected (BEAST_ISLAND "Known limits"); web_search is bounded at 15 s, fetch at 45 s |

---

## H. Fully offline operation

### H1. Two switches, and what keeps working

| Switch | Meaning | Effect |
|---|---|---|
| `ISLAND=auto` (default) / `force` (`./start.sh -d --offline`) | runtime: "the internet just went away" | `web_search` / `fetch` (MCP + `:3001`) refuse at once; WebUI in `OFFLINE_MODE`; compose `--pull never` when islanded at launch; healthcheck excuses an offline Tailscale ([BEAST_ISLAND.md](BEAST_ISLAND.md)) |
| `OFFLINE=true` in `openbeast.conf` (env `OPENBEAST_OFFLINE`) | install: "this box never has internet" | `bootstrap.sh`, `update.sh`, `engine.sh update`, `model-fetch.sh`, `apps.sh refresh` refuse the network up front and say what to stage instead |

**What keeps working offline:**

- **Inference** works: the recipe runs with `PREPARE=0` and `HF_HUB_OFFLINE=1`,
  and everything is local.
- **The whole command center** works except the web: WebUI, tools, agents,
  skills, files, beast-chat, beast-artifact, beast-gate, beast-slot.
- **opencode on spark-1** is not set up: it is not installed there, and the
  repo's `opencode.json` points at llama.cpp's `:8080`, not `:8000`
  ([SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md)). Use `./agent.sh` on the box,
  or opencode on the Mac in client mode (F2), which keeps working over the
  LAN or the tailnet.
- **`./agent.sh`** works on spark-1.
- **`language_reference`, `platform_docs`, `scripts/docs.sh search`** work
  against what is mirrored.
- **beast-ark** works: `verify`, `restore` and `serve` an ark drive (H8a,
  [BEAST_ARK.md](BEAST_ARK.md)).
- **The tests** work (hermetic).
- **The tailnet** keeps working wherever the LAN still routes between devices.
  Tailscale needs its coordination server to set up new paths, so existing
  direct connections are the ones that survive.

What does not work: `web_search` (refused), `fetch` (refused), WebUI's built-in
Web Search toggle (empty results), Hub downloads, image pulls, apt, pip from an
index, `git pull` from GitHub.

### H2. The repo

`git bundle` carries the whole history; a plain copy of a checkout works too:

```bash
git -C ~/openbeast bundle create /media/usb/openbeast.bundle --all     # online box
git clone /media/usb/openbeast.bundle ~/openbeast                      # closed box
git -C ~/openbeast log --oneline -1                                     # the commit you expect
```

### H3. .debs: libnccl2 and tailscale

On a Spark that can reach apt's repos (apt checks the repo signatures as it
downloads):

```bash
mkdir debs && cd debs && apt-get download libnccl2=2.28.9-1+cuda13.0 tailscale=1.104.7 && cd ..
~/openbeast/scripts/backends/spark-root-setup.sh --check-debs debs        # no root: sha256 vs the pins -> "OK: debs holds the pinned .debs"
sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N --debs debs --guard   # on the closed Spark
```

A `.deb` given by path skips apt's signature check. So `--debs` installs only
those exact versions, and only if each matches the sha256 pinned in the script
(`NCCL_DEB_SHA256`, `TS_DEB_SHA256`, taken from the signed repo indexes). A
newer tailscale means bumping `TS_DEB_VERSION` / `TS_DEB_SHA256` there
(`apt-cache show tailscale=<V> | grep SHA256`). A closed box cannot finish
`tailscale up` without a route to the coordination server. The script says so
and continues; re-run it once there is a route.

### H4. Container images

**The recipe image** is `tensorfold-glm53:v0.6.0`, ID
`sha256:ead0174052ac16b07d98fba9b68e7d7101b1e1db5c0ccdbde62f41ccf229c647`. The
recipe checks it by **tag**, so `docker load` satisfies it:

```bash
docker save tensorfold-glm53:v0.6.0 | ssh max@10.100.216.2 docker load      # spark-1 -> spark-2 over the fabric
docker save -o tensorfold-glm53-v0.6.0.tar tensorfold-glm53:v0.6.0 && sha256sum tensorfold-glm53-v0.6.0.tar   # to a stick
docker load -i tensorfold-glm53-v0.6.0.tar                                    # on the closed Spark
docker image inspect -f '{{.Id}}' tensorfold-glm53:v0.6.0                     # must print the ID above
```

The ID compares across the Sparks because both use the same classic overlay2
store. `scripts/bundle.sh` explains why it would not compare across store kinds.

**OpenBeast's container engines** (`spark-node.sh` with vLLM or container
TensorFold): `docker save | docker load` keeps an image's ID but drops its
RepoDigest. A digest pin (`*_IMAGE_DIGEST`) would therefore make an offline
worker try a registry pull. **Pin by ID instead:**

- `VLLM_IMAGE_ID` / `TENSORFOLD_IMAGE_ID` in `spark.env` win over a digest.
- The container runs as `sha256:<id>`; docker never pulls an ID.
- `spark-cluster.sh sync-image IMAGE` prints the exact line to pin.
- `save-image IMAGE FILE` writes the image to a file for a cluster with no
  registry at all.

The container TensorFold engine still needs GitHub and PyPI at every start, so
it is not offline-capable. The native engine is.

### H5. Native TensorFold and the engine plane

On any connected box, the Mac included:

```bash
scripts/backends/tensorfold/install-native.sh --stage tf-1.2.7                       # stages linux-aarch64 by default
scripts/backends/tensorfold/install-native.sh --from tf-1.2.7 --platform linux-aarch64   # on the Spark
# or through the engine plane, which also pins and records history:
scripts/engine.sh stage tensorfold --version 1.2.9 --platform linux-aarch64 /media/usb/tf
scripts/engine.sh install tensorfold --from /media/usb/tf
```

Both ends verify:

- The `.sha256` must name exactly `tensorfold-<V>-linux-aarch64.tar.gz`.
- Its ed25519 signature is checked against the vendored key. That needs
  OpenSSL ≥ 3 or python3 `cryptography`. On macOS the script finds Homebrew's
  `openssl@3` itself; LibreSSL and OpenSSL 1.1 cannot check ed25519.

Under `OFFLINE=true`, `engine.sh update` without `--from` refuses and says why.

### H6. The command center: wheels and compose images

`./bootstrap.sh` needs PyPI and the compose images. `scripts/bundle.sh build`
packs both, but for the **build host's** platform and image store. A bundle
built on the Mac holds macOS wheels and fails on a Spark. Build it **on a Spark
while it is online** (aarch64, the same overlay2 store) and keep it:

```bash
./scripts/bundle.sh build ~/openbeast-bundle      # on spark-1, online
./scripts/bundle.sh sign  ~/openbeast-bundle --key ~/.ssh/openbeast-bundle   # optional; prints the allowed-signers line
./scripts/bundle.sh verify  ~/openbeast-bundle [--key ~/.config/openbeast/allowed-signers]   # on the closed Spark
./scripts/bundle.sh install ~/openbeast-bundle [--key …]                                     # same commit as the build
echo 'OFFLINE=true' >> ~/openbeast/openbeast.conf
./bootstrap.sh --no-start && ./scripts/doctor.sh  # doctor says "serves offline" vs "can be maintained offline"
```

Off a Spark, only the wheels can be cross-built:

1. Run `./scripts/pydeps.sh wheelhouse wheels-spark` with the manylinux aarch64
   flags it prints. One `--platform manylinux_2_39_aarch64` alone misses cffi.
2. On the Spark, run `./scripts/pydeps.sh install --from wheels-spark`.

The lock never travels with the wheels. `install --from` accepts only the
checkout's committed lock, or one vouched for with `--lock-sha256`.

The compose images still have to come from a Spark-side `docker save`. They are
multi-arch at the pinned digests, so a connected Spark pulls the arm64 ones.
`bundle.sh install` rewrites the compose references to the loaded content IDs
and keeps a `.pre-bundle` copy.

`bootstrap.sh`'s OFFLINE message ("bundle.sh build on a connected box") does
not say the box must be aarch64. That is a known issue below.

### H7. Test and language toolchains

**pytest.** It is not in `agents/requirements.lock`, and neither spark-1's
`python3` (3.12.3) nor the Mac's default `python3` has it. The hash-pinned CI
closure is `.github/ci-requirements/test.txt`. This recipe was verified on the
Mac with python 3.12 on 2026-11-15 (95 tests passed). On a Spark it is the same
commands with `/usr/bin/python3`.

```bash
python3.12 -m venv ~/.venvs/openbeast-test
~/.venvs/openbeast-test/bin/python -m pip install -q --require-hashes -r ~/openbeast/agents/requirements.lock
~/.venvs/openbeast-test/bin/python -m pip install -q --require-hashes --no-deps -r ~/openbeast/.github/ci-requirements/test.txt
# offline: fill a wheelhouse online with the same files, then install with --no-index --find-links DIR
python3 -m pip download --require-hashes --no-deps -r .github/ci-requirements/test.txt -d wheels-test   # VERIFY ON HARDWARE for aarch64
```

**zig 0.16.** Nothing in the stack downloads a zig *compiler*. For
`language_reference` and the zig eval variants on a Spark, install it by hand,
pinned like CI's: `zig-aarch64-linux-0.16.0.tar.xz`, sha256
`ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17` (ziglang.org
index, read 2026-11-13). `lang-library.sh acquire zig` fetches the x86_64
tarball on any host, and that is fine: it is read as a std-source corpus, never
executed. No zig is installed on either Spark today (checked 2026-11-16).
The simplest pinned route, online or offline, is beast-ark
([H8a](#h8a-beast-ark-the-rest-of-the-software-on-a-drive)): its zig 0.16.0
entry carries that same sha256 plus the minisign signature, and
`ark restore zig --from DRIVE` puts it in `~/.local/opt/zig-0.16.0` with a
link in `~/.local/bin`. By hand, online:

```bash
cd /tmp && curl -fsSLO https://ziglang.org/download/0.16.0/zig-aarch64-linux-0.16.0.tar.xz
echo 'ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17  zig-aarch64-linux-0.16.0.tar.xz' | sha256sum -c -
mkdir -p ~/.local/opt ~/.local/bin && tar -xJf zig-aarch64-linux-0.16.0.tar.xz -C ~/.local/opt
ln -sfn ~/.local/opt/zig-aarch64-linux-0.16.0/zig ~/.local/bin/zig && zig version   # 0.16.0
```

**Rust, Go, clang.** beast-ark carries them pinned and checked: `go` 1.27.4,
`rust` 1.100.0 (`install = "manual"`: `restore` prints the installer path,
[BEAST_ARK.md](BEAST_ARK.md) §8.2) and `llvm` 23.1.5 (clang, lld). Restore
them from a drive the same way, online or offline. None is installed on
either Spark today: they have gcc / g++ 13.3 only. A Spark needs them only
for `language_reference` on those languages or to run eval validators
locally; the harness host needs them for every run
([F6](#f6-the-eval-harness-host-benchmarking-the-pair)).

**The language library** (`scripts/lang-library.sh acquire | check | list |
verify`) lives outside git. Acquire it online and copy `lang-library.sh where`'s
directory.

### H8. beast-docs mirror

```bash
./scripts/docs.sh sync swiftui --max-pages 300 --accept-terms   # online box
./scripts/docs.sh pack mirror.tar.gz                             # writes mirror.tar.gz + mirror.tar.gz.sha256
scp mirror.tar.gz mirror.tar.gz.sha256 spark-1.tail4109f9.ts.net:
ssh spark-1.tail4109f9.ts.net 'cd ~/openbeast && ./scripts/docs.sh unpack ~/mirror.tar.gz && ./scripts/docs.sh status'
```

`unpack` checks the tarball's hash first. It refuses absolute paths, `..`,
links and devices. It stages, requires the member set to equal `BUNDLE.json`,
re-hashes every file, and only then swaps each source in and reindexes. The
tarball hash proves integrity, not authenticity; sign the `.sha256` the way
`bundle.sh sign` does if you need authenticity.

### H8a. beast-ark: the rest of the software, on a drive

H2-H8 carry OpenBeast itself. Building or testing a larger system next to
the pair needs much more software. beast-ark
([BEAST_ARK.md](BEAST_ARK.md)) holds it: 112 open-source packages, each
pinned to a commit, with a sha256 or digest for every file and a build for
linux-aarch64. That includes Kubernetes and k3s with their images,
containerd, Docker, Go, Rust, Zig 0.16, Node, Temurin, Postgres, NATS,
Prometheus and Grafana, headscale, Forgejo, step-ca, Trivy with its DB, and
DevDocs. Fill a drive on any connected box, then restore on the Spark with
no network:

```bash
./scripts/ark.sh fetch --to /Volumes/ARK --profile core --platform linux-aarch64   # ~5.4 GiB + source
# plug the drive into the Spark (or rsync it over the cable)
/media/max/ARK/ark verify /media/max/ARK                       # re-hash; signatures re-checked
/media/max/ARK/ark restore go --from /media/max/ARK            # → ~/.local/opt/go-1.27.4, ~/.local/bin/go
/media/max/ARK/ark restore k3s --from /media/max/ARK           # + the air-gap image bundle (BEAST_ARK §8.4)
/media/max/ARK/ark serve /media/max/ARK --tailnet              # registry + pip/npm/go mirrors for the other boxes
```

The drive runs its own copy of the tool (`DRIVE/ark`), so it needs no
checkout. The Spark's python 3.12 is enough. On a box with no python, the
wrapper unpacks the CPython archived on the drive after checking it against
`SHA256SUMS`. Verified on spark-2 on 2026-11-15 (Zig 0.16.0 with its
minisign signature, and jq: fetched, verified, restored, run). `fetch`,
`lock` and `update` exit 3 under `OFFLINE=true` or island mode. The
TensorFold and llama.cpp pins in the ark are the repo's own (H5, H6), held
equal by tests.

### H9. The order for a pair built entirely offline

This order has not been run end to end. Every piece is measured on its own, and
the sequence is VERIFY ON HARDWARE.

1. Cable, NVIDIA Sync addressing (A2, B1). The repo from a bundle (H2).
2. `spark-root-setup.sh spark-N --debs debs --guard` on each Spark (H3).
   Optionally `spark-fabric-mtu.sh --stage` (B4, fully local). Reboot once.
3. Weights: copy, `model-fetch --verify`, HF-cache hard links, rsync to workers,
   verify there (C1-C3). DFlash2 plus the blob loop (C4).
4. Recipe checkout, `local.sh` (D1), image via `docker load` with the ID
   checked (H4). Unit installed with `PREPARE=0`.
5. Native TensorFold, optional (H5).
6. The command center from a Spark-built bundle with `OFFLINE=true` (H6),
   `openbeast.conf` (E2), units (E3), reboot.
7. Optional: pytest venv, zig, beast-docs mirror (H7, H8). If native
   TensorFold wrote `spark.env` in step 5, re-run `sudo spark-guard.sh` on
   that Spark (B3).
8. Optional: everything else from a beast-ark drive (H8a). `ark verify`
   first, then `ark restore <name>` per tool, or `ark serve --tailnet` for
   the whole fleet.

The same order with every command, per host, online and offline side by side:
[Install everything, fresh](#install-everything-fresh).

---

## Reference

### Serving configuration (measured 2026-11-13)

DFlash2, 4 parallel streams, 1M-token window, fp8 KV (pool ~2.2-2.6M tokens at
the time; `KV_POOL_GIB=10` since 2026-11-15), q4 dense, thinking off by default
(Ablit), RoCE all-gathers on both rails at MTU 1500. These numbers predate two
changes of 2026-11-17: thinking on by default for every client (E2a; it
lengthens the wait for the answer, not the decode rate) and MTU 9000 on the
fabric (B4; not re-measured).

| | 1 stream | 4 streams aggregate (each) | TTFT |
|---|---|---|---|
| prose | 43-44 tok/s | 145 tok/s (37) | ~160-250 ms |
| code | 68-70 tok/s | 224 tok/s (57) | ~170-260 ms |

With MTP instead (`DRAFTER=mtp`): 44 / 56 tok/s single, but **one request at a
time**; 4 queued requests wait 16-20 s. DFlash2 wins and is kept. Not yet
tried: `PARALLEL=8`, which needs a larger memory reserve (G4).

### Parity with the beast rig (verified 2026-11-15)

`openbeast.conf` on spark-1 enables what the rig ran in production (E2).

| Check | Result |
|---|---|
| `scripts/backends/conformance.sh --model GLM-5.3-Flash-EXL3 --heavy` | PASS: chat, stream, tools, tool_result, 2 parallel calls, max_tokens |
| `./agent.sh` (create / edit / run a file) | done in 5 iterations, 0 API errors |
| `./agent.sh` on a real repo fix, network tools disabled (SELF_DEVELOPMENT.md "Proof") | correct 2-file fix, test green, in 25 iterations / 68 s; hit the iteration cap before `task_done` |
| a WebUI chat that calls `bash` through the `:3001` tool server | executed server-side, answered `aarch64` / `20` |
| remote: every surface in E6 from off-LAN (phone hotspot) | 200; `:8443` 401 without a key, chat answers with one |
| `./scripts/doctor.sh` | 39 ok, 1 warning (SSD wear needs a sudoers rule) |

### Scaling: 1 to 8 Sparks

Scripts and their headers point here by this name. The content lives in the
linear sections:

- cabling and addressing per node count: [A2](#a2-cabling-and-addressing-for-1-2-3-and-4-8-sparks)
- root setup per node: [B2](#b2-spark-root-setupsh-the-only-root-step-once-per-spark), [B3](#b3-firewall-spark-guard)
- weights to every node: [C3](#c3-carry-the-weights-to-every-worker)
- `cluster.env`, `spark.env`, `spark-cluster.sh`, and which engine scales how far: [D2](#d2-native-tensorfold-through-spark-nodesh--spark-clustersh)
- the offline image pins: [H4](#h4-container-images)

### Offline staging

This is the old name for the staging steps, which the unit file's header still
uses. They are now:

- A, the .debs: [H3](#h3-debs-libnccl2-and-tailscale)
- B, the weights in the HF-cache layout: [C2](#c2-hard-link-into-the-hf-cache-layout-the-recipe-reads), [C3](#c3-carry-the-weights-to-every-worker)
- C, DFlash2: [C4](#c4-the-drafter-dflash2)
- D, the recipe image: [H4](#h4-container-images)
- E, native TensorFold: [H5](#h5-native-tensorfold-and-the-engine-plane)
- F, the command center: [H6](#h6-the-command-center-wheels-and-compose-images)

### Rebuild from scratch (short form)

1. Cable the CX-7 ports; NVIDIA Sync addresses both rails (A2, B1).
2. On each Spark: `sudo ~/openbeast/scripts/backends/spark-root-setup.sh spark-N --guard`
   (B2, B3). Offline: `--debs DIR` (H3). `sudo …/spark-fabric-mtu.sh --stage`
   (B4). Reboot once.
3. Weights on spark-1, `model-fetch.sh --profile glm53-flash-ablit-exl3-tensorfold --verify`,
   hard-link into the HF-cache layout, copy both to spark-2 with `-H` (C). The
   recipe never looks at `~/openbeast-models`.
4. Recipe to `~/openbeast-recipes/glm53`, `scripts/local.sh` as in D1,
   `scripts/prepare.sh` online. Offline: stage the image and DFlash2 (H4, C4)
   and leave `PREPARE=0`, as the unit does. Install the unit.
5. Native TensorFold, optional (D2, H5).
6. `~/openbeast`: `openbeast.conf` (E2), `./bootstrap.sh --no-start`, install
   both units (E3), reboot once, WebUI admin (E5), `setup-tailscale.sh` (E6),
   device keys (E7). Offline: H6.
7. The Mac client (F), then optional zig / docs mirror / ark / harness host.
   The long form: [Install everything, fresh](#install-everything-fresh).

### Known issues and errata (v1.9.0)

These are corrections to `RELEASE_NOTES_v1.9.0.md` and to this page as first
published, from the 2026-11-15 review:

- **ufw was never active.** The release notes and this page said ufw was
  "active by default", and that the root setup's rules were what let the ranks
  talk while keeping the LAN and tailnet out. ufw is installed but disabled on
  both Sparks, so the rules filtered nothing, and the unauthenticated
  rendezvous (29551) was reachable from the tailnet. Fixed by `spark-guard.sh`
  (B3), which runs on both Sparks: `active` on 2026-11-15, and 29551 closed
  from the Mac.
- **sshd accepts passwords** on both Sparks, and `max` is in `sudo` and
  `docker`. Hardening is opt-in: `spark-guard.sh --harden-ssh`.
- **Offline images for `spark-node.sh`**: pin `*_IMAGE_ID`, not a digest (H4).
  The container TensorFold engine needs GitHub and PyPI at every start.
- **The served snapshot is not what the lock names.** The recipe serves the
  HF-cache snapshot; the lock covers `~/openbeast-models/...`. They are the
  same bytes only while hard-linked (C2). DFlash2 is outside every lock (C4).
- **Plaintext device keys on spark-1** (E7): delete them once the devices hold
  their keys. The same goes for `webui-max-password` once it is changed (E5).
- **The recipe container runs with `--network host`**, as root, from
  third-party alpha code. Anything in it can reach `127.0.0.1:8000` (no key, no
  gate audit) and the loopback-trusting beast-chat / beast-artifact ports
  (`OPENBEAST_CHAT_LOGIN_FROM=loopback` trusts any local TCP peer's
  `Tailscale-User-Login`). **beast-chat: fixed on the pair (2026-11-15)** with
  `CHAT_LOGIN_FROM=unix` (E2), so the header counts only on the 0600 socket
  tailscaled proxies to. beast-artifact has the same switch since review
  2026-11-16 (`ARTIFACT_LOGIN_FROM=unix`, docs/BEAST_ARTIFACT.md "Where the
  login header counts"), but the pair still runs it in `loopback` until the
  operator sets it, restarts and re-runs `--publish-artifact`. Still open:
  that switch on the pair, and the recipe's network mode.
- **`bootstrap.sh` OFFLINE hint** points at `bundle.sh build` "on a connected
  box" without saying it must be an aarch64 Spark (H6).
- ~~**`setup-client.sh` writes the opencode v1 MCP shape**~~ Fixed: it writes
  `mcp.servers.<name>` on opencode v2 and moves other v1 entries in (F3;
  verified on the Mac 2026-11-17).
- **Checkout skew**: on 2026-11-16 evening spark-1 was at `29e5241a` and
  spark-2 at `de931dfe` (0). Fixed by 2026-11-17: both at `d3727185`.
  [G6](#g6-updating) step 4 pulls both.

From the 2026-11-16 review (fixes in this checkout; the live boxes pick them
up on the operator steps named):

- **Loopback is not isolation on spark-1.** Open WebUI and SearXNG also run
  with `network_mode: host` (`docker-compose.yml`), and SearXNG runs as uid 0
  while it parses untrusted web content. From inside either container,
  `127.0.0.1:8000` answers without a key, gate caps or the inference audit
  (checked read-only: `docker exec searxng wget -qO- http://127.0.0.1:8000/health`
  returns TensorFold's health), and rank 0's unauthenticated TCPStore on
  `*:29551` is reachable on loopback too. The agent `bash` tool has the same
  reach, since Sandlock leaves the network open by default
  ([SANDBOXING.md](SANDBOXING.md)). spark-guard cannot help: it must accept
  `lo` (Open WebUI and the agents reach inference there by design), and an
  nftables `meta skuid` rule would not separate the containers either, because
  Open WebUI, SearXNG and the recipe all run as uid 0. A compromised SearXNG
  could therefore run unaudited generations, starve the 4 streams, or disturb
  the TCPStore during a restart. It cannot read data or escalate. The real
  fixes are still open: serve with native TensorFold (D2) and its
  `--api-key-file`, so loopback reach alone grants nothing on `/v1`, and move
  SearXNG to a bridge network that reaches only what it needs.
- **spark-guard rules are stale on both Sparks**: installed 2026-11-15, they
  guard `29501` but not `29502` (B3). `--status` now says `STALE`. Fix: one
  `sudo scripts/backends/spark-guard.sh` on each Spark.
- **beast-gate admitted 2 generations per device** while the pair serves 4.
  The gate now defaults to `INFERENCE_SLOTS` (E2). Live on spark-1 since the
  2026-11-16 restart (`/api/slot`: `per_device_inflight: 4`).
- **spark-2's spark.env names a TensorFold key file that is not there**
  (`TENSORFOLD_API_KEY_FILE`; spark-2's `~/.config/openbeast` holds only
  `hf-token`). It matters only for a native TensorFold launch (D2): write the
  0600 key file on spark-2 first.
- ~~**The RoCE fabric runs at `active_mtu 1024`** (netdev MTU 1500).~~
  **Fixed 2026-11-17:** MTU 9000 staged on each Spark, both rebooted, and
  `--verify` passed on both (`active_mtu 4096`, 8972-byte don't-fragment
  pings on both rails). Still open: the performance A/B. Runbook, rollback
  and measurement plan: [B4](#b4-fabric-mtu-9000-spark-fabric-mtu).

### Not done yet

- zig for `language_reference` on a Spark (H7), and a beast-docs mirror on spark-1 (F5).
- The fabric MTU A/B (B4): MTU 9000 is live since 2026-11-17; its speed
  effect on prefill and decode is unmeasured.
- A `svc:spark-hydra` Tailscale Service name (an admin-console step), and the
  beast rig's hydra node for the pair (`https://spark-1.tail4109f9.ts.net:8443`,
  `engine = "openai"`, a gate device key, `slots = 4`, upstream
  `GLM-5.3-Flash-EXL3`), once the rig is back on. `hydra.toml.example`'s
  `[nodes.sparks]` is already that shape.
- Switching the primary between `beast` and `spark-hydra` on the hydra
  side. Clients switch since #204 (`openbeast-client use <rig>`, F2).
- A `PARALLEL=8` measurement; native TensorFold 1.2.7 (D2) vs the recipe.
- A fully offline rebuild of the pair, end to end (H9).
- `tests/test_scripts.sh` showed 7 failures when run from a non-git copy on
  spark-2 (`test_uninstall.sh` among them). Rerun from a git checkout before
  merging.
