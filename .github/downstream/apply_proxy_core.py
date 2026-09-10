#!/usr/bin/env python3
"""Re-apply and verify Fastpotify Proxy downstream customizations after upstream merges.

The patcher is idempotent: running it twice must not create a second change.
It repairs known downstream edits first, then verifies the complete proxy chain.
"""

from pathlib import Path
import re
import subprocess

REPO = "https://github.com/OthinusG/fastpotify-proxy_ver"
RELEASE_API = "https://api.github.com/repos/OthinusG/fastpotify-proxy_ver/releases/latest"
BUNDLE_ID = "com.othinusg.fastpotify.proxy"
INSTANCE_PORT = "47_114"
# Known-good commit containing the proxy core + GUI. It remains reachable in
# this repository's history and is used only as a source of small fragments.
CANONICAL = "cb69210893ecb16ee993fbdea64908409a0c8c11"
README_CANONICAL = "8d2a86c3d7b448e79e637041fe4577155aad2de3"
# Known-good release pipeline that builds the exact post-patch commit, gives
# every asset an immutable source suffix, and verifies the packaged binary.
RELEASE_CANONICAL = "fdbb57d454315a56c345301af066814b151a78b0"


def read(path: str) -> str:
    return Path(path).read_text()


def write(path: str, text: str) -> None:
    file = Path(path)
    if file.read_text() != text:
        file.write_text(text)


def git_file(commit: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def require(path: str, *markers: str) -> None:
    text = read(path)
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(f"{path}: missing downstream markers: {missing}")


def after(text: str, anchor: str, addition: str, marker: str, label: str) -> str:
    if marker in text:
        return text
    if anchor not in text:
        raise SystemExit(f"{label}: anchor changed")
    return text.replace(anchor, anchor + addition, 1)


def before(text: str, anchor: str, addition: str, marker: str, label: str) -> str:
    if marker in text:
        return text
    if anchor not in text:
        raise SystemExit(f"{label}: anchor changed")
    return text.replace(anchor, addition + anchor, 1)


def between(text: str, start: str, end: str) -> str:
    a = text.index(start)
    b = text.index(end, a)
    return text[a:b]


def settings_section_bounds(text: str, marker: str) -> tuple[int, int]:
    start = text.index(marker)
    starts = [
        match.start()
        for match in re.finditer(
            r'^    section\(ui, &palette, "[^"]+", \|ui\| \{', text, flags=re.M
        )
    ]
    later = [candidate for candidate in starts if candidate > start]
    return start, min(later) if later else len(text)


# ---------------------------------------------------------------------------
# Cargo metadata + TLS proxy transport.
# ---------------------------------------------------------------------------
cargo_path = "Cargo.toml"
cargo = read(cargo_path)
package_start = cargo.index("[package]")
package_end = cargo.index("\n[", package_start + 1)
package = cargo[package_start:package_end]
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
cargo = cargo[:package_start] + package + cargo[package_end:]

rustls = 'rustls = { version = "0.23", default-features = false, features = ["ring", "std", "tls12"] }'
cargo = before(
    cargo,
    'tokio = { version = "1", features = ["rt-multi-thread", "sync", "time", "macros", "fs"] }',
    rustls + "\n",
    rustls,
    "Cargo rustls dependency",
)
hyper_proxy = 'hyper-proxy2 = { git = "https://github.com/siketyan/hyper-proxy2", rev = "2a1a9845f4c9a100c45bf3dc0f1222773d5a33b7" }'
cargo = after(
    cargo,
    "[patch.crates-io]\n",
    hyper_proxy + "\n",
    hyper_proxy,
    "Cargo hyper-proxy2 patch",
)
write(cargo_path, cargo)


# ---------------------------------------------------------------------------
# Settings model + proxy URL validation.
# ---------------------------------------------------------------------------
settings_path = "src/settings.rs"
settings = read(settings_path)
canonical_settings = git_file(CANONICAL, settings_path)
field_block = between(
    canonical_settings,
    "    /// HTTP proxy hostname or IP address.",
    "    /// 96, 160, or 320 kbps.",
)
settings = after(
    settings,
    "    pub device_name: String,\n",
    field_block,
    "pub proxy_server: Option<String>",
    "Settings proxy fields",
)
default_block = "            proxy_server: None,\n            proxy_port: None,\n"
settings = after(
    settings,
    '            device_name: "Fastpotify".to_string(),\n',
    default_block,
    "proxy_server: None",
    "Settings proxy defaults",
)
proxy_fn = between(
    canonical_settings,
    "    /// Returns the proxy URL shared by HTTP requests and local playback.",
    "    pub fn remember_search",
)
settings = before(
    settings,
    "    pub fn remember_search",
    proxy_fn,
    "pub fn proxy_url(&self)",
    "Settings::proxy_url",
)
write(settings_path, settings)


# ---------------------------------------------------------------------------
# librespot EngineConfig + SessionConfig.
# ---------------------------------------------------------------------------
player_path = "src/player.rs"
player = read(player_path)
canonical_player = git_file(CANONICAL, player_path)
player = after(
    player,
    "    pub device_name: String,\n",
    "    pub proxy: Option<String>,\n",
    "pub proxy: Option<String>",
    "EngineConfig proxy field",
)
session_proxy = between(
    canonical_player,
    "            proxy: config\n",
    "            ..SessionConfig::default()",
)
player = after(
    player,
    "            autoplay: Some(config.autoplay),\n",
    session_proxy,
    "proxy: config",
    "librespot SessionConfig proxy",
)
# Current player tests construct EngineConfig directly.
if "proxy: None" not in player:
    player = after(
        player,
        '            device_name: "Fastpotify".into(),\n',
        "            proxy: None,\n",
        "proxy: None",
        "EngineConfig test proxy",
    )
write(player_path, player)


# ---------------------------------------------------------------------------
# Backend HTTP client.
# ---------------------------------------------------------------------------
backend_path = "src/backend.rs"
backend = read(backend_path)
if "reqwest::Proxy::all(proxy)" not in backend:
    canonical_backend = git_file(CANONICAL, backend_path)
    wanted = between(
        canonical_backend,
        "        let mut http = reqwest::Client::builder()",
        "        let art = ArtLoader::new",
    )
    start = backend.find("        let http = reqwest::Client::builder()")
    if start < 0:
        start = backend.find("        let mut http = reqwest::Client::builder()")
    end = backend.find("        let art = ArtLoader::new", start)
    if start < 0 or end < 0:
        raise SystemExit("Backend HTTP client: structure changed")
    backend = backend[:start] + wanted + backend[end:]
write(backend_path, backend)


# ---------------------------------------------------------------------------
# App wiring: settings -> engine and MilkDrop.
# ---------------------------------------------------------------------------
app_path = "src/app.rs"
app = read(app_path)
app = after(
    app,
    "        device_name: settings.device_name.trim().to_string(),\n",
    '        proxy: settings.proxy_url().expect("invalid proxy settings"),\n',
    'proxy: settings.proxy_url().expect("invalid proxy settings")',
    "EngineConfig proxy wiring",
)
canonical_app = git_file(CANONICAL, app_path)
if "download_missing(\n                            folder,\n                            self.settings.proxy_url()" not in app:
    wanted = between(
        canonical_app,
        "                        self.winamp.presets.download_missing(\n",
        "                        self.toast(\"Downloading MilkDrop preset packs\")",
    )
    old_start = app.find("                        self.winamp")
    while old_start >= 0 and "download_missing" not in app[old_start:old_start + 220]:
        old_start = app.find("                        self.winamp", old_start + 1)
    old_end = app.find("                        self.toast(\"Downloading MilkDrop preset packs\")", old_start)
    if old_start < 0 or old_end < 0:
        raise SystemExit("MilkDrop automatic download call: structure changed")
    app = app[:old_start] + wanted + app[old_end:]
if "self.winamp.presets.download(\n                        pack," not in app:
    wanted = between(
        canonical_app,
        "                    self.winamp.presets.download(\n",
        "                    self.toast(format!(\"Downloading {} presets\", pack.name));",
    )
    old_start = app.find("                    self.winamp")
    while old_start >= 0 and ".presets" not in app[old_start:old_start + 120]:
        old_start = app.find("                    self.winamp", old_start + 1)
    while old_start >= 0 and "download" not in app[old_start:old_start + 260]:
        old_start = app.find("                    self.winamp", old_start + 1)
    old_end = app.find("                    self.toast(format!(\"Downloading {} presets\", pack.name));", old_start)
    if old_start < 0 or old_end < 0:
        raise SystemExit("MilkDrop manual download call: structure changed")
    app = app[:old_start] + wanted + app[old_end:]
write(app_path, app)


# ---------------------------------------------------------------------------
# Startup TLS provider + proxy validation.
# ---------------------------------------------------------------------------
main_path = "src/main.rs"
main = read(main_path)
main = after(
    main,
    "fn main() -> eframe::Result<()> {\n",
    "    let _ = rustls::crypto::ring::default_provider().install_default();\n",
    "rustls::crypto::ring::default_provider().install_default()",
    "rustls ring provider",
)
canonical_main = git_file(CANONICAL, main_path)
validation = between(
    canonical_main,
    "    if let Err(error) = settings.proxy_url() {\n",
    "    if let Some(name) = cli.device_name",
)
main = after(
    main,
    "    let mut settings = settings::Settings::load(&dirs.settings_file());\n",
    validation,
    "if let Err(error) = settings.proxy_url()",
    "startup proxy validation",
)
write(main_path, main)


# ---------------------------------------------------------------------------
# MilkDrop download functions. Replace only those functions if upstream drops
# their proxy arguments; all unrelated MilkDrop code remains upstream-current.
# ---------------------------------------------------------------------------
milkdrop_path = "src/milkdrop.rs"
milkdrop = read(milkdrop_path)
canonical_milkdrop = git_file(CANONICAL, milkdrop_path)
if "proxy: Option<String>" not in milkdrop:
    wanted = between(
        canonical_milkdrop,
        "    pub fn download(\n",
        "    /// The pack on its way, if one is.",
    )
    start = milkdrop.find("    pub fn download(")
    end = milkdrop.find("    /// The pack on its way, if one is.", start)
    if start < 0 or end < 0:
        raise SystemExit("MilkDrop Presets download functions: structure changed")
    milkdrop = milkdrop[:start] + wanted + milkdrop[end:]
if "proxy: Option<&str>" not in milkdrop or "reqwest::Proxy::all(proxy)" not in milkdrop:
    wanted = between(
        canonical_milkdrop,
        "pub fn fetch_pack(",
        "/// Writes the `.milk` files of a zip into the folder.",
    )
    start = milkdrop.find("pub fn fetch_pack(")
    end = milkdrop.find("/// Writes the `.milk` files of a zip into the folder.", start)
    if start < 0 or end < 0:
        raise SystemExit("MilkDrop fetch_pack: structure changed")
    milkdrop = milkdrop[:start] + wanted + milkdrop[end:]
write(milkdrop_path, milkdrop)


# ---------------------------------------------------------------------------
# Settings GUI + complete application restart. Canonical fragments are copied
# only when missing/partial, then the Proxy section is placed after Appearance.
# ---------------------------------------------------------------------------
ui_path = "src/ui/settings.rs"
ui = read(ui_path)
canonical_ui = git_file(CANONICAL, ui_path)
ui = after(
    ui,
    'const PLAYBACK_DIRTY_ID: &str = "playback-settings-dirty";\n',
    'const PROXY_RESTART_ERROR_ID: &str = "proxy-restart-error";\n',
    "PROXY_RESTART_ERROR_ID",
    "Proxy UI state",
)
helpers = between(
    canonical_ui,
    "fn spawn_restarted_instance() -> Result<(), String> {",
    "pub fn show(app: &mut App, ui: &mut egui::Ui) {",
)
if "fn spawn_restarted_instance() -> Result<(), String>" not in ui:
    ui = before(
        ui,
        "pub fn show(app: &mut App, ui: &mut egui::Ui) {",
        helpers,
        "fn spawn_restarted_instance()",
        "Proxy restart helpers",
    )
proxy_marker = '    section(ui, &palette, "Proxy", |ui| {'
appearance_marker = '    section(ui, &palette, "Appearance", |ui| {'
winamp_marker = '    section(ui, &palette, "Winamp skins", |ui| {'
canonical_proxy_start, canonical_proxy_end = settings_section_bounds(canonical_ui, proxy_marker)
proxy_block = canonical_ui[canonical_proxy_start:canonical_proxy_end]
if proxy_marker not in ui:
    if winamp_marker not in ui:
        raise SystemExit("Proxy GUI: Winamp anchor changed")
    at = ui.index(winamp_marker)
    ui = ui[:at] + proxy_block + ui[at:]
else:
    start, end = settings_section_bounds(ui, proxy_marker)
    block = ui[start:end]
    if any(marker not in block for marker in ("HTTP proxy server", "HTTP proxy port", "Proxy status", "Restart Fastpotify")):
        ui = ui[:start] + proxy_block + ui[end:]
if appearance_marker not in ui or winamp_marker not in ui:
    raise SystemExit("Proxy GUI: Appearance/Winamp anchors changed")
if not (ui.index(appearance_marker) < ui.index(proxy_marker) < ui.index(winamp_marker)):
    start, end = settings_section_bounds(ui, proxy_marker)
    block = ui[start:end]
    ui = ui[:start] + ui[end:]
    at = ui.index(winamp_marker)
    ui = ui[:at] + block + ui[at:]
write(ui_path, ui)


# ---------------------------------------------------------------------------
# Update source, macOS identity, and runtime single-instance identity.
# ---------------------------------------------------------------------------
updates_path = "src/updates.rs"
updates = read(updates_path)
updates, count = re.subn(
    r'https://api\.github\.com/repos/[^"\s]+/releases/latest',
    RELEASE_API,
    updates,
    count=1,
)
if count != 1:
    raise SystemExit("Update API URL: structure changed")
write(updates_path, updates)

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
        raise SystemExit(f"Info.plist {key}: structure changed")
plist = plist.replace(
    "Fastpotify looks for Spotify Connect speakers on your local network.",
    "Fastpotify Proxy looks for Spotify Connect speakers on your local network.",
)
write(plist_path, plist)

single_path = "src/single_instance.rs"
single = read(single_path)
single, count = re.subn(
    r"const INSTANCE_PORT: u16 = 47_\d{3};",
    f"const INSTANCE_PORT: u16 = {INSTANCE_PORT};",
    single,
    count=1,
)
if count != 1:
    raise SystemExit("Single-instance port: structure changed")
write(single_path, single)


# ---------------------------------------------------------------------------
# Downstream documentation.
# ---------------------------------------------------------------------------
readme_path = "README.md"
readme = read(readme_path)
if "## Proxy Configuration" not in readme:
    canonical_readme = git_file(README_CANONICAL, readme_path)
    block = between(canonical_readme, "## Proxy Configuration", "**Spotify, native and fast.**")
    title_end = readme.find("\n", readme.find("# ")) + 1
    readme = readme[:title_end] + block + readme[title_end:]
write(readme_path, readme)

settings_doc = "docs/_reference/settings-and-files.md"
doc = read(settings_doc)
if "| `proxy_server` |" not in doc:
    canonical_doc = git_file(CANONICAL, settings_doc)
    rows = between(canonical_doc, "| `proxy_server` |", "| `bitrate` |")
    anchor = "| `device_name` | `Fastpotify` | Name on Spotify Connect |\n"
    doc = after(doc, anchor, rows, "| `proxy_server` |", "proxy settings docs")
write(settings_doc, doc)

connect_doc = "docs/_reference/how-it-connects.md"
doc = read(connect_doc)
if "When `proxy_server` and `proxy_port` are set" not in doc:
    canonical_doc = git_file(CANONICAL, connect_doc)
    paragraph = between(
        canonical_doc,
        "When `proxy_server` and `proxy_port` are set",
        "Each access-point attempt",
    )
    doc = before(
        doc,
        "Each access-point attempt",
        paragraph,
        "When `proxy_server` and `proxy_port` are set",
        "proxy connection docs",
    )
write(connect_doc, doc)


# ---------------------------------------------------------------------------
# Final verification. Unknown upstream restructures fail here before tagging.
# ---------------------------------------------------------------------------
required = {
    "Cargo.toml": [rustls, hyper_proxy, f'repository = "{REPO}"'],
    settings_path: ["pub proxy_server: Option<String>", "pub proxy_port: Option<u16>", "pub fn proxy_url(&self)"],
    player_path: ["pub proxy: Option<String>", "proxy: config"],
    backend_path: ["reqwest::Proxy::all(proxy)"],
    app_path: ['proxy: settings.proxy_url().expect("invalid proxy settings")', "self.settings.proxy_url().expect"],
    main_path: ["settings.proxy_url()", "rustls::crypto::ring::default_provider().install_default()"],
    milkdrop_path: ["proxy: Option<String>", "proxy: Option<&str>", "reqwest::Proxy::all(proxy)"],
    ui_path: ["HTTP proxy server", "HTTP proxy port", "Proxy status", "Restart Fastpotify", 'env!("CARGO_PKG_REPOSITORY")'],
    updates_path: [RELEASE_API],
    plist_path: [BUNDLE_ID, "Fastpotify Proxy"],
    single_path: [f"const INSTANCE_PORT: u16 = {INSTANCE_PORT};"],
    readme_path: ["## Proxy Configuration"],
    settings_doc: ["| `proxy_server` |", "| `proxy_port` |"],
    connect_doc: ["When `proxy_server` and `proxy_port` are set"],
}
for path, markers in required.items():
    require(path, *markers)
ui = read(ui_path)
if not (ui.index(appearance_marker) < ui.index(proxy_marker) < ui.index(winamp_marker)):
    raise SystemExit("Proxy settings are not between Appearance and Winamp skins")

print("Fastpotify Proxy downstream customizations are repaired and verified.")
