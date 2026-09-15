# OpenBeast v1.3.0 — beast-artifact 🎨

**The answer is a link, not a wall of chat.**

v1.2.0 argued that intelligence also comes from the serving stack — the model
gets smarter when the stack pushes real compiler feedback into its loop.
v1.3.0 takes the other half of that thought: a local model's best work is
often not a paragraph. It is a report, a comparison table, a dashboard, a
small interactive tool. Chat is a terrible container for those, and until now
the only way to get one off the rig was to paste it out of a transcript.

**beast-artifact** gives anything the model renders a durable URL on your
tailnet, private to its publisher by default, versioned, and openable on a
phone.

```
Published "Drive wear, 90 days" → https://beast:8446/a/6f1c2a3e-…  (v1)
```

---

## What it does

Ask for something worth keeping. The model writes a self-contained HTML page,
calls `publish_artifact`, and hands back a link. Publish again with the same
id and **the URL never changes** while a new immutable version is added — the
link you sent someone last week still resolves to what they actually read. A
mobile-first gallery lists everything you own; the viewer adds a version
picker, a theme toggle and a copy-link button.

Scripts get the same thing without the model: `./scripts/artifact.sh publish
page.html`. That is how background agents publish, and how the eval campaign
will publish its verdict tables — a stable id per verdict, so a rerun becomes
version 2 of the same page rather than a new link nobody has.

Two new tools join the MCP/WebUI surface, taking it from 15 to 17:
`publish_artifact` and `list_artifacts`. They are deliberately **not** in the
autonomous runner's 10-tool registry — tool-selection accuracy at 27B degrades
with registry size, and that registry is the one the eval suite measures. The
practical consequence is that `agents/tools.py` is byte-identical to v1.2.0,
so **not one banked eval row is invalidated by this release**.

## Model-authored HTML is treated as hostile, because it is

The page renders in an opaque-origin sandboxed iframe under this repo's first
Content-Security-Policy. No storage, no `fetch`, no downloads, no reaching the
page that frames it, no navigating the top frame. Scripts load only from the
same four CDNs Claude's own artifacts allow, so a page written for one system
renders in the other.

Around that:

- **Publishing reads only the agent's workspace**, through the same hazard,
  regular-file and size guards `read_file` earned in the v1.0 security sprint.
  A page cannot be a wrapper for `/etc/passwd`, a named pipe, or `/dev/zero`.
- **Pages are private to their publisher.** `tailnet` visibility is an
  explicit, owner-only flip; publishing can never widen it as a side effect.
- **Reads require a tailnet identity.** An unlisted login gets a 404, never a
  403, and an anonymous caller gets the same — loopback is not a trust
  boundary against a browser on the same machine.
- **Writes are loopback-only**, proven with a token in a 0600 file that never
  touches a process argument. A phone can view a page and never create,
  change, or delete one.
- Every refusal is byte-identical, so the route table cannot be enumerated.

### How we know

Three adversarial review agents attacked this feature before it shipped, with
instructions to demonstrate rather than argue. They found **twelve confirmed
defects**, including an arbitrary-file-read that published `/etc/passwd` to a
durable URL, an ownership hole that let one operator hijack and re-share
another's private page, a template injection where a page's *title* deleted
the viewer's sandbox attribute, and an unauthenticated 50 MB request that
buffered in memory before any auth check.

Then we attacked the fixes, and that is the part worth reporting honestly:

| Round | Findings | Introduced by the previous fix |
|---|---|---|
| Hostile review | 12 | — |
| Verification | 11 | 6 |
| Verification | 9 | most |

Fixing turned out to be about as defect-prone as building. Two of those
second-round findings were security holes created by the first round's
repairs: an identity alias added to keep older pages reachable became a
credential a stranger could present, and a fallback that was supposed to make
an unattributable page recoverable instead handed it to the rig's operator,
where a second chat account could enumerate and overwrite it. Four tests
written along the way were **vacuous** — one asserted a value equals the same
expression the code evaluates, so it could never fail — and each vacuous test
was sitting next to a live defect.

The third round therefore preferred **deleting** mechanisms to guarding them.
The alias is gone rather than fenced. Publishing no longer deletes any version
directory it did not create in that call, which is a stronger guarantee than
sweeping carefully. Every fix carries a regression test that fails without it,
verified by reverting each one individually.

We are reporting this rather than the tidier version because the tidier
version would suggest a feature that was got right the first time, and the
useful information for anyone running this on their own machine is that a
publish-to-a-URL feature layered onto a system with two identity namespaces
took three rounds to settle.

The complete posture, the authoring rules, and what is deliberately **not** in
v1 (no public-internet sharing, no per-artifact origins, no browser-side
editing, no `localStorage` inside pages) are in
[`docs/BEAST_ARTIFACT.md`](BEAST_ARTIFACT.md).

## Enabling it

```bash
# openbeast.conf
BEAST_ARTIFACT=true
ARTIFACT_OPERATORS=you@github          # who may READ; empty = any identified tailnet login

./stop.sh && ./start.sh
./scripts/setup-tailscale.sh --publish-artifact      # optional: :8446, for phones
```

Off by default. With it off, the tools refuse politely instead of returning a
URL to nothing, and nothing new listens.

## Also in this release

- `stop.sh`, `doctor.sh` and `healthcheck.sh` learned the new service. The
  health check restarts it **by recorded PID, never by pattern** — a
  pattern-matched kill destroyed a live measurement unit during this release's
  own development, and the reasoning is written into the code so it does not
  recur.
- A failure of an opt-in page viewer no longer tears down the language model
  with it.
- `--publish-artifact` now says plainly when an empty operator list means
  every identified login on your tailnet can read every page, instead of
  claiming reads are gated.
- Tool-count prose corrected across 20 files, and `docs/TOOLS.md` explains why
  17 on one surface and 10 on the other is deliberate rather than drift.

## Upgrading

Nothing to do. beast-artifact is off until you turn it on, the eval cache is
untouched, and the 15 pre-existing tools are byte-identical in name, schema
and behavior — verified by dumping both surfaces and diffing them.
