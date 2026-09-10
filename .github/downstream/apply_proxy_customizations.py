#!/usr/bin/env python3
"""Re-apply and verify Fastpotify Proxy downstream customizations after upstream merges."""

from pathlib import Path
import re

REPO = "https://github.com/OthinusG/fastpotify-proxy_ver"
RELEASE_API = "https://api.github.com/repos/OthinusG/fastpotify-proxy_ver/releases/latest"
BUNDLE_ID = "com.othinusg.fastpotify.proxy"


def read(path: str) -> str:
    return Path(path).read_text()


def write_if_changed(path: str, text: str) -> None:
    file = Path(path)
    if file.read_text() != text:
        file.write_text(text)


def require(path: str, markers: list[str]) -> None:
    text = read(path)
    missing = [marker for marker in markers if marker not in text]
    if missing:
        joined = "\n  - ".join(missing)
        raise SystemExit(
            f"{path} lost required Fastpotify Proxy customization(s):\n  - {joined}"
        )


# 1. Package metadata: About -> Source code must always point to this fork.
cargo_path = "Cargo.toml"
cargo = read(cargo_path)
package_end = cargo.find("\n[", cargo.find("[package]") + len("[package]"))
if package_end < 0:
    raise SystemExit("Unable to locate the end of [package] in Cargo.toml")
package = cargo[:package_end]
if re.search(r'^repository\s*=\s*"[^"]*"', package, flags=re.M):
    package = re.sub(
        r'^repository\s*=\s*"[^"]*"',
        f'repository = "{REPO}"',
        package,
        count=1,
        flags=re.M,
    )
else:
    package += f'\nrepository = "{REPO}"'
cargo = package + cargo[package_end:]

# Keep the proxy TLS dependency and patched hyper-proxy2 transport present.
rustls_line = 'rustls = { version = "0.23", default-features = false, features = ["ring", "std", "tls12"] }'
if rustls_line not in cargo:
    anchor = 'tokio = { version = "1", features = ["rt-multi-thread", "sync", "time", "macros", "fs"] }'
    if anchor not in cargo:
        raise SystemExit("Unable to restore downstream rustls dependency: Cargo.toml anchor changed")
    cargo = cargo.replace(anchor, rustls_line + "\n" + anchor, 1)

hyper_proxy_line = 'hyper-proxy2 = { git = "https://github.com/siketyan/hyper-proxy2", rev = "2a1a9845f4c9a100c45bf3dc0f1222773d5a33b7" }'
if hyper_proxy_line not in cargo:
    patch_anchor = "[patch.crates-io]"
    if patch_anchor not in cargo:
        raise SystemExit("Unable to restore downstream hyper-proxy2 patch: [patch.crates-io] missing")
    cargo = cargo.replace(patch_anchor, patch_anchor + "\n" + hyper_proxy_line, 1)
write_if_changed(cargo_path, cargo)

# 2. Built-in update checker must always use this fork's Releases endpoint.
updates_path = "src/updates.rs"
updates = read(updates_path)
updates, count = re.subn(
    r'https://api\.github\.com/repos/[^"\s]+/releases/latest',
    RELEASE_API,
    updates,
    count=1,
)
if count != 1:
    raise SystemExit("Unable to locate latest-release API URL in src/updates.rs")
write_if_changed(updates_path, updates)

# 3. Keep the macOS app identity and visible branding separate from upstream.
plist_path = "packaging/macos/Info.plist"
plist = read(plist_path)
for key, value in (
    ("CFBundleName", "Fastpotify Proxy"),
    ("CFBundleDisplayName", "Fastpotify Proxy"),
    ("CFBundleIdentifier", BUNDLE_ID),
):
    pattern = rf'(<key>{re.escape(key)}</key>\s*<string>)[^<]*(</string>)'
    plist, count = re.subn(pattern, rf'\g<1>{value}\g<2>', plist, count=1)
    if count != 1:
        raise SystemExit(f"Unable to restore {key} in {plist_path}")
plist = plist.replace(
    "Fastpotify looks for Spotify Connect speakers on your local network.",
    "Fastpotify Proxy looks for Spotify Connect speakers on your local network.",
)
write_if_changed(plist_path, plist)

# 4. Keep the Proxy section between Appearance and Winamp skins.
settings_ui_path = "src/ui/settings.rs"
ui = read(settings_ui_path)
proxy_marker = '    section(ui, &palette, "Proxy", |ui| {'
appearance_marker = '    section(ui, &palette, "Appearance", |ui| {'
winamp_marker = '    section(ui, &palette, "Winamp skins", |ui| {'

for marker in (proxy_marker, appearance_marker, winamp_marker):
    if marker not in ui:
        raise SystemExit(f"Settings UI marker missing after upstream merge: {marker.strip()}")

proxy_start = ui.index(proxy_marker)
appearance_start = ui.index(appearance_marker)
winamp_start = ui.index(winamp_marker)
if not (appearance_start < proxy_start < winamp_start):
    section_starts = [
        match.start()
        for match in re.finditer(r'^    section\(ui, &palette, "[^"]+", \|ui\| \{', ui, flags=re.M)
    ]
    later = [start for start in section_starts if start > proxy_start]
    if not later:
        raise SystemExit("Unable to locate end of Proxy settings section")
    proxy_end = min(later)
    proxy_block = ui[proxy_start:proxy_end]
    ui = ui[:proxy_start] + ui[proxy_end:]
    winamp_start = ui.index(winamp_marker)
    ui = ui[:winamp_start] + proxy_block + ui[winamp_start:]
write_if_changed(settings_ui_path, ui)

# 5. Verify all functional proxy hooks survived the merge. Do not publish a
# silently degraded build if upstream restructures one of these code paths.
required_markers = {
    "src/settings.rs": [
        "pub proxy_server: Option<String>",
        "pub proxy_port: Option<u16>",
        "pub fn proxy_url(&self)",
    ],
    "src/player.rs": [
        "pub proxy: Option<String>",
        "proxy: config",
    ],
    "src/backend.rs": [
        "reqwest::Proxy::all(proxy)",
    ],
    "src/app.rs": [
        "settings.proxy_url()",
    ],
    "src/main.rs": [
        "settings.proxy_url()",
        "rustls::crypto::ring::default_provider().install_default()",
    ],
    "src/milkdrop.rs": [
        "reqwest::Proxy::all(proxy)",
    ],
    "src/ui/settings.rs": [
        "HTTP proxy server",
        "HTTP proxy port",
        "Proxy status",
        "Restart Fastpotify",
        'env!("CARGO_PKG_REPOSITORY")',
    ],
    "Cargo.toml": [rustls_line, hyper_proxy_line, f'repository = "{REPO}"'],
    "packaging/macos/Info.plist": [BUNDLE_ID, "Fastpotify Proxy"],
    "src/updates.rs": [RELEASE_API],
}
for path, markers in required_markers.items():
    require(path, markers)

print("Fastpotify Proxy downstream customizations are applied and verified.")
