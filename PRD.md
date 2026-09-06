# Proxy Downstream PRD

Date: 2026-09-06

## Problem

Fastpotify cannot currently reach Spotify and related Internet services on networks that require an HTTP proxy. A maintained downstream build also needs to keep up with upstream `main` and publish a macOS artifact without manually replaying the customization.

## Scope

- Read an optional proxy host and port from the existing `settings.json`.
- Apply that proxy to all outbound Internet HTTP traffic and local Spotify playback.
- Keep local-network receiver discovery and activation direct.
- Automatically merge detected upstream `main` changes into the downstream repository.
- Build and publish an ad-hoc-signed universal macOS DMG for each detected upstream tip.

## Configuration contract

```json
{
  "proxy_server": "127.0.0.1",
  "proxy_port": 7890
}
```

Both fields are optional, but they must be set together. `proxy_server` is a hostname or IP address without a scheme, path, or credentials. The app uses an HTTP proxy and must be restarted after editing the file.

## Acceptance criteria

- Old settings files deserialize with proxying disabled.
- Valid hostname, IPv4, and IPv6 values produce one canonical HTTP proxy URL.
- Partial or unsafe proxy values fail closed without exposing their contents in logs.
- The shared HTTP client and librespot session receive the same proxy.
- MilkDrop pack downloads use the proxy; LAN receiver calls do not.
- Focused unit tests cover compatibility and validation.
- A scheduled/manual GitHub Actions workflow skips unchanged upstream tips and publishes a macOS universal DMG after a successful sync.

## Constraints and risks

- No proxy authentication is stored or supported.
- Upstream merge conflicts stop automation for manual resolution; the workflow must not overwrite downstream history.
- GitHub-hosted macOS builds are ad-hoc signed unless Apple signing secrets are configured.
- The current librespot proxy path uses its existing `hyper-proxy2` transport; dependency advisories must be checked before release.
