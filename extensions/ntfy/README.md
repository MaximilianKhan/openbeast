# ntfy — push notifications for beast-chat

An opt-in, compose-kind extension: a self-hosted [ntfy](https://ntfy.sh)
server, digest-pinned, bound to `127.0.0.1`, with its message cache and access
database in docker volumes. beast-chat posts to it when a session reaches a
state you care about. The phone's ntfy app subscribes to the topic over your
tailnet.

What a notification carries: the session title (secret-scrubbed; a job whose
title is its own command sends `job <short id>` instead), its new state, and
a link to it in the console. It **never** carries transcript text:
notification services and lock screens leak. The rules, rate limits and
every key: [docs/BEAST_CHAT.md § Push notifications](../../docs/BEAST_CHAT.md#push-notifications).

## Setup

**Status:** the compose file is YAML-checked and its image digest matches
the registry, but the container has not yet been run under this hardening
(`read_only`, `cap_drop: ALL`) on the reference rig. Enable it at a quiet
moment and check `./scripts/doctor.sh` before relying on it.

```bash
./scripts/ext.sh enable ntfy
# openbeast.conf — pick a long topic name; with the default open access the
# topic name is what keeps other tailnet devices off your feed. The stack
# treats the URL as a secret: only beast-chat's process receives it.
#   CHAT_NOTIFY_URL=http://127.0.0.1:3005/openbeast-<long-random-topic>
#   CHAT_NOTIFY_ON=failed,lost,done          # the default
./stop.sh && ./start.sh -d
./scripts/setup-tailscale.sh --publish-ntfy   # :8447 on the tailnet, never public
./scripts/doctor.sh                           # "notifications" rows
```

Then, in the ntfy app on the phone (Android, iOS or desktop), add a
subscription on the server `https://<rig>.<tailnet>.ts.net:8447` to the same
topic.

## Closed networks (`OFFLINE=true`)

The ntfy image travels like the core images: `scripts/bundle.sh build` saves
every extension fragment's pinned image into the bundle, `bundle.sh install`
loads it and rewrites this `compose.yaml` to the loaded content ID (keeping a
`.pre-bundle` copy), and `update.sh --images` re-resolves its pinned tag's
digest on a connected box. Enabled extensions join the same
`docker compose up --pull never` as WebUI and SearXNG, so a missing ntfy
image would stop all three: `./scripts/doctor.sh` FAILs when `OFFLINE=true`,
the extension is enabled and the image line as written does not resolve
(a loaded `repo:tag` under a line that still pins `@sha256:` FAILs too:
compose would not find it). On a box
installed without a bundle, move the image by hand. `docker save`/`load`
drops the registry digest `compose.yaml` pins, so after the load the pinned
line can never resolve: point it at the loaded image's ID, the same rewrite
`bundle.sh install` makes.

```bash
# connected box
docker pull binwiederhier/ntfy:v2.28.0@sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da
docker save -o ntfy.tar binwiederhier/ntfy:v2.28.0@sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da
# closed box
docker load -i ntfy.tar          # prints "Loaded image: …" or "Loaded image ID: sha256:…"
docker image inspect -f '{{.Id}}' <what load printed>
cp extensions/ntfy/compose.yaml extensions/ntfy/compose.yaml.pre-bundle
# then, in extensions/ntfy/compose.yaml:  image: sha256:<that id>
```

A new ntfy *version* is a reviewed edit to the tag in `compose.yaml`;
`update.sh --images` only follows a respin of the same tag.

## Ports

`NTFY_PORT` (default 3005) moves the loopback port. `setup-tailscale.sh
--unpublish-ntfy` takes the tailnet mount down, and `./scripts/setup-tailscale.sh
--status` lists it.

## Locking it down

The default access mode is `read-write` for anyone who can reach the port.
That means loopback, plus every device on your tailnet once it is published:
the same trust boundary as SearXNG. To require a token instead:

```bash
docker exec -it ntfy ntfy user add --role=admin me
docker exec -it ntfy ntfy token add me            # prints tk_...
install -m 600 /dev/null ~/.config/openbeast/ntfy.token
printf '%s' 'tk_...' > ~/.config/openbeast/ntfy.token
# openbeast.conf
#   NTFY_DEFAULT_ACCESS=deny-all
#   CHAT_NOTIFY_TOKEN_FILE=~/.config/openbeast/ntfy.token
./stop.sh && ./start.sh -d
```

The token lives in a 0600 file that beast-chat reads. It never goes in
`openbeast.conf`, in the environment or on a command line. Log the phone app
in as the same user.

## iOS caveat: instant delivery goes through ntfy.sh

Android (without Google services) and desktop clients hold a connection to
your server, so nothing leaves the tailnet. **iOS cannot do that.** Apple only
wakes an app through APNs, so the ntfy iOS app relies on the public ntfy.sh
server to relay a *poll request* for every message. For instant iOS delivery
you must set:

```bash
# openbeast.conf
NTFY_BASE_URL=https://<rig>.<tailnet>.ts.net:8447    # the URL the phone subscribes to
NTFY_UPSTREAM_BASE_URL=https://ntfy.sh
```

With these set, your server sends ntfy.sh a poll request for each
notification. The request names the message ID and a SHA-256 of your topic
URL, not the content. The phone then
fetches the content from your rig over the tailnet. The content does not
leave the tailnet, but the fact and timing of every notification do. That
breaks the closed-network (`OFFLINE=true`) posture, so both settings are
empty by default, and doctor warns when `OFFLINE=true` and an upstream is set.
Without them, iOS shows messages only when the app is opened. Android and
desktop work fully either way.
