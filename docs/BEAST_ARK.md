# beast-ark — open-source software, pinned and verifiable, in cold storage

**The problem.** Building or testing a full system with no internet needs
the software itself already on hand, and in a form that runs:
container runtimes, Kubernetes, language toolchains, databases, brokers,
monitoring, a git forge, a CA, a VPN. Each of these is only useful
offline if you also have the exact source it was built from, a runnable
build for each machine you own, and proof that neither changed since you
fetched it. Max asked for exactly that: *"a list of open source software
with pinned versions that we can easily download like kubernetes, docker,
language zips, &c … in hard/cold storage … both the github repo for a
project if it's available and a working image, build, or anything else to
execute said full stack software piece offline."*

**What it is.** `scripts/ark.sh` and `agents/ark/`. Three parts:

1. A **catalog** (`agents/ark/catalog.toml`) of 112 pieces of software
   (350 downloads). Each is pinned to an exact version, git tag **and
   commit**, with a sha256 (or upstream SHA-512 / SHA3) for every file and a
   digest for every image. The `lock` command derived every one of those
   values from upstream. None was typed by hand.
2. **`fetch`** puts the catalog on a drive. Downloads resume. Every file is
   checked against its pin, its upstream checksum file and its signature,
   and anything that fails is deleted. The drive gets an `ark.lock` of every
   file, so it can be checked again later.
3. **`verify`, `restore` and `serve`**, which never use the network. They
   re-check the drive and unpack a toolchain into `~/.local/opt`. They clone
   the source at its pinned commit and load images into docker or a local
   registry. `serve` publishes the drive to the tailnet as an OCI registry
   with pip, npm and go mirrors.

```bash
./scripts/ark.sh plan --profile both                       # how big, per platform, full vs core
./scripts/ark.sh fetch --to /Volumes/ARK --profile core    # online, once
./scripts/ark.sh verify /Volumes/ARK                       # any time, offline
/Volumes/ARK/ark restore go --from /Volumes/ARK            # on a bare box, offline
/Volumes/ARK/ark serve /Volumes/ARK --tailnet              # the drive, for every box on the tailnet
```

The drive carries its own copy of the tool (`DRIVE/ark`). That copy needs
no OpenBeast checkout, and on a box with no python at all it unpacks the
CPython archived on the drive (§7.1).

---

## 1. Where it sits

| | carries | pinned by | lives |
|---|---|---|---|
| `scripts/bundle.sh` | OpenBeast's **own** stack: llama.cpp source, its wheels, its weights, its two images | `MANIFEST.json`, the repo's locks | a bundle directory, for installing OpenBeast offline |
| beast-docs (`scripts/docs.sh`, [BEAST_DOCS.md](BEAST_DOCS.md)) | platform **documentation** (Apple, Android, …) | URL + fetch date + sha256 per page | `~/.openbeast/docs` |
| `model-fetch.sh` ([SPARK_HYDRA.md C1](SPARK_HYDRA.md)) | **weights** | `models/<name>.lock` | `MODELS_DIR` |
| **beast-ark** | **everything else a systems lab runs** | `agents/ark/catalog.toml` (pins) + `DRIVE/ark.lock` (what is on the drive) | a drive |

beast-ark reuses the patterns of all three:

- **The acquirer ships, the content does not.** As in beast-docs, the
  software is never committed. The catalog says what to fetch, and the
  drive holds it.
- **One verification bar.** It is the bar of
  `scripts/backends/tensorfold/install-native.sh`. A checksum file vouches
  only for the one name it binds the hash to. A signature is checked with a
  key **vendored in the repo**, never one fetched at install time. Staging
  (`fetch`) verifies, so the drive is known good before it is carried
  anywhere.
- **A lock is written as you go.** As in `model-fetch.py`, `ark.lock` is
  rewritten atomically after every item. An interrupted fetch keeps what it
  finished, and `verify` re-hashes against the lock, never against the
  network.
- **Offline is a first-class state.** `OFFLINE=true` in `openbeast.conf`,
  or island mode ([BEAST_ISLAND.md](BEAST_ISLAND.md)), makes `fetch`,
  `lock` and `update` refuse at once with exit 3. The check is the same
  `offline_reason()` beast-docs uses. `verify`, `restore`, `serve`, `list`
  and `plan` never dial.

`agents/ark/` is under `agents/`, so it ships in the client-mode slim
checkout too. A Mac client can fill a drive or restore from one, with
one exception: a **pip set** must be fetched on a box of its own platform
and python (§3.2), so the Linux vLLM sets are filled on a Linux box (a
Spark for linux-aarch64).

---

## 2. The trust model: what each check proves

| Check | Proves | Does not prove |
|---|---|---|
| **sha256 pin in the catalog** (`sha256 = …`, reviewed in git) | the file is byte-identical to the one `lock` saw upstream | who built it |
| **upstream checksum file** (`checksum_url`), which must **name this exact file** | upstream says this is the file for this name | anything, if upstream's release page itself was replaced |
| **signature** (`signature_url`) with a **vendored** key | the publisher's key signed it (minisign, TensorFold's ed25519). GPG with a pinned keyring | — |
| **git commit pin** | the tag still points where it did when `lock` ran: a moved tag is **refused** | — |
| **OCI digest pin** | every manifest and blob is content-addressed and re-checked on arrival | — |
| **GitHub release-asset digest** (`verified = "upstream-json"` on a file with no `checksum_url`) | GitHub's own sha256 for the asset, read from its release API at `lock` time | anything against a compromised maintainer account (GitHub computes it from what was uploaded). It is the same trust as a `checksums.txt` on the same release |
| **`ark.lock` on the drive** | the drive did not change since `fetch` (bit rot, a bad copy, tampering by someone who did not also rewrite the lock) | that the lock itself is genuine: it is on the same drive. Keep a copy of `ark.lock` off the drive, or compare with the catalog's pins (§9). **Nothing in it is trusted to name a place on the restoring box:** entry names, `bins`, tags and commits are checked before they become paths or git arguments (§7.4) |
| **trust on first use** (`verified = "unverified"` in the catalog → `tofu` in the lock) | only that the drive still holds what was downloaded | anything about the download. Used only where upstream publishes **no** checksum file **and** the file is not a GitHub release asset (whose digest `lock` takes instead). Named in the catalog, refused without `--allow-unverified`, and reported by every `verify` — including after `lock --from` has copied the hash into the catalog (the lock then says `tofu-pinned`, §10.4) |

The catalog is the trust root, as `models/*.lock` is for weights. A hash
came from upstream once, at `lock` time, and from then on it is a fact in
a reviewed commit. A later fetch must reproduce it. If upstream ever serves
different bytes under the same name, `fetch` refuses, and `lock` reports
`UPSTREAM CHANGED` and never overwrites the pin (§11).

**Signatures, concretely.**

- **minisign** (Zig, 3 files) and **ed25519-sums** (TensorFold, 3 files)
  are verified in pure python by `agents/ark/verify.py`. That module is an
  RFC 8032 implementation, tested against the RFC's own vector, so it needs
  no OpenSSL 3 and no `cryptography`. Zig's key
  (`RWSGOq2NVecA2UPNdBUZykf1CCb147pkmdtYxgb3Ti+JO/wCYvhbAb/U`) is the one
  ziglang.org/download publishes. TensorFold's is
  `agents/ark/keys/tensorfold-release-key.pem`, byte-identical to the
  installer's (a test holds them equal).
- **gpg** (53 files: Node, Temurin, Rust, CMake, Kafka, restic, Ubuntu, …):
  the `.asc` / `.sig` is fetched and kept next to what it signs. It is
  verified only when `gpgv` is installed **and**
  `agents/ark/keys/<entry>.gpg` holds a keyring that was pinned by review.
  No keyring is vendored yet. Each one needs its fingerprint confirmed
  against the publisher out of band, so these report `stored`, never
  `verified`. §9 shows how to check them by hand.
- **cosign / sigstore**: these bundles need an online transparency log. The
  catalog does not fetch them. BuildKit's and Kubernetes' notes say how to
  check a bundle by hand.

---

## 3. The catalog format — `agents/ark/catalog.toml`

It holds two flat lists. Flat on purpose: a pin is one line, a reviewer
reads it in a diff, and `lock` / `update` rewrite exactly that line.

```toml
[ark]
schema = 1

[[entry]]
name = "go"
category = "languages"
description = "Go toolchain"
homepage = "https://go.dev"
license = "BSD-3-Clause"
version = "1.27.4"
profiles = ["core"]
update = "go"
git = "https://go.googlesource.com/go"
tag = "go{version}"
commit = "…40 hex, written by `ark lock`…"

[[artifact]]
entry = "go"
kind = "file"
platform = "linux-aarch64"
url = "https://dl.google.com/go/go{version}.linux-arm64.tar.gz"
sha256 = "…written by `ark lock` from the .sha256 below…"
checksum_url = "https://dl.google.com/go/go{version}.linux-arm64.tar.gz.sha256"
install = "tar"
verified = "upstream-sums"
size = 66000000
```

The file is parsed by `agents/ark/tomlsub.py`, a strict TOML subset parser
that works on python 3.9 and records the line each value came from. A test
checks that it reads the real catalog exactly as `tomllib` does.

### 3.1 `[[entry]]`

| field | required | meaning |
|---|---|---|
| `name` | yes | slug, unique (`[a-z0-9][a-z0-9._-]*`) |
| `category` | yes | one of `containers orchestration languages build databases messaging observability networking dev cli storage security iac ai docs os virtualization` |
| `description`, `homepage` (https) | yes | |
| `license` | yes | an SPDX expression. If any id is not OSI-approved (the list is in `catalog.py`), `license_note` must say why a personal archive may keep it, or lint fails |
| `version` | yes | the pinned upstream version |
| `profiles` | | `["core"]` puts the entry in the core profile (§4) |
| `update` | | how `update` finds a newer release: `github:owner/repo` (the latest release), `github-tag:owner/repo[:regex]` (the highest stable tag, optionally limited to a line, e.g. nginx's `^1\.30\.`), `node-lts`, `go`, or `manual` |
| `git` | | the upstream repository (https; `file://` only in tests) |
| `tag` | | default `v{version}`. Set `tag = ""` for a commit-only pin (llama.cpp, DevDocs) |
| `commit` | with `git` | 40 hex. `lock` fills it from `git ls-remote` of the tag (the peeled commit), or of `HEAD` for a tag-less pin. Lint fails without it unless `git_verified = "unverified"` |
| `history` | | `shallow` (default: the tag's commit only, stored as a tarred shallow bare repo) or `full` (a real `git bundle` of the tag with all its history) |
| `git_size` | | bytes. `lock` writes GitHub's whole-repository size. That is an upper bound for a shallow pin, and the plan labels it `≤` |
| `vars` | | `["k=v", …]`: extra template variables, e.g. `release=20261110` for python-build-standalone, `build=42849527790` for Grafana's tarballs |
| `note`, `license_note` | | free text that `restore` prints |

### 3.2 `[[artifact]]`

`kind = "file"`: a release file for one platform.

| field | meaning |
|---|---|
| `platform` | `linux-x86_64` (the 5090 rig), `linux-aarch64` (the DGX Sparks), `darwin-arm64` (Macs), or `any` (source tarballs, JVM builds, docs) |
| `url` | https |
| `sha256` | the pin. `lock` writes it from upstream, never from our own download (`lock --from` is the TOFU exception, §10) |
| `upstream_hash` | `sha512:…` / `sha3-256:…` when upstream publishes no sha256 (Caddy, ClickHouse, Kafka, cni-plugins: SHA-512; SQLite: SHA3-256). `fetch` checks it, and the drive records the sha256 too |
| `size` | bytes, from a HEAD at `lock` time |
| `checksum_url`, `checksum_format`, `checksum_name` | the upstream checksum. Formats: `sums` (coreutils `hex  name` / `hex *name` / paths, BSD `SHA256 (name) = hex`, gpg `--print-md` wrapped hex as Apache uses, or one bare hash at a per-file URL), `json:a/b/c` with `[k=v,…]` list selectors (Zig's `index.json`, Grafana's download API), `sqlite-csv` (sqlite.org's download page). `checksum_name` sets the name to look up when it differs from the URL's basename |
| `signature_url`, `signature_kind`, `signature_key`, `signature_prefix` | `minisign` (key: the base64 `RW…` or a repo path), `ed25519-sums` (a repo-path PEM key; the signature covers `prefix + checksum file`, as TensorFold's does), `gpg`, `cosign`. A signature over the checksum file (`SHASUMS256.txt.sig`) is detected from the URL |
| `install` | how `restore` lays it out: `bin` (one executable), `tar` / `zip` / `zst` (unpack; one top directory is stripped), `gz` / `bz2` / `xz` (one compressed binary), `manual` (unpack it yourself; the note says how), `data` (kept as-is: ISOs, ZIMs, air-gap image bundles) |
| `bins` | paths inside the install root to link into `~/.local/bin`. The default is every executable in `bin/`. For `install = "bin"` / `gz` / `bz2` / `xz` it is ONE file name. The lint, and `restore` again, refuse an absolute path or a `..` |
| `strip` | `0` keeps a lone top directory (`containerd`'s `bin/`); the default `-1` means auto |
| `verified` | where the hash came from: `upstream-sums`, `upstream-json` (a JSON index, or GitHub's release API digest for an asset with no checksum file), `registry`, `tofu`, or the explicit **`unverified`** |

`kind = "oci"`: an image (or a non-image OCI artifact such as Trivy's
vulnerability DB), pinned by **digest**.

| field | meaning |
|---|---|
| `image` | `registry/repo` with no tag (`docker.io/library/postgres`, `registry.k8s.io/kube-apiserver`) |
| `tag` | the human name. The **digest** is the pin |
| `digest` | the top-level digest (an index for multi-arch images). `lock` resolves it at the registry with a HEAD |
| `platforms` | which children to keep: `linux/amd64`, `linux/arm64`. A Mac runs `linux/arm64` images in its VM, so its plan counts those |
| `platform_sizes` | `["linux/amd64=N", …]`, summed from the manifests at `lock` time |

`kind = "pip" | "npm" | "gomod" | "cargo"`: a **package set**, resolved by
the ecosystem's own tool at fetch time, so `verified = "tofu"`:

- `pip`: `requirements = ["vllm=={version}"]`, `python = "3.12"`,
  `pip_platforms = [manylinux tags]`. It is `pip download --only-binary`
  with the target's wheel tags, and it writes `requirements.ark.txt` with a
  `--hash` line per wheel, so the offline install runs `--require-hashes`.
  **Fetch a pip set on a box of its platform, running its python.** pip's
  `--platform` / `--python-version` choose wheel *tags* only; the
  environment markers in dependency metadata (`sys_platform`,
  `platform_machine`, `python_version`) are evaluated against the box that
  runs pip. A linux-x86_64 vLLM set resolved on a Mac or an aarch64 Spark
  silently drops torch's `platform_machine == "x86_64"` nvidia-* wheels,
  and the offline install then fails on the target. So `fetch` refuses a
  set whose platform or python does not match the running box, and says
  which box to use; `--allow-cross-pip` accepts the risk and records
  `verified = "tofu+cross-host"`, which `verify` reports. It resolves from
  **PyPI only**, `pip download --isolated --index-url https://pypi.org/simple/`
  with `PIP_*` cleared, so an extra index in the fetching box's pip config
  can never supply a package. A mirror is used only when named on purpose
  in `ARK_PIP_INDEX_URL`, and the lock's `source` records the index used.
- `npm`: `packages = ["name@ver"]`. The whole tree comes from
  `npm install --package-lock-only --registry=https://registry.npmjs.org/`
  with `~/.npmrc` and the global config ignored (`ARK_NPM_REGISTRY` names a
  mirror on purpose). Every tarball must resolve on that registry and is
  checked against its `integrity`; one with no `integrity` is refused.
- `gomod`: `go mod download` in a clone of the entry's git pin. Go checks
  every module against `go.sum`, and the drive keeps the `cache/download`
  tree, which is a GOPROXY.
- `cargo`: `cargo vendor --locked` in a clone of the git pin, checked
  against `Cargo.lock`.

### 3.3 Templates

Any string may use `{version}`, `{tag}`, `{version_pu}` (`+` → `_`:
Temurin's `21.0.13+9` is `21.0.13_9` in file names) and `{version_du}`
(`.` → `_`: postgres `18.7` is tag `REL_18_7`), plus the entry's `vars`.
An unknown variable is a lint error.

### 3.4 The lint (`./scripts/ark.sh lint`; CI runs it through the tests)

It fails on:

- an unknown field;
- a non-OSI licence without a note;
- a non-https URL (plain http is allowed for loopback only);
- a git pin without a commit;
- a file without a hash, an OCI artifact without a digest. Either needs the
  explicit `unverified` mark instead;
- a hash whose `verified` does not say where it came from;
- a signature with no vendored key;
- a pip requirement that is not `name==exact-version`;
- a package set not marked `tofu`;
- a duplicate id, or an unknown template variable.

---

## 4. What is in it (2026-11-15)

| category | entries | core | artifacts | entries |
|---|---|---|---|---|
| containers | 10 | 8 | 27 | buildkit, containerd, distribution, docker, docker-buildx, docker-compose, nerdctl-full, oras, podman, runc |
| orchestration | 8 | 6 | 37 | cni-plugins, cri-tools, etcd, helm, k3s, k9s, kind, kubernetes |
| languages | 14 | 7 | 45 | bun, cpython-3.12, cpython-3.14, deno, go, lang-images, llvm, node, rust, swift, temurin-21, temurin-25, uv, zig |
| build | 10 | 2 | 27 | bazel, bazelisk, ccache, cmake, just, meson, mold, ninja, sccache, task |
| databases | 7 | 4 | 16 | clickhouse, duckdb, mariadb, postgres, redis, sqlite, valkey |
| messaging | 4 | 1 | 7 | kafka, mosquitto, nats, rabbitmq |
| observability | 8 | 4 | 29 | alertmanager, alloy, grafana, jaeger, loki, node-exporter, otelcol-contrib, prometheus |
| networking | 9 | 5 | 18 | caddy, coredns, curl-static, headscale, nginx, tailscale, traefik, wireguard-go, wireguard-tools |
| dev | 7 | 4 | 17 | code-server, forgejo, gh, git, lazygit, neovim, shellcheck |
| cli | 5 | 5 | 15 | fd, fzf, jq, ripgrep, yq |
| storage | 5 | 3 | 13 | minio, rclone, restic, seaweedfs, syncthing |
| security | 8 | 6 | 26 | age, cosign, openbao, sops, step, step-ca, syft, trivy |
| iac | 2 | 1 | 6 | ansible-core, opentofu |
| ai | 5 | 2 | 7 | llama.cpp, open-webui, searxng, tensorfold, vllm |
| docs | 4 | 2 | 44 | devdocs, devdocs-zim-core, devdocs-zim-extra, kiwix-tools |
| os | 3 | 2 | 8 | alpine-minirootfs, base-images, ubuntu-server |
| virtualization | 3 | 2 | 8 | colima, firecracker, lima |
| **total** | **112** | **64** | **350** | |

**Verification, as `lock` left it:**

- **334 of 350 artifacts are hash- or digest-pinned from upstream.**
  - 251 files from upstream checksum files;
  - 6 from upstream JSON indexes (Zig's `index.json`, Grafana's download API);
  - 43 GitHub release assets with no checksum file, from the per-asset
    `digest` GitHub's release API publishes (2026-11-16): BuildKit, LLVM,
    ninja, mold, ccache, DuckDB, ClickHouse's macOS binary, SeaweedFS (MD5
    only upstream), neovim, code-server, fd, yq (a multi-hash table),
    shellcheck, age (transparency proofs), curl-static, the meson source
    tarball and buildx's darwin binary (its `checksums.txt` lists Linux
    only);
  - 34 images and DB artifacts by registry digest.
- 10 files are explicitly `unverified`, because their upstream publishes no
  checksum at all and they are not GitHub release assets:
  - Docker's static tarballs (3) and kiwix-tools (3);
  - Swift (2) and the nginx source tarball (gpg only);
  - k3s's `install.sh` (it is also inside the commit-verified git pin).
- 6 package sets are `tofu` by design:
  - vLLM for x86_64 and aarch64;
  - ansible-core for all three platforms;
  - MinIO's Go modules, which `go.sum` checks.
- All 98 git pins have a commit from `git ls-remote`.

**Choices the catalog makes:**

- **Licences are OSI or explained.** These are in on a `license_note`:
  - SQLite (public domain);
  - curl;
  - `just` (CC0);
  - neovim's Vim-licensed parts;
  - the DevDocs ZIMs (each keeps its project's docs licence);
  - the OS images;
  - Open WebUI (BSD-3 plus a branding clause since v0.6.6).

  **Left out on purpose:** Redpanda (BSL; Kafka is in), Terraform / Vault /
  Consul (BSL; OpenTofu and OpenBao are in), MongoDB and Elasticsearch
  (SSPL).
- **MinIO is source plus Go modules.** MinIO stopped publishing community
  binaries: `dl.min.io` answers **410 Gone** for its last release. The ark
  keeps the source at the pinned commit and every Go module it needs, and
  builds it offline (§7.6). SeaweedFS is the binary S3 store.
- **GCC and PostgreSQL** publish no official generic binaries. The official
  images are the runnable builds (`lang-images` has `gcc:16.2.0`), and the
  source tarballs are kept too.
- **The repo's own pins are mirrored, not re-chosen.** Each is held equal
  to the repo by `tests/test_ark.py`:
  - llama.cpp is `scripts/llama.cpp.ref`'s commit;
  - TensorFold is `install-native.sh`'s 1.2.7 (`update` sees 1.2.9 and
    leaves it to whoever moves the installer);
  - Zig is 0.16.0, whose x86_64 sha256 equals CI's `ZIG_SHA256`;
  - Open WebUI and SearXNG are at `docker-compose.yml`'s digests.
- **The Kubernetes control-plane image tags** (etcd `3.7.0-0`, coredns
  `v1.14.6`, pause `3.10.2`) are the ones kubeadm v1.37.2 hard-codes in
  `cmd/kubeadm/app/constants`. They were read from the source at the tag.

---

## 5. Sizes (`./scripts/ark.sh plan --profile both`, 2026-11-15)

The **core** profile is the 64 entries a bare-metal lab needs to stand up a
whole stack. It covers these groups:

| group | core entries |
|---|---|
| containers | Docker, compose, buildx, containerd, runc, nerdctl-full, the registry, ORAS |
| orchestration | Kubernetes with its images, k3s with its air-gap bundle, kind, Helm, CNI plugins, crictl |
| languages | CPython 3.14, Go, Rust, Zig, Node LTS, Temurin 25, uv |
| build | CMake, ninja |
| databases | Postgres, Valkey, SQLite, DuckDB |
| messaging | NATS |
| observability | Prometheus, the node exporter, Grafana, the OTel collector |
| networking | headscale, Tailscale, WireGuard, Caddy, CoreDNS |
| dev | Forgejo, git, gh, neovim |
| storage | SeaweedFS, restic, rclone |
| security | step-ca, step, Trivy with its DBs, cosign, age, OpenBao |
| iac | OpenTofu |
| ai | llama.cpp, TensorFold |
| docs | the core DevDocs ZIMs and kiwix-tools |
| cli | jq, yq, ripgrep, fd, fzf |
| os | the Alpine root fs and the base images |
| virtualization | Lima, Colima |

**Full** is all 112.

| profile | linux-x86_64 (rig) | linux-aarch64 (Sparks) | darwin-arm64 (Macs) | one drive, all three platforms |
|---|---|---|---|---|
| **core**, artifacts | 5.79 GiB | 5.42 GiB | 4.43 GiB | **13.2 GiB** |
| **full**, artifacts | 21.04 GiB + 3 unsized | 16.28 GiB + 3 | 10.96 GiB + 2 | **41.8 GiB** + 6 unsized |
| git (source), upper bound | core ≤ 14.5 GiB, full ≤ 42.4 GiB (+3 non-GitHub repos unsized) | same | same | same (counted once) |

How to read it:

- **"One drive, all three"** counts an `any` file once and each OCI
  platform once.
- **The unsized six are package sets.** Their size is known only after
  resolution: vLLM ×2 (torch plus the nvidia-* wheels; expect several GB
  per platform), ansible-core ×3 (tens of MB) and MinIO's Go modules
  (hundreds of MB).
- **The git column is GitHub's *full-repository* size, an upper bound.** A
  shallow pin is far smaller. Measured in the smoke run:

  | repo | GitHub size | shallow pin |
  |---|---|---|
  | distribution | 53 MiB | 13.9 MiB |
  | jq | 7.9 MiB | 1.4 MiB |
  | TensorFold | 75 MiB | 37.5 MiB |

  Expect the real source total to be a fraction of the bound.
  `lock --from DRIVE` replaces the bound with the measured sizes.
- **The biggest single items:**
  - the DevDocs app image (4.0 GiB, amd64 only; the ZIMs are the
    lightweight path);
  - LLVM (~1.5-2 GiB per platform);
  - the Ubuntu server ISOs (~2.7 GiB amd64, ~2.9 GiB arm64);
  - Trivy's Java DB (~1 GiB);
  - the Grafana and Loki images.

**Drive sizing:** a 64 GB stick holds **core for every platform with full
source history to spare**. A 256 GB drive holds **full** plus vLLM's wheels
plus the git pins, with room for weights from `bundle.sh` beside it.
exFAT works (no symlinks or permissions are needed on the drive), and so
do APFS and ext4.

---

## 6. The drive

```
/Volumes/ARK/
  ark                  POSIX sh entry point: runs the tool from the drive (finds python >= 3.9,
                       else unpacks the archived CPython after a sha256 check)
  ARK.md               generated: what is on this drive and the offline commands
  ark.lock             JSON: every file → path, sha256, size, source URL, how it was verified,
                       signature status, the git commit / image digest
  SHA256SUMS           the same hashes in coreutils format (`sha256sum -c`, no python needed)
  catalog.toml         the catalog this drive was filled from (restore reads its notes)
  tool/agents/ark/     a copy of the tool + keys (self-hosting; also tool/agents/docs/config.py)
  git/<entry>/         <entry>-<tag>.shallow.git.tar   (history = shallow: a bare repo, tarred)
                       <entry>-<tag>.bundle            (history = full: `git bundle`)
  files/<entry>/<version>/<platform>/
                       the release file + its checksum file + its signature, as upstream named them
  oci/<entry>/<image>_<tag>.oci.tar
                       OCI image layout, uncompressed tar: oci-layout, index.json (→ the pinned
                       digest), blobs/sha256/…, plus a docker-archive manifest.json
  pkgs/pip/<entry>-<v>/<platform>/   wheels + requirements.ark.txt (--require-hashes)
  pkgs/npm/<entry>-<v>/<platform>/   package.json, package-lock.json, tarballs/
  pkgs/gomod/<entry>-<v>/any/goproxy/  a GOPROXY tree (cache/download)
  pkgs/cargo/<entry>-<v>/any/        vendor.tar + cargo-config.toml
  .ark-stage/          partial downloads and pull state; never served, safe to delete
```

**Why a shallow pin is a tarred repo, not a bundle.** `git bundle create`
in a shallow repository writes a bundle that says *"records a complete
history"* but cannot be cloned ("Failed to traverse parents of commit"). We
measured that on 2026-11-15. A shallow bare repository *can* be cloned,
keeps the real commit SHA, and is verified on restore. `history = "full"`
entries get a genuine `git bundle`.

**`ark.lock` item** (abridged):

```json
"tensorfold/linux-aarch64/tensorfold-V-linux-aarch64.tar.gz": {
  "entry": "tensorfold", "kind": "file", "platform": "linux-aarch64", "version": "1.2.7",
  "source": "https://github.com/ashhart/TensorFold/releases/download/v1.2.7/tensorfold-1.2.7-linux-aarch64.tar.gz",
  "verified": "pinned+upstream-sums+signature",
  "signature": {"status": "verified", "kind": "ed25519-sums", "over": "checksum", "detail": "ed25519 over the checksum file", …},
  "files": [{"path": "files/tensorfold/1.2.7/linux-aarch64/tensorfold-1.2.7-linux-aarch64.tar.gz", "sha256": "…", "size": 49812341},
            {"path": "…/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256", …},
            {"path": "…/tensorfold-1.2.7-linux-aarch64.tar.gz.sha256.sig", …}],
  "primary": "files/tensorfold/1.2.7/linux-aarch64/tensorfold-1.2.7-linux-aarch64.tar.gz",
  "fetched_at": "2026-11-15T22:58:12Z"
}
```

---

## 7. Commands, with the output they print

Exit codes everywhere:

| code | meaning |
|---|---|
| 0 | ok |
| 1 | verification failed (a mismatch, corruption, a missing file, a bad signature) |
| 2 | usage, catalog or not-a-drive error |
| 3 | an offline refusal |
| 4 | incomplete: some items failed, everything else was kept |

| variable | meaning |
|---|---|
| `ARK_CATALOG` | a different catalog |
| `ARK_HTTP_TIMEOUT` | seconds, default 60 |
| `ARK_FORCE_OFFLINE=1` | act as offline |
| `OPENBEAST_OFFLINE` | as in `openbeast.conf` |
| `GITHUB_TOKEN` | only for `lock`'s repository sizes; else `gh auth token` is asked. It is sent to `api.github.com` only and never printed |
| `OPENBEAST_PYTHON` | which python `ark.sh` runs |

### 7.1 list, plan, lint

```
$ ./scripts/ark.sh list --category languages

[languages]
   bun                    1.4.4                MIT                          file+git
 * cpython-3.14           3.14.10              PSF-2.0                      file+git
 * go                     1.27.4               BSD-3-Clause                 file+git
   lang-images            2026-11              GPL-3.0-or-later WITH GCC-exception-3.1 oci
   llvm                   23.1.5               Apache-2.0 WITH LLVM-exception file+git  (3 unverified)
 * zig                    0.16.0               MIT                          file+git
   …
14 entries (* = core profile). Details: docs/BEAST_ARK.md

$ ./scripts/ark.sh plan --profile core --platform linux-aarch64
beast-ark plan — profile core: 64 entries

category         entries    linux-aarch64
containers             8          498 MiB
orchestration          6         1002 MiB
…
artifacts                        5.42 GiB
git (source)               ≤14.50 GiB +3?

$ ./scripts/ark.sh lint
lint …/agents/ark/catalog.toml: 112 entries, 350 artifacts (334 hash/digest-pinned), 0 problem(s)
```

`--only a,b`, `--category c` and `--profile core|full|both` combine;
`--platform host` means this box; `--json` for scripts.

### 7.2 fetch (online)

```
$ ./scripts/ark.sh fetch --to /Volumes/ARK --only jq,tensorfold,distribution
fetch 12 item(s) for linux-x86_64, linux-aarch64, darwin-arm64 into /Volumes/ARK
  fetch git:distribution
    ok git:distribution (13.9 MiB, pinned)
  fetch distribution/linux-x86_64/registry_V_linux_amd64.tar.gz
    ok distribution/linux-x86_64/registry_V_linux_amd64.tar.gz (15.3 MiB, pinned+upstream-sums)
  fetch distribution/oci/registry
    linux/amd64 sha256:77c315b2f195 15 MiB
    …
    ok distribution/oci/registry (37.8 MiB, registry-pinned)
  fetch tensorfold/linux-aarch64/tensorfold-V-linux-aarch64.tar.gz
    ok tensorfold/linux-aarch64/tensorfold-V-linux-aarch64.tar.gz (47.5 MiB, pinned+upstream-sums+signature)
  …
fetch: 12 fetched, 0 already present, 0 failed — /Volumes/ARK
```

That is the real 2026-11-15 smoke run: 228 MB in 30 s. Options:

| option | meaning |
|---|---|
| `--platform P` (repeatable) | which platforms; the default is all three. `--platform linux-aarch64` fills a Spark-only drive |
| `--profile core`, `--only`, `--category` | which entries |
| `--kinds file,oci,git,pip,npm,gomod,cargo`, `--no-git` | which kinds of item |
| `--allow-unverified` | accept the 10 no-checksum files as TOFU |
| `--allow-cross-pip` | fetch a pip set on a box whose platform or python differs from the set's (§3.2): recorded as `tofu+cross-host`, and the closure may be incomplete |
| `--recheck` | re-hash what is already present instead of trusting its size |
| `--dry-run` | list what would be fetched |

Re-running is always safe. Items already present (same pin, same size)
are skipped: `12 already present`.

**What it refuses** (the messages as tests/test_ark.py triggers them), each deleting the download and continuing with the
next item (exit 4 at the end):

```
FAILED toy/any/toy-1.0.tar.gz: toy/any/toy-1.0.tar.gz: sha256 MISMATCH — downloaded 6244…, the catalog pins d233…. REFUSED and deleted.
FAILED …: the upstream checksum file says sha256 aaaa…, the download does not match. REFUSED and deleted.
FAILED …: the checksum file does not name 'toy-1.0-linux.tar.gz'          (a sums file for another version)
FAILED …: minisign signature does NOT verify
FAILED git:toy: tag v1.0 is commit 77a1… upstream, the catalog pins 0000… — REFUSED (a moved tag is exactly what a pin is for)
FAILED app/oci/app: …@sha256:eeee…: the registry served a manifest whose digest is sha256:3f1c… — refused
FAILED nosum/any/blob.bin: no hash pin and no upstream checksum — refused (--allow-unverified records it as trust-on-first-use)
FAILED zg/any/zig.tar.xz: the minisign signature is not valid base64                (a garbled upstream file fails its item only)
FAILED app/oci/app: toy/app@sha256:…: the registry did not answer JSON               (an HTML error page served with 200)
FAILED demo/pip/linux-x86_64: this box is darwin-arm64, the set is for linux-x86_64. pip evaluates dependency markers against the box it runs on, …
FAILED lp/npm/any: node_modules/left-pad has no integrity in the lock — refused (nothing to check it against)
```

An image whose pull outlives its registry token (Docker Hub's anonymous
ones last 300 s) is not one of these: the expired token is replaced from
the registry's challenge and the blob resumes.

**Topping up a platform keeps what is there.** One archive holds every
kept platform of an image, so `fetch --platform linux-aarch64` on a drive
filled for linux-x86_64 rewrites each image with **both** platforms. The
amd64 blobs are copied out of the existing archive, not downloaded again.

Package sets need their tool on the fetching box:

- `pip` uses the running python's pip, which must match the set's
  platform and python (§3.2): `OPENBEAST_PYTHON=~/.local/opt/cpython-3.12/bin/python3
  ./ark fetch --kinds pip --platform host` on the target box;
- `npm` needs npm;
- `gomod` needs go (restore it from the ark first:
  `ark restore go --from …`);
- `cargo` needs cargo.

A missing tool fails that item only.

### 7.3 verify (offline)

```
$ ./scripts/ark.sh verify /Volumes/ARK --deep
verify /Volumes/ARK: 23 files OK across 12 items
RESULT: OK
```

It re-hashes every file in `ark.lock` and re-checks every minisign and
ed25519 signature. `--deep` also re-hashes every blob inside every OCI
archive. `--quick` checks presence and size only, takes minutes on a full
drive, and says `NOT re-hashed`. `--jobs N` hashes N files at once: about
N times faster on storage quicker than one core's sha256 (~1.8 GB/s:
internal NVMe, a USB4 SSD); keep the default 1 on a spinning disk. On
failure:

```
verify /Volumes/ARK: 21 files OK across 12 items
  CORRUPT: files/toy/1.0/any/toy-1.0-linux.tar.gz
  MISSING: files/toy/1.0/any/single
  1 file(s) on the drive that ark.lock does not list (never served or restored):
    files/toy/planted.sh
  2 item(s) trusted on first use (no upstream checksum existed): docker/linux-x86_64/…
                                     (also every `tofu-pinned` and `tofu+cross-host` item)
  14 gpg/cosign signature(s) kept but not checked here (docs/BEAST_ARK.md §9)
RESULT: FAILED
```

With no python at all: `cd /Volumes/ARK && sha256sum -c SHA256SUMS`
(macOS: `shasum -a 256 -c SHA256SUMS`).

### 7.4 restore (offline)

```
$ ./ark restore jq --from /Volumes/ARK
jq: source at /home/max/src/jq (jq-1.8.2 34f7186b8674, verified)
jq 1.8.2: /home/max/.local/opt/jq-1.8.2  (linked into /home/max/.local/bin: jq)
$ jq --version
jq-1.8.2

$ ./ark restore tensorfold --from /Volumes/ARK --kinds file
tensorfold 1.2.7: /home/max/.local/opt/tensorfold-1.2.7  (linked into /home/max/.local/bin: tensorfold-native)
$ tensorfold-native --version
tensorfold-native 1.2.7

$ ./ark restore distribution --from /Volumes/ARK --kinds oci --docker
distribution: docker load docker.io/library/registry:3.1.2 (linux/arm64, manifest sha256:f3d00d4a9323)
```

Those three are real runs. The image then ran (`registry 3.1.2`), and
docker gave it the ID `sha256:4f30…`, which is the pinned config blob's
digest.

Every file is re-hashed before it is used. One corrupt file refuses the
whole entry (`… is CORRUPT … — refusing to restore it`, exit 1).

**Nothing on the drive chooses where restore writes.** `ark.lock` is on
the same drive it describes, so a swapped drive can rewrite it. Restore
therefore refuses (exit 1):

- an entry name that is not a catalog name (`../../x`);
- a `bins` value that is not one file name for a single binary, or that
  resolves outside the install root (`../../../../.ssh/authorized_keys`);
- a git commit that is not full hex, or a tag that is not a plain name
  (they are git arguments).

**Archives are unpacked one member at a time, each checked against the
disk as it is then**, so a link chain (`y -> .`, `x -> y/..`, `x/evil`)
cannot write outside the target, and every link is re-resolved once the
tree is complete. This does not depend on python 3.12's tar `data` filter
(which is applied on top where it exists): a Mac's `/usr/bin/python3`
3.9.6 is as safe. The archive is read once, front to back, so a `.tar.xz`
is decompressed once.

| what | where it goes | options |
|---|---|---|
| release files | `~/.local/opt/<entry>-<version>/`, plus `~/.local/opt/<entry>` → it, plus links in `~/.local/bin`. One entry's files merge into one root (kubeadm, kubelet and kubectl together) | `--prefix`, `--bin`, `--platform` (default: this box) |
| git | `~/src/<entry>`, checked out at the pinned commit, re-checked, tagged | `--src`, `--force` to replace |
| images | `--registry HOST:PORT` pushes every kept platform plus the index (digests unchanged; §8). `--docker` / `--podman` loads this box's platform as a one-platform tar that docker 20.10+ loads. With neither, it prints the `docker load` / `skopeo copy` lines | |
| pip | a venv in `<prefix>/<entry>-<v>/venv`, `pip install --no-index --require-hashes` | `--python PATH`: the wheels' python, e.g. the archived CPython |
| npm | tarballs into npm's cache, then `npm ci --offline` | |
| gomod | prints the `GOPROXY=file://…` line | |
| cargo | `vendor/` plus a `cargo-config.toml` to copy into `.cargo/config.toml` | |
| `manual` / `data` | prints the path and the catalog's note (ISOs, ZIMs, Rust's `install.sh`, k3s's image bundle) | |

`--kinds git,file,oci,…` picks what to restore.

### 7.5 serve (offline)

```
$ ./ark serve /Volumes/ARK --tailnet
beast-ark serving /Volumes/ARK at http://100.101.102.103:8780/ (read-only; Ctrl-C stops)
  images: 100.101.102.103:8780/<repo>:<tag>   pip: http://100.101.102.103:8780/pypi/simple/   npm: …/npm/   go: …/goproxy
```

See §8.

### 7.6 lock and update (online)

These are for maintaining the catalog, so they belong to whoever reviews
the repo. See §10.

---

## 8. Restoring on a bare box with no network

### 8.1 Step zero: the tool itself

Plug the drive in. Everything starts from it:

```bash
cd /media/$USER/ARK                    # or /Volumes/ARK on a Mac
sha256sum -c SHA256SUMS | grep -v ': OK$'   # prints nothing on a good drive (macOS: shasum -a 256 -c)
./ark verify .                         # the same, plus signatures, via python
```

Running `./ark` needs **no OpenBeast checkout and no python install**. It
looks for python >= 3.9 (`python3.14 … python3`). If there is none, it
looks for `files/cpython*/…/<this platform>/cpython-*-install_only.tar.gz`
on the drive. It checks that file against `SHA256SUMS` and refuses it on a
mismatch, unpacks it into `~/.local/opt/ark-python`, and runs the tool with
it. `tests/test_ark.sh` proves both branches on a PATH with no python.

### 8.2 A developer toolchain

```bash
./ark restore cpython-3.14 --from .   # python3, pip3 in ~/.local/bin
./ark restore go --from .
./ark restore zig --from .            # 0.16.0, minisign-verified when fetched, re-verified by `verify`
./ark restore node --from .
./ark restore temurin-25 --from .     # java, javac
./ark restore uv --from .
export PATH="$HOME/.local/bin:$PATH"
```

**Rust** is `install = "manual"`, because its standalone installer
installs itself:

```bash
./ark restore rust --from .           # prints the path
tar -xJf files/rust/1.100.0/linux-aarch64/rust-1.100.0-aarch64-unknown-linux-gnu.tar.xz -C /tmp
/tmp/rust-1.100.0-aarch64-unknown-linux-gnu/install.sh --prefix="$HOME/.local/opt/rust-1.100.0" --disable-ldconfig
tar -xJf files/rust/1.100.0/any/rust-src-1.100.0.tar.xz -C /tmp && /tmp/rust-src-1.100.0/install.sh --prefix="$HOME/.local/opt/rust-1.100.0"
export PATH="$HOME/.local/opt/rust-1.100.0/bin:$PATH"
```

### 8.3 Docker with no internet (Linux)

```bash
./ark restore docker --from .         # dockerd, docker, containerd, runc, … in ~/.local/opt/docker
sudo cp ~/.local/opt/docker/{docker,dockerd,containerd,containerd-shim-runc-v2,ctr,runc,docker-init,docker-proxy} /usr/local/bin/
sudo dockerd &                        # or write a systemd unit; see docs.docker.com/engine/install/binaries
./ark restore docker-compose --from . && mkdir -p ~/.docker/cli-plugins && ln -sf ~/.local/bin/docker-compose ~/.docker/cli-plugins/
./ark restore base-images --from . --docker   # alpine, debian, ubuntu, busybox, loaded by digest
```

On a Mac, the engine is `colima` or `lima` (both are in core). Their VM
needs a guest image, and the drive's `ubuntu-server` ISO or a cloud image
works. The docker CLI is `./ark restore docker` (darwin-arm64: the CLI
only).

### 8.4 k3s, air-gapped

This is the documented k3s air-gap procedure, fed from the drive:

```bash
./ark restore k3s --from .            # binary to ~/.local/bin/k3s; the images bundle and install.sh are printed
sudo mkdir -p /var/lib/rancher/k3s/agent/images
sudo cp files/k3s/1.37.2+k3s1/linux-aarch64/k3s-airgap-images-arm64.tar.zst /var/lib/rancher/k3s/agent/images/
sudo cp ~/.local/bin/k3s /usr/local/bin/k3s       # (the link's target; cp -L)
INSTALL_K3S_SKIP_DOWNLOAD=true sh files/k3s/1.37.2+k3s1/any/install.sh
sudo k3s kubectl get nodes
```

For the catalog's other images, point k3s at `ark serve` (§9.2) through
`/etc/rancher/k3s/registries.yaml`.

### 8.5 kubeadm against a local registry

```bash
./ark restore kubernetes --from .              # kubeadm, kubelet, kubectl + source
./ark restore cni-plugins --from . && sudo mkdir -p /opt/cni/bin && sudo cp ~/.local/opt/cni-plugins/* /opt/cni/bin/
./ark restore cri-tools --from .
./ark restore containerd --from . && ./ark restore runc --from .   # or nerdctl-full: all of it in one tarball
./ark serve . --bind 127.0.0.1 --port 8780 &   # the registry kubeadm pulls from
cat > kubeadm.yaml <<'EOF'
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: v1.37.2
imageRepository: 127.0.0.1:8780
EOF
sudo kubeadm init --config kubeadm.yaml
```

`registry.k8s.io/kube-apiserver` is served as repo `kube-apiserver`, and
`registry.k8s.io/coredns/coredns` as `coredns/coredns`, which is exactly
the layout kubeadm asks an `imageRepository` for. containerd must be told
that 127.0.0.1:8780 is plain http. In `config.toml`, set
`config_path = "/etc/containerd/certs.d"`, then in
`/etc/containerd/certs.d/127.0.0.1:8780/hosts.toml`:

```toml
server = "http://127.0.0.1:8780"
[host."http://127.0.0.1:8780"]
  capabilities = ["pull", "resolve"]
```

### 8.6 Building from source offline (MinIO, wireguard-tools, git)

```bash
./ark restore go --from .
./ark restore minio --from .                   # ~/src/minio at the pinned commit + the GOPROXY line
cd ~/src/minio && GOFLAGS=-mod=mod GONOSUMDB='*' GOPROXY=file:///media/$USER/ARK/pkgs/gomod/minio-RELEASE.2025-10-15T17-29-55Z/any/goproxy go build -o ~/.local/bin/minio .
./ark restore wireguard-tools --from . && make -C ~/src/wireguard-tools/src && make -C ~/src/wireguard-tools/src install PREFIX=$HOME/.local
./ark restore git --from . && cd ~/src/git && make prefix=$HOME/.local install    # needs a C toolchain + zlib headers
```

### 8.7 Python stacks (vLLM, ansible)

```bash
./ark restore cpython-3.12 --from .
./ark restore vllm --from . --python ~/.local/opt/cpython-3.12/bin/python3
~/.local/opt/vllm-0.33.0/venv/bin/python -c 'import vllm; print(vllm.__version__)'
```

Restore checks that `--python` is the version the wheels were resolved for
(`python = "3.12"` in the catalog), and says so when it is not. The set
itself must have been fetched on a box of this platform running that
python (§3.2); a `tofu+cross-host` set may be missing wheels.

### 8.8 Offline docs

```bash
./ark restore kiwix-tools --from .
kiwix-serve --port 8790 files/devdocs-zim-core/2026-10/any/*.zim &
# http://127.0.0.1:8790 — Python, Go, Rust, Kubernetes, kubectl, Docker, Bash, C, C++, PostgreSQL, git, Zig,
# CMake, nginx, Redis, SQLite, HTTP, JavaScript, TypeScript, Node — the DevDocs sets, searchable, offline
```

This complements beast-docs: beast-ark carries the language and tool
references, and beast-docs carries Apple's and Android's.

### 8.9 Rebuilding a box entirely

The `ubuntu-server` entry is the 26.04.1 server ISO for amd64 and arm64.
Its `SHA256SUMS` is verified, and `SHA256SUMS.gpg` is kept. Write it with
`dd if=… of=/dev/sdX bs=4M conv=fsync`, install, and then follow §8.1.

The DGX Sparks run NVIDIA's DGX OS, which is not redistributable and not
in the ark. Rebuild them from NVIDIA's recovery media, then
[SPARK_HYDRA.md](SPARK_HYDRA.md) §B and §H.

---

## 9. Serving the drive to the tailnet

```bash
./ark serve /Volumes/ARK --tailnet          # binds this box's Tailscale IPv4 only (tailscale ip -4)
./ark serve /Volumes/ARK                    # 127.0.0.1:8780 (the default)
./ark serve /Volumes/ARK --bind 0.0.0.0     # every interface: warns — the LAN can then read the drive
```

**One read-only HTTP server. It serves:**

| path | serves |
|---|---|
| `/` | an index page with the client lines below |
| `/healthz` | |
| `/files/…`, `/git/…`, `/oci/…`, `/pkgs/…` | the stored files, with **Range support** (a client's download resumes; an inverted range answers 416). **Only paths in `ark.lock` are served, and only as regular files inside the drive.** A planted file, the stage, `../`, and a listed file swapped for a symlink (to `~/.ssh`, say) all get 404 |
| `/ark.lock`, `/SHA256SUMS`, `/ARK.md`, `/catalog.toml` | the drive's own records |
| `/v2/…` | a **pull-only OCI registry** answered straight out of the `*.oci.tar` archives (no copy, no daemon): `/v2/_catalog`, `/tags/list`, manifests by tag or by **the pinned digest**, blobs. A platform that was not archived answers `MANIFEST_UNKNOWN: not archived on this drive` |
| `/pypi/simple/` | a PEP 503 index over every stored wheel, each link tagged `#sha256=` from the lock, so pip checks it |
| `/npm/<name>` | packuments built from the stored `package-lock.json`, with tarballs and `integrity` |
| `/goproxy/…` | a GOPROXY over the stored module trees |

Every write method answers **405**. There is no auth: it serves public
software, and the bind address is the boundary. Prefer `--tailnet`, and
note that beast-gate does not front it (it is not an inference path).

It speaks HTTP/1.1 keep-alive, so a pip or go client pulling hundreds of
small files reuses one connection instead of paying a tailnet round trip
per file. An image archive that is missing or unreadable is skipped with
`warning: not serving oci/…` and everything else is still served; run
`verify` to see why.

### 9.1 Clients

```bash
H=100.101.102.103:8780        # the serving box's tailnet IP
# images (docker): add "insecure-registries": ["100.101.102.103:8780"] to /etc/docker/daemon.json, restart, then
docker pull $H/library/postgres:18.7
# pip
pip install --index-url http://$H/pypi/simple/ --trusted-host ${H%:*} vllm==0.33.0
# npm
npm install --registry http://$H/npm/ <package>
# go
GOPROXY=http://$H/goproxy GOSUMDB=off go build ./...     # go.sum still checks every module
# files
curl -fO http://$H/files/go/1.27.4/linux-aarch64/go1.27.4.linux-arm64.tar.gz
curl -fs http://$H/SHA256SUMS | grep go1.27.4.linux-arm64 | sha256sum -c -
```

A Mac's Docker Desktop daemon runs in a VM. There, `127.0.0.1` is the VM,
not the Mac, so pull from the Mac's tailnet IP with that IP in Docker
Desktop's insecure registries. `restore --docker` needs none of this.

### 9.2 k3s, containerd and kind mirrors

**k3s** (`/etc/rancher/k3s/registries.yaml`): every `docker.io` and
`registry.k8s.io` pull goes to the drive first.

```yaml
mirrors:
  docker.io:
    endpoint: ["http://100.101.102.103:8780"]
  registry.k8s.io:
    endpoint: ["http://100.101.102.103:8780"]
```

**containerd**: one `hosts.toml` per upstream registry, under
`/etc/containerd/certs.d/<registry>/` (the shape is in §8.5).

**kind**: pass the same `containerdConfigPatches`, or
`kind load image-archive` on the `restore --docker` output.

A mirror is asked for the repo path *without* the registry host:
`library/postgres`, `kube-apiserver`. That is how `serve` names them.
Two images that share a path on different registries would collide.
There is none in the catalog today.

### 9.3 Trivy, offline

```bash
trivy image --db-repository $H/aquasecurity/trivy-db --java-db-repository $H/aquasecurity/trivy-java-db \
  --insecure  alpine:3.24.4        # then --skip-db-update on later runs
```

The DB tags move daily upstream. The drive holds the digest `lock` saw;
re-lock and re-fetch to refresh it.

---

## 10. Updating pins

Two commands; both need the network, and both are for whoever maintains
the catalog in git.

```bash
./scripts/ark.sh update                 # what is newer upstream: prints the catalog diff, writes NOTHING
./scripts/ark.sh update --only kubernetes,helm --write     # applies it; clears every pin the bump moves
./scripts/ark.sh lock                   # re-derives every cleared commit / sha256 / digest / size from upstream
./scripts/ark.sh lint && python3 -m pytest tests/test_ark.py -q
git diff agents/ark/catalog.toml        # review it like an image-digest bump, then commit
```

### 10.1 What `update` prints (real, 2026-11-15)

```
  vllm: latest release v0.33.0rc3 is a pre-release; left alone
update: nothing to change
```

The first run, before the version filter was tightened, proposed
`kafka 4.4.0 -> show` and `ansible-core 2.22.0 -> 2.22.0b2`. That bad
suggestion is why `update` never writes silently: the diff is the review.
It now skips anything that does not start with a digit, and every
pre-release marker (`rc`, `alpha`, `beta`, `dev`, PEP 440 `b2` / `a1`).
It also never moves a pin backwards.

**What `--write` clears.** Every sha256, size, digest and commit whose
upstream location changes with the version. That is decided by expanding
the templates under the old and the new version, not by looking for the
text `{version}`: jq's URL is `…/download/{tag}/jq-linux-amd64` with
`tag = "jq-{version}"`, and Temurin's goes through `{version_pu}`. Both
move, so both are cleared. A file whose URL does not move keeps its pin.

### 10.2 How each entry finds its update

| `update =` | source |
|---|---|
| `github:owner/repo` | the `releases/latest` redirect (no API, no rate limit). The tag is matched against the entry's `tag` template |
| `github-tag:owner/repo[:regex]` | `git ls-remote --tags`; the highest stable tag that fits the template (nginx: `^1\.30\.`, the stable line) |
| `node-lts` | nodejs.org/dist/index.json, the newest LTS |
| `go` | go.dev/dl/?mode=json, the newest stable |
| `manual` | not checked |

These entries are `manual`, and why:

| entry | why manual |
|---|---|
| cpython-3.12 / 3.14 | python-build-standalone tags are dates: bump `vars` `release=` and `version` together |
| zig | 0.16 on purpose: the repo targets it |
| tensorfold | follows `install-native.sh` |
| llama.cpp | follows `scripts/llama.cpp.ref` |
| sqlite | `sv=` and `year=` encode the version |
| forgejo | releases are on Codeberg |
| swift, postgres, mariadb, rabbitmq, mosquitto, open-webui, searxng, lang-images, base-images, ubuntu-server, alpine-minirootfs, devdocs, devdocs-zim-* | dated or mutable tags. Re-pin with `lock --refresh --only …` |

### 10.3 What `lock` does

`lock` fills only empty fields, unless `--refresh`. It writes one line per
value, and `--dry-run` prints the diff only:

| field | source |
|---|---|
| `commit` | `git ls-remote` (peeled tag, or `HEAD` when there is no tag) |
| `sha256` / `upstream_hash` + `verified` | the checksum file, which must name the file. With no `checksum_url` and a GitHub release URL, GitHub's release API digest for that asset (`upstream-json`); only then `unverified` |
| `size` | HEAD |
| `digest` + `platform_sizes` + `verified = "registry"` | the registry. A HEAD, which Docker Hub does not count against its pull limit, then the manifests |
| `git_size` | the GitHub API |

**A pin that disagrees with upstream is never overwritten.** `lock` prints
`UPSTREAM CHANGED — the catalog pins …, upstream now says …`. Investigate,
and blank the field by hand to accept the new file.

The first full lock of this catalog (2026-11-15) failed
on 9 of its 443 reported lookups. Each one was a real catalog error, now fixed:

- Jaeger's per-platform sums list the tarball's *contents*; the
  release-wide `jaeger-<v>.sha256sum.txt` names the tarballs.
- buildx's `checksums.txt` has no darwin rows.
- Mosquitto tags its images `2.1.2-alpine`.
- The DevDocs image is amd64-only.
- A template bug left Zig's JSON path unexpanded.

### 10.4 `lock --from DRIVE` (offline)

It copies hashes from a drive's `ark.lock` into the catalog:

- for `unverified` files fetched with `--allow-unverified`, marked
  `verified = "tofu"`;
- the measured git pin sizes;
- the package-set sizes (`est_size`).

That turns a first-use hash into a reviewed pin. It is the only way a hash
reaches the catalog from our own download, and the `tofu` mark says so
forever: a later fetch from that catalog records the file as
`tofu-pinned` (checked against the hash, still first-use provenance), and
`verify` keeps naming it.

---

## 11. Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `fetch` exit 3, "needs the network, and OFFLINE=true …" / "ISLAND MODE …" | the box is offline by config or by the island watcher | run it on a connected box; `verify`, `restore` and `serve` work offline |
| `sha256 MISMATCH … REFUSED and deleted` | upstream re-published under the same name, a proxy or CDN altered the bytes, or the download was corrupted in transit | re-run; if it persists, compare with the upstream checksum by hand. **Do not edit the pin to match** without knowing why |
| `the checksum file does not name '<file>'` | the URL template drifted (a renamed asset), or the sums file is per-archive-contents (Jaeger) | fix `checksum_url` / `checksum_name`, then `lock --only <entry>` |
| `UPSTREAM CHANGED` from `lock` | as above, seen at lock time | investigate; blank the field to accept |
| `tag vX is commit A upstream, the catalog pins B — REFUSED` | the tag was moved (force-pushed) | treat it as an incident: compare the two commits; re-pin deliberately |
| `HTTP 404` at `lock` / `fetch` | the version bump changed a file name, or a `vars` build id (Grafana) is stale | fix the template or the var |
| `HTTP 410 Gone` | upstream withdrew the release (MinIO did) | archive the source and modules instead (the `minio` entry shows how) |
| `HTTP 429` from Docker Hub | the anonymous pull limit, at `fetch` (blobs) | wait; or fetch fewer images at a time (`--kinds oci --only …`) |
| `HTTP 401` on a blob | a registry whose challenge gives no new token | an expired token alone is handled (it is renewed and the blob resumes); re-run |
| `fetch` (pip): `this box is …, the set is for …` / `the set is for 3.12` | pip would resolve the closure's markers for the wrong box | fetch that set on a box of its platform with its python (§3.2), or `--allow-cross-pip` knowingly |
| `fetch` (npm): `… resolves to …, not the registry` / `has no integrity` | an `.npmrc`-style redirect, or a lock entry with nothing to check | none needed: refused by design |
| `minisign signature does NOT verify` / `… is by a different key` | a tampered file or signature, or a re-keyed upstream | refuse. If upstream really re-keyed, vendor the new key by review |
| `verify`: `CORRUPT` | bit rot or a bad copy | re-fetch that entry on a connected box (`fetch --only <entry> --recheck`), or copy it from a second drive and `verify` again |
| `verify`: files "that ark.lock does not list" | something was added to the drive by hand, or a fetch was interrupted between writing a file and the lock | they are never served or restored; delete them, or re-fetch |
| `restore`: `is CORRUPT … refusing to restore it` | as for verify | as for verify |
| `restore`: `nothing on this drive for linux-aarch64` | the drive was filled with `--platform` excluding this box | fetch that platform (images keep the platforms already archived, §7.2) |
| `restore`: `archive member … escapes the target` / `archive link … resolves outside the target` | a tarball built to write outside its directory | refuse the drive or the upstream file; nothing was written outside |
| `restore`: `ark.lock names the binary …` / `… outside the install root` / `not a catalog name` | the drive's lock was edited to point restore at your files | use another drive; compare its `ark.lock` with a copy kept elsewhere |
| `serve`: `warning: not serving oci/…` | that archive is missing or truncated | `verify`, then re-fetch it; the rest is served meanwhile |
| `restore` (pip): `wheels are for python 3.12` | the wrong `--python` | `restore cpython-3.12`, then pass its `python3` |
| `docker load` fails on an old docker | before 20.10 | `restore --registry` into `ark serve`'s registry, or skopeo |
| a registry refuses the index on `restore --registry` | distribution v3 validates every index child, and only some platforms were archived | handled: restore pushes a **reduced** index of the kept platforms, says so, and leaves the per-platform digests unchanged |
| `docker pull` from `serve` says `http: server gave HTTP response to HTTPS client` | docker wants TLS for non-localhost registries | `insecure-registries` (§9.1), or front `serve` with the catalog's Caddy for TLS |
| `./ark` on a bare box: `no python >= 3.9 here and no archived CPython for <platform>` | the drive has no CPython for this platform | fetch `cpython-3.14` for it, or install any python 3.9+ |
| `./ark`: `REFUSED: … does not match SHA256SUMS` | the archived CPython is corrupt or tampered | use another drive; nothing was unpacked |
| a `.zst` restore: `needs python >= 3.14 or the zstd binary` | `.tar.zst` needs a decoder | `restore cpython-3.14` first (it has `compression.zstd`), or install zstd |
| `lock` slow or `HTTP 403` from api.github.com | the unauthenticated GitHub API limit (only `git_size` uses it) | `GITHUB_TOKEN=…` or `gh auth login`; or `--no-git-sizes` |

**Checking a kept gpg signature by hand.** Get the publisher's key out of
band and confirm its fingerprint from more than one source. Then:

```bash
gpg --no-default-keyring --keyring ./node.gpg --import nodejs-release-keys.asc
gpgv --keyring ./node.gpg files/node/26.13.1/linux-aarch64/SHASUMS256.txt.sig files/node/26.13.1/linux-aarch64/SHASUMS256.txt
```

To make `fetch` check it from then on, commit the keyring as
`agents/ark/keys/node.gpg` after review.

---

## 12. How it is tested

`tests/test_ark.py` has 48 tests (68 cases with their parameters) and
`tests/test_ark.sh` 14 checks. Both
are hermetic: loopback fakes only. CI runs both: the python file in the
full pytest step, the shell suite as its own step. `tests/run_tests.sh`
runs both too.

- **The real catalog:**
  - it lints clean;
  - every entry has a licence, a pin, and a hash or an explicit
    `unverified`;
  - `tomlsub` parses it exactly as `tomllib` does (3.11+);
  - it covers the required ground on all three platforms;
  - the repo-pin parity holds (llama.cpp, TensorFold version and key, Zig
    vs CI's `ZIG_SHA256`, the compose digests).
- **Negative controls on the lint:**
  - a blank pin, a non-OSI licence, an unknown field, a missing licence;
  - a hash with no provenance;
  - plain http to a public host.
- **Plan math:**
  - per platform;
  - `any` counted once on a multi-platform drive;
  - a Mac counting linux/arm64 image layers;
  - core vs full.
- **The round trip** (shallow and full history):
  - fetch → lock → `SHA256SUMS` → verify → restore;
  - a toy toolchain that runs;
  - a single binary;
  - the git source at its commit;
  - the drive's own `./ark`.
- **Refusals:**
  - a catalog-pin mismatch: deleted, and the rest kept;
  - upstream sums that disagree;
  - a sums file for another name;
  - a moved tag;
  - a registry serving the wrong manifest;
  - an unverified file without `--allow-unverified`.
- **Resume:**
  - a transfer cut halfway resumes with `Range: bytes=N-`;
  - a `.part` from an earlier run is picked up.
- **Verify:** corruption, a missing file and a planted file. `--quick`
  misses the same-size corruption and says so. `--jobs 4` gives the same
  verdict.
- **Extraction** (with and without python 3.12's `data` filter, so the
  3.9.6 path is covered): a link chain built forwards and backwards,
  `../` and absolute names and links, an outbound hardlink — all refused,
  nothing written outside, no outbound link left; ordinary relative links
  and hardlinks in a streamed `.tar.xz` kept.
- **A hostile drive lock:** `bins` naming `../../../ESCAPED`, `..`, an
  outbound tar `bins` (never chmod'ed), an entry `../../t`, a commit
  starting `--`, a tag `-f` or with `..` — all refused before any write.
- **Robustness:** a garbled `.minisig` fails its item and the run goes on
  (and is a BAD_SIGNATURE in `verify`); a registry answering HTML fails
  the image cleanly.
- **Hashing:** a pinned file with a sums file is read once, and a shared
  sums file is downloaded once.
- **Signatures:**
  - Ed25519 matches the RFC 8032 vector;
  - minisign is verified at fetch, refused when bad, and caught by
    `verify` when swapped later;
  - TensorFold-style ed25519 over prefix plus sums, and the same without
    the prefix refused.
- **OCI:**
  - pull by digest through bearer auth and a CDN redirect;
  - **the token never reaches the CDN, stdout, stderr or `ark.lock`**;
  - `verify --deep`;
  - a push to a registry that demands every index child (reduced index);
  - `docker load` of a one-platform tar (a fake docker);
  - a token that expires mid-pull is renewed (two tokens, two 401s, no
    401 per manifest);
  - a second-platform top-up keeps the first platform, re-using its blobs;
  - a push streams layers (never read whole) and fails on a refused tag.
- **Package sets:**
  - pip from one explicit index with `PIP_INDEX_URL`, `PIP_EXTRA_INDEX_URL`
    and `PIP_NO_INDEX` set to something else and ignored; a real wheel
    restored offline into a venv with `--require-hashes` and imported;
  - a set for another platform or python refused, then accepted with
    `--allow-cross-pip` and reported by `verify`;
  - npm (a fake npm) given `--registry` and `/dev/null` configs; a tarball
    with no integrity, off the registry, or not matching, refused.
- **lock / update:**
  - commits, sha256 and sizes from upstream, idempotent;
  - SHA-512 into `upstream_hash`;
  - digests and platform sizes;
  - `lock --from` TOFU, and a later fetch from that catalog still `tofu`;
  - a GitHub release asset pinned from the release API digest;
  - `update` writes nothing without `--write`, and clears the pins it
    bumps, including URLs built through `{tag}` and a var.
- **serve:**
  - Range, and an inverted range → 416;
  - unlisted, stage and traversal requests get 404, writes get 405;
  - a listed file swapped for a symlink to a secret → 404;
  - a missing and a truncated archive are skipped, the rest served;
  - markup in an entry name and the Host header escaped;
  - HTTP/1.1 keep-alive, closed after a refused write;
  - registry manifests by tag, digest and child, blobs, and a non-archived
    platform → 404;
  - the pip index with hashes;
  - ark's own client pulling back through it.
- **Offline:** fetch, lock and update exit 3 without one request; verify
  and restore still work.
- **Shell:**
  - help, lint, plan and list;
  - exit codes 2 and 3;
  - the `OPENBEAST_PYTHON` fallback;
  - the bare-box wrapper: it unpacks a CPython that matches `SHA256SUMS`,
    and refuses a tampered one without unpacking it.

**Real runs, 2026-11-15:**

- **Mac (darwin-arm64, python 3.9 and 3.13).** It fetched `jq`,
  `tensorfold` and `distribution` for all three platforms: 12 items,
  228 MB, 30 s.
  - All TensorFold signatures verified.
  - `verify --deep` was OK.
  - Offline restores of jq and TensorFold ran.
  - The registry image went through `restore --docker`, ran, and was
    removed afterwards.
  - `serve` answered the catalog, the pinned digest and a 405.
- **spark-2 (Linux aarch64, python 3.12, git 2.43).** The run was in a
  throwaway clone that was deleted afterwards.
  - `test_ark.py` 31/31, `test_ark.sh` 14/14.
  - The GNU-only `test_scripts.sh` was 384/0.
  - It fetched `jq` and `zig` for linux-aarch64, with Zig's minisign
    verified, restored them offline, and ran them.

---

## 13. For the local model (network off)

You are GLM on spark-1, or any model working from
[SELF_DEVELOPMENT.md](SELF_DEVELOPMENT.md). With the network off:

- **What works:** `list`, `plan`, `lint`, `verify`, `restore`, `serve`,
  and `lock --from`.
- **What exits 3:** `fetch`, `lock` and `update`. That is correct: say so,
  and do not retry.
- **To answer "is X in the ark, and at what version?"**, run
  `./scripts/ark.sh list --only X`, or `./ark list --from /media/…/ARK`
  for a drive.
- **To restore** something for a task, use `--prefix` and `--src` inside
  your work tree, never the live box's `~/.local`, unless Max asked.
- **To add a catalog entry offline:**
  1. Write the `[[entry]]` and `[[artifact]]` blocks with
     `verified = "unverified"` and empty hashes.
  2. Run `./scripts/ark.sh lint` and `python3 -m pytest tests/test_ark.py -q`.
  3. Leave `lock` to a connected session. **Never invent a sha256, commit
     or digest.** The lint accepts a blank pin only with the explicit mark.

---

## 14. Known limits, and what is next

- **No gpg keyrings vendored yet** (53 signatures are kept but not
  checked). Each publisher's fingerprint has to be confirmed out of band
  before its keyring is committed. Node, Temurin, Rust, CMake, Kafka and
  Ubuntu come first; nginx, meson, LLVM and Swift would then verify their
  `.asc` / `.sig` too.
- **Pip sets must be fetched on their own platform** (§3.2). A drive that
  carries vLLM for both Linux arches needs one pip fetch on each, unless
  `--allow-cross-pip` is accepted knowingly.
- **Git source sizes** in `plan` are upper bounds until a drive is measured
  (`lock --from`).
- **Package-set sizes** (vLLM, ansible, MinIO modules) are unknown until
  first fetched.
- **No `fetch --jobs`.** Fetching is sequential: simple, resumable, and
  polite to Docker Hub. A full drive takes hours, not minutes.
- **`update` cannot see Codeberg releases** (Forgejo, Zig's repo) or
  date-tagged builds. Those entries are `manual`.
- **Not archived:** Linux distro package repositories (apt/dnf mirrors are
  a different tool: `apt-mirror`, `aptly`). Windows builds. Weights (that
  is `bundle.sh` / `model-fetch.sh`).
- **A full-drive fetch has not been run yet.** The plan numbers come from
  upstream HEADs and registry manifests, and the 2026-11-15 smoke run
  covered a sample.
