# Project Memory

- Project: Fastpotify
- Upstream: `https://github.com/crmne/fastpotify`
- Stack: Rust 1.95+, egui, Tokio, librespot
- Package manager: Cargo
- Primary directories: `src/`, `docs/`, `packaging/`, `.github/workflows/`
- Local objective: maintain a downstream build that reads proxy host and port from the existing settings file, applies the proxy to all supported outbound app traffic, tracks upstream changes automatically, and publishes a macOS release.
- Preserve upstream's small native-client design, backward-compatible atomic settings, and cross-platform compilation.
- Never store proxy credentials or other secret values in this file.

## Decisions

- 2026-09-06: Use the existing settings and HTTP/client construction paths. Avoid a new dependency unless the pinned stack cannot configure proxy transport directly.
- 2026-09-06: The proxy contract is `proxy_server` plus `proxy_port`, with no scheme or credentials. Both are required to enable proxying; invalid values fail closed at startup.
- 2026-09-06: Internet HTTP traffic, MilkDrop downloads, and librespot use the HTTP proxy. Zeroconf discovery and receiver activation stay direct because they are local-network operations.
- 2026-09-06: Proxy-enabled librespot must use the pinned `hyper-proxy2` maintenance commit with rustls 0.23. The published 0.1.0 release routes CONNECT TLS through rustls-webpki 0.102, which has known certificate-validation advisories. Select rustls's ring provider explicitly because the maintenance commit also enables AWS-LC through `hyper-rustls` defaults.
- 2026-09-06: `.github/workflows/sync-upstream-macos.yml` polls upstream hourly, merges without force-pushing, and releases one ad-hoc-signed universal macOS DMG per detected upstream tip. Merge conflicts require manual resolution.
- 2026-09-07: macOS Actions must explicitly add both Rust targets after toolchain setup because the repository-pinned toolchain, not only the action's requested toolchain, needs the target standard libraries.
