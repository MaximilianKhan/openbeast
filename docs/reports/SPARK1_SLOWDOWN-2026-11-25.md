# spark-1 slowdown, 2026-11-25: a kernel oops in `mstflint_access`, an hourly NVIDIA OTA check, and a prefill-bound job

On 2026-11-25 at about 16:45 PST, Max reported that DeepSeek on the Sparks
was "grinding at a 6 tok/s", and that spark-1's available memory had slid
from about 4.6 GB to about 2.6 GB. Max was running a large parallel job
against the pair at the time. The investigation ran from 16:50 to 17:30
PST, read-only on the Sparks (no sudo). Max rebooted spark-1 at about 17:08,
with the `mstflint_access` blacklist below already in place. Spark-1 came
back at 17:16:52, and DeepSeek was serving again by 17:19.

**Verdicts.**

1. **The 6 tok/s was Max's workload, not a fault.** The job sends prompts of
   47K to 92K tokens that share no prefix with earlier ones (cached=0 on 298
   of the 335 requests since 14:00), and each asks for only about 1,000
   output tokens. In a 120 s sample at 17:04, the engine spent 116 s
   prefilling at 1,766 tok/s and generated 7.4 tok/s in aggregate, with 4
   requests running and 3 waiting. Long prefills block decode rounds, as the
   [2026-11-23 tuning report](DEEPSEEK_TUNING-2026-11-23.md) found: a stream
   that is already decoding only gets rounds between prompt chunks. The
   pair's output was prompt processing, which the generation tok/s number
   does not show. The baseline is 30.8 tok/s per stream at 4 streams and
   19.9 at 8, measured with short prompts.
2. **There was one fault: a kernel oops in `mstflint_access`, triggered by
   NVIDIA's own hourly OTA check.** It left 1,640 `mstflint` processes stuck
   in uninterruptible sleep (D state) and drove the load average to about
   1,642. The stuck processes used no CPU (85% idle, all pressure counters
   zero), and they did not slow the engine. They cost about 0.3 GB of
   kernel memory and would have kept growing by 40 processes an hour.
3. **The memory slide is mostly user-space growth, not the oops.** Open
   WebUI is the largest part, at about 44 MiB/h. The stuck processes add
   about 7 MiB/h of kernel memory. Details are in [Memory](#memory).
4. **The temperatures are within the range NVIDIA and other owners report,
   and nothing throttled.** Peaks were 86-88 °C GPU and 89.5-93.5 °C on the
   ACPI zones, the same every day since 2026-11-22, so they have not risen
   since the rack move. There is not much margin on the zone sensors; see
   [Temperatures](#temperatures).
5. **The spawner is stopped, and the fix is permanent on spark-1:**
   `mstflint_access` is blacklisted. Spark-2 has the same software and the
   module still loaded, so it needs the same two lines ([Commands for
   Max](#commands-for-max)).

## Timeline (PST)

| When | What |
|---|---|
| 2026-11-23 23:09:45 | spark-1 boots (kernel 7.0.0-1025-nvidia). `dgx-dashboard-admin` (root) starts at 23:09:53 and runs `refreshUpdatesCache` once. That run finishes in about 2 minutes: `mstflint` still works. |
| 2026-11-24 00:09 | The first hourly `refreshUpdatesCache`. It runs `/opt/nvidia/spark-ota-check/check_ota_status.py`, which calls `mstflint -d 0000:01:00.0 q` once for each of 13 OTA recipes (`check_firmware.py` `check_connectx`). |
| 2026-11-24 00:10:15-16 | `mstflint` PID 169168 hits a kernel oops in `mst_ioctl+0x988/0x1cd8 [mstflint_access]`: "Unable to handle kernel paging request at virtual address 48b02498128c08dd", level 0 translation fault. The kernel is now tainted D. The dead process still holds the device lock. |
| 2026-11-24 00:10:50 onward | Every later `mstflint` on that device blocks in D state. `run_cmd` (spark-ota-check `utils.py`) waits 30 s, sends SIGKILL to the process group (which a D-state process ignores), waits 5 s and gives up. That makes one stuck process every 35 s, 40 per hourly run, from :10 to about :33. The Python script then exits, and the stuck process is reparented to PID 1. |
| 2026-11-24 to 11-25, every hour | The same 40 processes each hour: 41 runs × 40 = 1,640. The last one started at 16:33:49. The admin log shows each run taking about 24 minutes instead of 2, ending "ota-versions failed: signal: killed". |
| 2026-11-25 ~14:27 | Max's job starts (short prompts at first, then 47K-92K tokens). The prompt cache stops producing hits (1,118 hits, then flat), because the prompts share no prefix. |
| 2026-11-25 16:45-17:05 | Max reports the slowdown. Investigation (this report). |
| 2026-11-25 ~17:08 | Max writes `/etc/modprobe.d/openbeast-blacklist-mstflint.conf` and reboots spark-1. The shutdown takes about 8 minutes, most likely waiting on the D-state processes. |
| 2026-11-25 17:16:52 | spark-1 is up. `mstflint_access` is not loaded, taint is 4096 (O only: out-of-tree modules, as always), there are 0 `mstflint` processes, and the load average is 2.6. The boot-time OTA refresh completed in about 1 minute and still reported "no new OTA". |
| 2026-11-25 17:19 | DeepSeek's `/health` returns ok (the unit is enabled at boot). |

## Evidence

### The spawner: `dgx-dashboard-admin` → `spark-ota-check` → `mstflint`

- **The cadence.** `ps -eo lstart` on the 1,640 stuck processes gives
  exactly 40 per clock hour, from 2026-11-24 00h through 2026-11-25 16h.
  Within each hour they start at :10 and continue every 35-36 s until about
  :33. Neither the openbeast repo, the user crontab, nor the user timers
  run `mstflint`. No system timer fires at :09-:10. `dgx-dashboard-admin`,
  however, started at 23:09:53, so its hourly cycle lands at :09.
- **The log.** `/var/log/dgx-dashboard-admin.log.1` (world-readable) has
  `refreshUpdatesCache: starting` at :09 every hour. On spark-1 each run
  completed at about :33, and the last one at 16:34 says
  `Failed to get OTA components for ordinary package exclusion
  error="/opt/nvidia/spark-ota-check/check_ota_status.py ota-versions failed:
  signal: killed"`. Spark-2's log has the same hourly runs, each finished in
  2 minutes (:09 to :11).
- **The code.** `/opt/nvidia/spark-ota-check/check_firmware.py:188`:
  `mstflint_out = run_cmd(["mstflint", "-d", pci_addr, "q"])`, where
  `pci_addr` is the first "Ethernet controller: Mellanox" in `lspci`, so
  0000:01:00.0. `utils.py` `run_cmd` uses timeout=30, SIGKILL to the process
  group, then `communicate(timeout=5)`: the 35 s step. The comment above
  `check_connectx` says "Not failing if the ConnectX is not found", so an
  `mstflint` that fails fast only removes the ConnectX row from the OTA
  score.
- **Package provenance.** The module is
  `/lib/modules/7.0.0-1025-nvidia/kernel/mstflint/mstflint_access.ko.zst`,
  from `linux-modules-7.0.0-1025-nvidia` (the kernel package; `modinfo`
  says version 2.0.0). It is loaded at boot by `nvidia-mstflint-loader`
  23.03-1, which ships only `/etc/modules-load.d/nvidia-mstflint.conf`
  (`mstflint-access`). `mstflint` 4.26.0+1-2ubuntu3 is the newest in the
  archive, and `mstflint-dkms` is not installed. No newer kernel was
  offered (`apt-cache policy`: Installed = Candidate = 7.0.0-1025.26~24.04.1),
  so no update fixes this today.
- **The other candidates are ruled out.** `nvidia-dgx-telemetry`,
  `nvidia-dgx-sol` and `nvidia-spark-mlnx-firmware-manager` are disabled
  units. `fwupd-refresh.timer` fires at :04 on a different cadence, and the
  admin log shows fwupd's refresh succeeding separately.

### The fault: the oops

`journalctl -k -b -1`: `Internal error: Oops: 0000000096000004 [#1] SMP`,
`CPU: 9 UID: 0 PID: 169168 Comm: mstflint Tainted: G O`,
`pc : mst_ioctl+0x988/0x1cd8 [mstflint_access]`, and a non-canonical
pointer in x23 (`6d8dd49d99251140`). The ioctl died holding the module's
per-device mutex, and every later `mstflint` sleeps on that mutex
uninterruptibly. Only a reboot frees them. Spark-2 runs the same module
under the same hourly check and has never oopsed. The trigger is
intermittent, so spark-2 is at the same risk.

### Speed: load, not the fault

| Measure (spark-1, 17:02-17:04) | Value |
|---|---|
| `tensorfold:requests_running` / `waiting` | 4 / 3 (`PARALLEL=8`; the gate admits up to 8) |
| generation over 120 s | 884 tokens, **7.4 tok/s aggregate** |
| prompt tokens over 120 s | 211,951, **1,766 tok/s**, 0 cached |
| prefill time over 120 s | 116.3 s of request prefill (`request_prefill_seconds_sum`) |
| decode rounds | 2.38 per s; spec acceptance 598 / 958 = 62% |
| Max's requests since 16:00 | 112 finished, mean prompt 59,380 tokens, mean output 995 tokens |
| Lifetime since 11-24 08:50 | 303 M prompt tokens (280 M cached), 1.49 M generated, TTFT mean 34 s |
| CPU, pressure | 85% idle; PSI memory / io / cpu all 0.00 |
| Disk reads | about 400 MB/s, in 4 KiB reads, on **both** Sparks. Both read about 5.1 TB since boot, and the same rate shows up in the 2026-11-23 tuning session (sar `-b`), so this is how the engine normally runs under load, not a symptom. The engine had 2 major faults in its lifetime: these are explicit reads, not page-cache thrash. |
| Fabric | spark-1 `rocep1s0f0` and spark-2 `rocep1s0f1` ACTIVE, 200 Gb/s (4X HDR), MTU 9000. spark-2's RoCE error counters (out_of_sequence, packet_seq_err, local_ack_timeout_err, rnr_nak_retry_err, cnp) are all 0. |
| GPU at 16:50 | spark-1 81 °C, 74.6 W, 96% util, 2,372 MHz; spark-2 86 °C, 78 W. No throttle reasons. |

The direct probe Max's report started from (120 tokens in 118 s while 4
requests ran) is what a decode stream gets when 60K-token prefills take most
of the engine's time. The tuning report measured 116.5 tok/s aggregate and
30.8 per stream at 4 streams with short prompts. A prefill-bound job cannot
reach that, and the generation tok/s figure undercounts its real output (the
prompt tok/s). There is nothing to fix in the engine for this. To get the
job through faster, its prompts should share a prefix (the fixed
instructions first, so the prompt cache hits) or be shorter. Adding streams
does not help, because prefills run one at a time.

A side note for TensorFold: since 14:24 its prompt-cache line has counted
`failed` going up with every request (8 → 275), while `misses` moved less
(21 → 103). The cache is full (503 of 512 MiB, 0 spare, 848 evicted), and
the new prompts share no prefix, so no hits are expected. What `failed`
counts (most likely a state it could not keep) is not documented. It is
logged in [TODO](../TODO.md) to check with the next TensorFold review, and
it is not a cause of this slowdown.

## Memory

`sar -r ALL` on spark-1, in kB (memory available, anon pages, slab, kernel
stacks, page tables):

| Window | kbavail | Anon | Slab | KStack | PgTbl |
|---|---:|---:|---:|---:|---:|
| 11-24 01:30, engine down | 121,578,992 | 829,740 | 697,060 | 16,736 | 31,072 |
| 11-24 08:30, engine down | 121,251,468 | 1,117,796 | 723,460 | 21,296 | 51,200 |
| 11-24 09:30, engine up | 5,154,100 | 3,736,504 | 1,558,972 | 25,792 | 68,816 |
| 11-25 16:30 | 2,867,372 | 5,281,740 | 1,695,252 | 46,896 | 159,256 |
| 11-25 17:19, after the reboot | (6.3 GiB) | | SUnreclaim 1.25 GB | 18,032 | 33,756 |

- **The engine was down from about 01:00 to 08:50 on 11-24**, which gives a
  clean window to measure the leak. Anon grew 41 MB/h, slab 3.8, kernel
  stacks 0.65 and page tables 2.9: about 49 MB/h in total.
- **The stuck `mstflint` processes cost about 7 MB/h** (slab + stacks +
  page tables), or about 180 KB each. Each one holds 8 kB of anon memory and
  3.9 MB of shared library pages (RssAnon 8 kB, RssFile 3,940 kB, VmPTE
  68 kB). Adding up their RSS (about 7 GB) counts the shared pages 1,640
  times, so it overstates the cost. Their real total was about 0.3 GB at
  1,640 processes.
- **Anon growth is user space**: the per-process RssAnon totals match
  `AnonPages` exactly (4,744,900 vs 4,743,072 kB). Open WebUI went from 0 at
  its restart (11-25 01:42) to 682 MB of anon memory by 17:03, about
  44 MB/h, which matches the 41 MB/h slope on its own. TensorFold rank 0
  held 1.95 GB of anon memory (host-side, apart from the GPU allocations).
- **From 11-24 09:30 to 11-25 16:30, available memory fell 2.18 GiB.**
  Anon grew 1.55 GB, page cache shrank 2.66 GB (it was the reclaimable
  buffer that absorbed the growth), and kernel memory grew 0.25 GB. On
  GB10, the model's 104 GiB per rank are driver allocations outside these
  categories, so what is left for everything else is the 4-6 GiB above
  memguard's 2.5 GiB floor.
- **Does low available memory slow the engine?** Not here: memory pressure
  was 0.00 and the engine had no page faults. But at 2.6 GiB, spark-1 was
  0.1 GiB from memguard's floor, and the healthcheck had been warning
  "under the 3 GiB reserve" since 14:21. At that rate, memguard would have
  killed a rank within about a day. The fix is to restart or slim the
  command center's heaviest services on spark-1 (the WebUI first;
  [DeepSeek tuning, moving the WebUI](DEEPSEEK_TUNING-2026-11-23.md#moving-the-open-webui-off-the-head-node)),
  not anything in the engine.

## Temperatures

Readings: spark-1 GPU 81 °C at 96% util and 74.6 W, ACPI zone maximum
89.5 °C; spark-2 GPU 86 °C at 78 W, zone 93.5 °C (16:50). The dashboard's
7-day history (15-minute slots, `dashboard-history.json` on spark-1) gives
the daily peak and the mean of busy slots (GPU util over 50%):

| Day | spark-1 peak / busy mean | spark-2 peak / busy mean |
|---|---|---|
| 11-22 | 87 / 79.0 | 84 / 77.3 |
| 11-23 | 86 / 73.3 | 88 / 74.4 |
| 11-24 | 86 / 72.1 | 86 / 72.0 |
| 11-25 | 86 / 77.4 | 87 / 77.7 |

- **No throttling.** `nvidia-smi -q -d PERFORMANCE` shows no slowdown reason
  active, and the cumulative "SW/HW Thermal Slowdown" counters are 0 us.
- **There is no published limit.** NVIDIA publishes no GB10 throttle or
  shutdown temperature, only an ambient range of 5-30 °C. Forum reports
  put the SoC at 92-97 °C and the GPU at 78-83 °C under load, and an NVIDIA
  representative called the mid-80s within range. Some owners report
  firmware thermal power-offs at zone temperatures of about 90-97 °C.
- **Verdict: within range, no rise since the rack move, but little
  margin.** The GPU peaks of 86-88 °C are unchanged day to day. Spark-2's
  zone reading of 93.5 °C is inside the band where some owners report
  power-offs. Keep the rack's intake below about 25 °C, and keep the front
  intake and rear exhaust clear: nothing in front of the intake, and no
  other device's exhaust feeding it. The fan RPM files exist
  (`/sys/class/hwmon/hwmon{0,1}/fan1_input`) and are worth adding to the
  dashboard next. The dashboard warns at 85 °C GPU and goes critical at
  92 °C (`DASHBOARD_ALERT_GPU_TEMP_C` / `_CRIT_C`).

## What changed

- **spark-1 (Max, sudo, before the 17:08 reboot):**
  `/etc/modprobe.d/openbeast-blacklist-mstflint.conf`. After the reboot,
  `lsmod` shows no `mstflint_access`, there are 0 `mstflint` processes, and
  `systemd-modules-load` is `active (exited)` without errors (a blacklisted
  module is skipped quietly). `dgx-dashboard-admin`'s boot refresh completed
  in about 1 minute and still reported "no new OTA".
- **`scripts/backends/spark-root-setup.sh` step 5/5** writes the same
  blacklist and unloads the module when nothing holds it, so a fresh
  install, or a re-run on a built Spark, gets it
  ([SPARK_HYDRA.md G9](../SPARK_HYDRA.md#g9-mstflint_access-is-blacklisted-on-both-sparks)).
- **Dashboard (PR, this report's branch).** It now shows **single-stream vs
  multi-stream throughput** for every engine (see below), and it raises the
  alerts this incident needed: D-state processes, a load anomaly, a kernel
  oops, a falling available-memory trend, and streams far below the model's
  baseline.

### Dashboard: per-stream vs aggregate tok/s

Max asked whether the dashboard shows single vs multi-stream tok/s. It did
not: it showed one engine-wide "tokens / s" (the aggregate) and, on the
Serving page, a "decode per request" from finished requests over 5 minutes.
It now shows three things on the home tile, the Serving page and the
history chart:

- **tok/s · all streams**: the aggregate (the engine's live counter), plus
  the 60 s average.
- **tok/s · per stream**: the median decoding stream. Basalt and llama.cpp
  measure it per slot from `/slots` (each slot's `n_decoded` between two
  samples). TensorFold decodes in batched rounds, so every decoding stream
  advances once per round, and the aggregate over `/health`'s
  `streams.decoding` is each stream's rate. vLLM has no per-stream gauge,
  so it shows the aggregate over the running requests, labelled as a lower
  bound. With no stream decoding, it falls back to the finished requests'
  decode rate over 5 minutes, labelled as such.
- **streams**: decoding · prefilling of active.

The new alerts:

| Rule | Fires when | Default |
|---|---|---|
| `slow` | per-stream decode below `SLOW_STREAM_PCT` of the model's baseline at that stream count, for `SLOW_STREAM_S` | 35%, 120 s. Baselines are built in for DeepSeek-V4.1-Flash (tuning report F) and Hy4 (recipe), or set with `DASHBOARD_STREAM_BASELINE="1:68,4:31,8:20"` |
| `dstate` | at least `DSTATE_N` processes in D state (critical at `DSTATE_CRIT_N`), naming the most common command | 20 (200), 60 s |
| `load` | 1-minute load above `LOAD_PER_CPU` × CPUs | 8 |
| `kernel` | `/proc/sys/kernel/tainted` bit 7 (D: an oops or BUG since boot): critical, read `journalctl -k -b`, plan a reboot | — |
| `memtrend` | MemAvailable falling faster than `MEM_TREND_MIB_H` (least squares over 6 h) and reaching the memguard floor within `MEM_TREND_H` | 25 MiB/h, 72 h |

On 2026-11-25 the `kernel` alert would have fired at 00:10 on 11-24,
`dstate` at about 00:23 (20 stuck processes), `load` at about 04:00, and
`slow` from 14:30 on. That last one is a true reading: the streams were
slow, and its detail line shows the prefilling count next to it.

## Commands for Max

**Spark-2 (now; no reboot needed).** The module is loaded but unused
(refcount 0), so it can be unloaded on the spot. Avoid :09-:12 past the
hour, when the OTA check runs.

```sh
ssh -t spark-2 'printf "blacklist mstflint_access\ninstall mstflint_access /bin/false\n" | sudo tee /etc/modprobe.d/openbeast-blacklist-mstflint.conf >/dev/null && sudo modprobe -r mstflint_access; lsmod | grep -c mstflint_access'   # prints 0
```

**spark-1 is done** (the same file, written before the 17:08 reboot).

**Is the blacklist safe and sufficient?** Yes for everything we and NVIDIA
run day to day:

- Nothing in OpenBeast uses `mstflint` (`git grep` finds nothing outside
  the docs). RoCE, mlx5 and NCCL do not depend on the module (`modinfo`:
  no dependents; `lsmod` showed it used only by the stuck processes).
- NVIDIA's OTA check tolerates it: `check_connectx` returns no result when
  `mstflint` finds nothing ("Not failing if the ConnectX is not found"). The
  first refresh after the reboot finished in about 1 minute with the same
  verdict as before. The DGX Dashboard and NVIDIA Sync (ssh) need nothing
  more.
- **Without the module, `mstflint` does not hang another way.** Its other
  path is PCI config space through sysfs
  (`/sys/bus/pci/devices/…/config`), and Secure Boot is on with kernel
  lockdown at `integrity`. Lockdown refuses config-space writes from user
  space, so that path fails at once instead of hanging. Its in-band (MAD)
  path has its own timeouts and does not go through the module.
- **What it costs:** flashing ConnectX-7 firmware.
  `nvidia-spark-mlnx-firmware-manager` (a disabled unit) and an OTA that
  updates the CX-7 firmware need the module. Undo the blacklist for that
  update, then put it back.
- `install mstflint_access /bin/false` also blocks an explicit `modprobe`.
  `/etc/modules-load.d/nvidia-mstflint.conf` stays as it is: systemd applies
  the blacklist and skips the module without an error. The module is not
  in the initramfs, so no `update-initramfs` is needed.

**To undo it** (for a CX-7 firmware update), and then re-apply it:

```sh
sudo rm /etc/modprobe.d/openbeast-blacklist-mstflint.conf && sudo modprobe mstflint_access
# ... the firmware update ...
printf "blacklist mstflint_access\ninstall mstflint_access /bin/false\n" | sudo tee /etc/modprobe.d/openbeast-blacklist-mstflint.conf && sudo modprobe -r mstflint_access
```

**After any reboot, on each Spark:**

```sh
lsmod | grep -c mstflint_access          # 0
pgrep -c mstflint                        # 0
cat /proc/loadavg                        # load in single digits
cat /proc/sys/kernel/tainted             # 4096 (O); 128 set = an oops since boot
grep SUnreclaim /proc/meminfo            # ~1.1-1.3 GB on spark-1 after boot
systemctl is-failed systemd-modules-load # inactive or active: not failed
sed 's/\x1b\[[0-9;]*m//g' /var/log/dgx-dashboard-admin.log | grep refreshUpdatesCache | tail -2   # starting/completed within ~2 min
```

**Optional.** To stop the hourly OTA scan altogether: it also runs
`fwupdmgr refresh`, `lspci` and package checks every hour on a serving box.
`sudo systemctl disable --now dgx-dashboard-admin` does that, but it also
turns off the DGX Dashboard's admin features (updates, Wi-Fi assistant).
With the blacklist in place, this is no longer needed for the fault.

**Memory on spark-1.** If the slide comes back (the dashboard's new
`memtrend` alert will say so), restart the WebUI between jobs with
`systemctl --user restart openbeast`. That does not touch the model unit.
In the longer run, move the WebUI off the head node (the tuning report's
plan).

**The fabric after the reboot:** MTU 9000 and 200 Gb/s (4X HDR) on both
ends (spark-1 `enp1s0f0np0` / `rocep1s0f0`, spark-2 `enp1s0f1np1` /
`rocep1s0f1`; spark-2's f0 port has no cable and reads DOWN at 40 Gb/s,
which is expected).
