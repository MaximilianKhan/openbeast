# Tailscale and local model access

Read the [cross-platform Tailscale field guide](../skills/tailscale/references/guide.md) for connecting to DeepSeek from a desktop or phone, selecting a rig and device key, diagnosing remote connections, and reaching the inference gate over a disconnected LAN. The guide ships with the skill so slim client checkouts carry it too.

A local agent can load `skill("tailscale")` and search the offline publisher mirror with `platform_docs(query="connection types", source="tailscale")`.
