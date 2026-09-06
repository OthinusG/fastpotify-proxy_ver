# Proxy Downstream Technical Specification

Date: 2026-09-06

## Design

1. Extend `Settings` with backward-compatible `proxy_server: Option<String>` and `proxy_port: Option<u16>` fields.
2. Validate and normalize them once through `Settings::proxy_url`. Reject incomplete pairs, zero ports, schemes, paths, whitespace, query/fragment delimiters, and credentials.
3. Carry the normalized URL in `EngineConfig` so the backend HTTP client and librespot `SessionConfig` share one source of truth.
4. Pin `hyper-proxy2` to the maintenance commit that uses rustls 0.23, avoiding the vulnerable rustls 0.22 CONNECT path in its published release. Explicitly select the existing ring provider because that commit's `hyper-rustls` defaults also compile AWS-LC.
5. Pass the same URL to MilkDrop's blocking downloader. Keep zeroconf receiver HTTP direct because those addresses are on the local network.
6. Document the JSON contract and restart behavior in the README and settings reference.

## Automation

- `.github/workflows/sync-upstream-macos.yml` runs on a schedule and manually.
- It fetches `crmne/fastpotify/main`, performs a normal merge, and pushes only when the upstream tip is not already contained in downstream `main`.
- A macOS job builds both Apple architectures, creates the existing app bundle and DMG, then publishes it under a non-`v*` tag keyed by the upstream commit. A non-`v*` tag avoids triggering upstream's all-platform release workflow.
- `concurrency` prevents overlapping syncs. `contents: write` is the only required repository permission.

## Verification

- `cargo fmt --all --check`
- Focused settings and player/backend tests, followed by the feasible checks from `CONTRIBUTING.md`.
- YAML syntax and shell review for the sync workflow.
- `cargo tree`/advisory audit for the activated proxy transport.

## Rollback

Remove the two proxy fields or leave them unset to restore direct networking. Reverting the customization commit removes the feature without changing existing settings files.
