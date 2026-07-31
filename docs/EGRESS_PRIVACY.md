# Egress privacy: obscuring outbound traffic

**Goal:** make OpenBeast's outbound web traffic (SearXNG searches, the `fetch`
tool, model and image pulls) leave from somewhere other than your home or office
IP, without breaking the tailnet.

**Short answer:** use a **Tailscale exit node**. Do not run a second full-tunnel
VPN alongside Tailscale.

---

## Why not NordVPN (or any second full-tunnel VPN)

A commercial VPN client takes the default route and firewalls everything that
isn't its own tunnel, including `tailscale0`. The symptom is specific and
recognisable: **the stack stays perfectly healthy while remote access dies**, so
it looks like OpenBeast broke when the routing table changed underneath it.

It can be made to work. NordVPN's Linux CLI has an allowlist
(`nordvpn allowlist add subnet 100.64.0.0/10`, plus Tailscale's IPv6 ULA range
`fd7a:115c:a1e0::/48`), and some platforms offer split tunnelling. But the
client auto-restarts, updates reset firewall rules, and you discover the
regression mid-session. Two WireGuard stacks competing for one routing table is
a permanent maintenance tax for something Tailscale already does.

There is also nothing to gain on the inbound side. **OpenBeast has no public
inbound surface at all**: every service binds `127.0.0.1`, ingress is
authenticated WireGuard only, and `tailscale funnel` is never used. A VPN cannot
improve on zero.

## The supported approach: a Tailscale exit node

One WireGuard stack does both jobs. Tailnet traffic stays direct between your
devices; everything else egresses through the node you choose. No collision is
possible, because there is only one tunnel.

### Option A: route through a machine you control

On the machine that should be the egress point (a VPS, a box on another
network, or the rig itself):

```bash
# Linux: forwarding must be on, or the node advertises but cannot route
printf 'net.ipv4.ip_forward=1\nnet.ipv6.conf.all.forwarding=1\n' \
  | sudo tee /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf

sudo tailscale up --advertise-exit-node
```

Then **approve it in the Tailscale admin console** (Machines → the node → Edit
route settings → Use as exit node). Advertising alone does nothing until it is
approved.

On the machine whose traffic you want to obscure:

```bash
tailscale exit-node list
sudo tailscale up --exit-node=<node> --exit-node-allow-lan-access
```

`--exit-node-allow-lan-access` keeps your local network reachable (printers,
NAS). Drop it if you want the exit node to carry LAN traffic too.

### Option B: commercial exit IPs, still one stack

Tailscale ships a first-party **Mullvad** integration. You get commercial egress
addresses in many countries, selected as ordinary tailnet exit nodes, governed
by your ACLs rather than by a second app's firewall rules. This is the closest
equivalent to what people run NordVPN for, without the routing conflict.

### Verify it took

```bash
tailscale status | head -3          # should name the exit node in use
curl -s https://api.ipify.org       # should NOT be your ISP address
openbeast-client status             # tailnet must still be healthy
```

That last line is the one that matters. It is exactly the check that fails when
a second full-tunnel VPN is running.

## What this does and does not hide

**Obscured:** every outbound connection the machine makes, which for a rig means
SearXNG's upstream queries, the SSRF-guarded `fetch` tool, model downloads and
container pulls. Your ISP and the sites you reach see the exit node, not you.

**Not obscured, by design:** traffic *between* your own devices. Tailnet packets
go direct (or via DERP), encrypted end to end with WireGuard, and they never
touch the exit node. That is the point: your laptop talking to your rig should
not detour through a third party.

**Not changed at all:** the data-residency promise. Prompts, file contents and
model output still never leave hardware you own. An exit node changes where your
*web* traffic appears to come from. It does not move your inference anywhere.

## Choosing where the egress lives

| Exit node | Effect | Good for |
|---|---|---|
| The rig | Client web traffic appears to come from the rig's network | A laptop on hostile or untrusted Wi-Fi |
| A VPS you own | Everything appears to come from the VPS | A stable, attributable business egress IP |
| Mullvad via Tailscale | Commercial shared IPs, many regions | Not being attributable to your own network |

For a client on public Wi-Fi, pointing the exit node at your own rig is often
the best answer. You get one tunnel, your traffic appears to originate from your
own network, and you were already trusting that machine with your inference.

## Compliance note

If you are doing this to satisfy an audit rather than a preference, read
[`docs/SOC2_READINESS.md`](SOC2_READINESS.md) first. No SOC 2 Trust Services
Criterion requires hiding your IP address, and inserting a commercial VPN
provider adds a subprocessor to your data path that you will then have to
justify and diligence. Egress privacy is a legitimate preference. It is not a
control.
