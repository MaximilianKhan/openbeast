# Downloading model weights from a Hugging Face URL

**One command, for every LLM and every person on this project.** Given a
Hugging Face URL, it downloads the model into one predictable folder,
verifies every file, and prints the folder. Use it instead of `hf download`,
`git clone`, `wget` or a hand-written script, so that each download lands in
the same place, in the same layout, with the same proof that it is intact.

```bash
scripts/weights-download.sh https://huggingface.co/ORG/REPO               # -> ~/Documents/weights/ORG/REPO
scripts/weights-download.sh https://huggingface.co/ORG/REPO /path/to/dir  # -> exactly /path/to/dir
```

It runs the same way on the Mac, on the rig and on either Spark, from the
repo root of any OpenBeast checkout. It needs only `python3` (3.9 or newer)
and the standard library, so there is nothing to install.

## Where it puts the weights

| You give | The model goes to |
|---|---|
| a URL only | `~/Documents/weights/ORG/REPO` (for example `~/Documents/weights/Qwen/Qwen3-0.6B`) |
| a URL and a folder | exactly that folder; it is the model's own folder, not a parent |
| `WEIGHTS_DOWNLOAD_ROOT=/mnt/ssd` and a URL | `/mnt/ssd/ORG/REPO` |

The last line of the output (stdout) is always the absolute folder, and
nothing else is printed on stdout. A script can therefore capture it:

```bash
dir="$(scripts/weights-download.sh https://huggingface.co/ORG/REPO)"
```

Progress and errors go to stderr. A line every 30 seconds shows the GB done,
so a large shard does not look like a hang.

## The URL forms it accepts

| URL | Downloads |
|---|---|
| `https://huggingface.co/ORG/REPO` | the whole repo at `main` |
| `https://huggingface.co/ORG/REPO/tree/REV` | the whole repo at a branch, tag or commit |
| `https://huggingface.co/ORG/REPO/tree/REV/SUBDIR` | only the files under `SUBDIR/` |
| `https://huggingface.co/ORG/REPO/blob/REV/FILE` (or `resolve/`) | one file, for example one GGUF |
| `ORG/REPO`, `hf.co/ORG/REPO` | the same as the first line |

Copy the URL from the browser as it is, `?download=true` included. Dataset
and Space URLs are refused, because they are not models.

## Options

```bash
scripts/weights-download.sh URL --dry-run                    # the file list, sizes and target folder; writes nothing
scripts/weights-download.sh URL --include '*.safetensors' --include '*.json'   # only matching files
scripts/weights-download.sh URL --exclude 'original/*'       # skip matching files
scripts/weights-download.sh URL --revision v1.0              # same as /tree/v1.0
scripts/weights-download.sh URL --jobs 8                     # files fetched at once (default 4)
scripts/weights-download.sh --verify ~/Documents/weights/ORG/REPO   # re-hash; offline, no network
scripts/weights-download.sh --help
```

A glob matches either the whole path in the repo or just the file name.

**For an LLM agent:** run `--dry-run` first when the size is unknown. A
model repo can hold several quantizations, several hundred GB in all. Pick the
files you need with `--include`, and say what you chose and why.

## What it guarantees

1. **One commit.** `main` (or a tag) is resolved to a commit SHA once, and
   every file is requested at that commit only. Files from two different
   commits never mix.
2. **Every file is verified.** Large files are checked against the Hub's LFS
   sha256, small ones against their git blob id. A file that fails is deleted
   and the run fails. A large file that the Hub gives no hash for is refused.
3. **Nothing half-done appears in the folder.** Files download into a hidden
   stage beside it (`.REPO.partial`) and move in only after every one has
   passed.
4. **Interrupted? Run the same command again.** It resumes from the stage,
   continuing each partial file where it stopped, and good files are not
   downloaded again.
5. **Already there? Run the same command again.** It re-verifies the files
   against the manifest and downloads nothing.
6. **Never overwrites.** A folder that holds another repo or commit, or a
   non-empty folder it did not create, is refused. Pick another folder or move
   the old one aside.
7. **A crawling connection is dropped and resumed.** A Hub connection can
   slow to ~1 MB/s and stay there for hours, while a fresh one is fast at
   once. A file that grows less than `FETCH_STALL_MB` (default 50) MB in
   `FETCH_STALL_SECS` (default 180) s, or whose connection sends nothing for
   `FETCH_READ_TIMEOUT` (default 120) s, is requested again from the byte it
   reached, up to `FETCH_STALL_RESTARTS` (default 5) times per file; each
   restart is logged (`stalled FILE: ...: reconnecting, restart 1 of 5`).
   After that the run fails and keeps the partial, so the same command
   resumes it later. `FETCH_STALL_SECS=0` turns the check off. The same
   watchdog runs in `model-fetch.sh`, which shares the download code.
8. **One-file URLs add up.** Several `blob/` URLs from the same repo and
   commit (the shards of a GGUF, for example) can target the same folder, and
   each run adds its file to it.

Each folder carries `.openbeast-weights.json`. It records the repo, the
commit, the URLs it came from, and every file's size and sha256. That manifest
is what `--verify` checks, on any machine, without a network.

Dotfiles in the repo (`.gitattributes`, `.github/`) are skipped. Every other
file is kept, whatever its format (safetensors, GGUF, EXL3, `.bin`).

## Gated and private repos

Accept the model's licence on its Hugging Face page first, then give a token
in one of these ways (checked in this order):

1. `HF_TOKEN` in the environment.
2. `HF_TOKEN_FILE=/path/to/file`; the file must be `chmod 600`.
3. `~/.cache/huggingface/token`, the file `hf auth login` writes.

The token goes only to the Hub's own host, never to the download CDN. An
HTTP 401 or 403 means the token is missing, or the licence was not accepted.

## On a Spark

The commands are the same. `scripts/backends/spark.env` supplies
`HF_ENDPOINT`, `HF_TOKEN_FILE` and `OFFLINE` when they are set there. Our
convention is to download on spark-1 and mirror to spark-2. After the
download, copy the folder over the QSFP link and verify it on the other
side:

```bash
rsync -a --info=progress2 ~/Documents/weights/ORG/REPO/ spark-2:Documents/weights/ORG/REPO/
ssh spark-2 'cd ~/openbeast && scripts/weights-download.sh --verify ~/Documents/weights/ORG/REPO'
```

A model the pair serves still needs its recipe or profile to point at the
folder. Downloading does not change what is served. Packs that already have
their own staging tool keep it:

- `spark-hy4-stage.sh` for Hy4-flash;
- `model-fetch.sh --profile` for a pinned profile;
- `fetch-weight.sh` for the rig's registry GGUFs.

Use `weights-download.sh` for everything else.

## Offline

With `OFFLINE=true` (or no network), a download stops with a clear error and
writes nothing. The offline path is to copy the folder from a machine that
has it, such as the other Spark or the backup SSD, then check the copy:

```bash
scripts/weights-download.sh --verify /path/to/the/copied/folder
```

`--verify` needs only the folder and its `.openbeast-weights.json`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | downloaded and verified, or already present and verified |
| 1 | failed: bad URL, network, HTTP error, a file failed verification, not enough space, refused folder |
| 2 | usage: no URL given |
| 3 | `--verify` (or a re-run) found a missing or changed file |
| 130 | interrupted (re-run to resume) |

## Where the code is

- `scripts/weights-download.sh` is the entry point. It loads the Hub
  settings and picks `python3` (`OPENBEAST_PY` overrides it).
- `scripts/backends/pylib/weights_download.py` holds the URL parsing,
  selection, staging, the manifest and verification.
- It reuses `hfapi.py`, the standard-library Hub client, and the resumable
  download and hash checks of `model_fetch.py`.
- Tests: `tests/test_weights_download.py`, run against a loopback stub Hub
  with no network.
