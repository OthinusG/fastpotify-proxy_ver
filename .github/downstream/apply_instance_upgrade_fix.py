#!/usr/bin/env python3
"""Repair build-aware Fastpotify Proxy instance handoff and release injection.

This layer is intentionally idempotent. It runs after apply_proxy_core.py,
which may restore upstream-shaped source or the known-good base release
workflow. The final downstream state always uses port 47115 and embeds the
exact release commit so an older Proxy process cannot silently surface itself
when a newer build is launched.
"""

from pathlib import Path
import re

PORT = "47_115"
BUILD_ENV = "FASTPOTIFY_PROXY_BUILD_SHA"


def read(path: str) -> str:
    return Path(path).read_text()


def write(path: str, text: str) -> None:
    file = Path(path)
    if file.read_text() != text:
        file.write_text(text)


def require(path: str, *markers: str) -> None:
    text = read(path)
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(f"{path}: missing instance-upgrade markers: {missing}")


# ---------------------------------------------------------------------------
# macOS / Windows single-instance protocol.
# 47115 is a one-time migration away from old downstream builds on 47114.
# From this build onward the listener reports its build id. A newer build asks
# an older build to quit, waits for the port, then becomes the running instance.
# ---------------------------------------------------------------------------
single_path = "src/single_instance.rs"
single = read(single_path)
single, count = re.subn(
    r"const INSTANCE_PORT: u16 = 47_\d{3};",
    f"const INSTANCE_PORT: u16 = {PORT};",
    single,
    count=1,
)
if count != 1:
    raise SystemExit("single-instance port anchor changed")

if "const BUILD_ID: &str" not in single:
    anchor = f"const INSTANCE_PORT: u16 = {PORT};\n"
    addition = "\n/// Exact downstream release commit when CI builds a package.\n#[cfg(not(target_os = \"linux\"))]\nconst BUILD_ID: &str = match option_env!(\"FASTPOTIFY_PROXY_BUILD_SHA\") {\n    Some(id) => id,\n    None => env!(\"CARGO_PKG_VERSION\"),\n};\n"
    if anchor not in single:
        raise SystemExit("BUILD_ID insertion anchor changed")
    single = single.replace(anchor, anchor + addition, 1)

if "QuitForUpdate" not in single:
    anchor = "    Show,\n"
    addition = "    /// Exit so a different downstream build can replace this instance.\n    QuitForUpdate,\n"
    if anchor not in single:
        raise SystemExit("ControlCommand::Show anchor changed")
    single = single.replace(anchor, anchor + addition, 1)

if "const BUILD_REPLY" not in single:
    anchor = 'const DEVICES_REPLY: &str = "fastpotify:devices ";\n'
    addition = 'const BUILD_REPLY: &str = "fastpotify:build ";\n'
    if anchor not in single:
        raise SystemExit("reply constant anchor changed")
    single = single.replace(anchor, anchor + addition, 1)

if "Build(String)" not in single:
    anchor = "    Devices(String),\n}"
    addition = "    Devices(String),\n    /// Exact build id of the running downstream instance.\n    Build(String),\n}"
    if anchor not in single:
        raise SystemExit("Reply::Devices anchor changed")
    single = single.replace(anchor, addition, 1)

if "line.strip_prefix(BUILD_REPLY)" not in single:
    anchor = "    } else if let Some(snapshot) = line.strip_prefix(DEVICES_REPLY) {\n        Ok(Reply::Devices(snapshot.to_owned()))\n    } else {"
    replacement = "    } else if let Some(snapshot) = line.strip_prefix(DEVICES_REPLY) {\n        Ok(Reply::Devices(snapshot.to_owned()))\n    } else if let Some(build) = line.strip_prefix(BUILD_REPLY) {\n        Ok(Reply::Build(build.to_owned()))\n    } else {"
    if anchor not in single:
        raise SystemExit("send_to reply parser anchor changed")
    single = single.replace(anchor, replacement, 1)

if "replacing running Fastpotify Proxy build" not in single:
    start_marker = "    let listener = match TcpListener::bind((Ipv4Addr::LOCALHOST, INSTANCE_PORT)) {"
    end_marker = "    let guard = unguarded();"
    start = single.find(start_marker)
    end = single.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit("single-instance acquire block changed")
    replacement = '''    let bind = || TcpListener::bind((Ipv4Addr::LOCALHOST, INSTANCE_PORT));
    let mut listener = bind();

    // A previous downstream build must not intercept a newly installed build.
    // Same-build launches still behave as normal single-instance `show` calls.
    if listener.is_err() {
        let running_build = match send("build-id") {
            Ok(Reply::Build(build)) => Some(build),
            _ => None,
        };
        if running_build
            .as_deref()
            .is_some_and(|build| build != BUILD_ID)
        {
            log::info!(
                "replacing running Fastpotify Proxy build {} with {}",
                running_build.as_deref().unwrap_or("unknown"),
                BUILD_ID
            );
            let accepted = |reply: Reply| matches!(reply, Reply::Ok);
            if send("quit-for-update").is_ok_and(accepted) {
                for _ in 0..50 {
                    std::thread::sleep(std::time::Duration::from_millis(100));
                    if let Ok(bound) = bind() {
                        listener = Ok(bound);
                        break;
                    }
                }
            }
        }
    }

    let listener = match listener {
        Ok(listener) => listener,
        Err(_) => {
            // Raise the existing instance only if the port answers as
            // Fastpotify. A link goes with the request; an instance from
            // before links does not answer that verb, so a plain show
            // follows and the link is dropped rather than the launch.
            let accepted = |reply: Reply| matches!(reply, Reply::Ok);
            let opened =
                link.is_some_and(|uri| send(&format!("open-link {uri}")).is_ok_and(accepted));
            if link.is_some() && !opened {
                log::warn!("the running Fastpotify does not take links; asking it to show");
            }
            let answered = opened || send("show").is_ok_and(accepted);
            if answered {
                return Outcome::Surfaced;
            }
            log::warn!("port {INSTANCE_PORT} is busy but not with Fastpotify; running unguarded");
            return Outcome::Only(unguarded());
        }
    };

'''
    single = single[:start] + replacement + single[end:]

if "Some(Request::Build)" not in single:
    anchor = "            // Not our client; say nothing and hang up.\n            None => {}"
    addition = "            Some(Request::Build) => {\n                let _ = stream.write_all(format!(\"{BUILD_REPLY}{BUILD_ID}\\n\").as_bytes());\n            }\n"
    if anchor not in single:
        raise SystemExit("serve Request::Build anchor changed")
    single = single.replace(anchor, addition + anchor, 1)

if "    Build,\n}" not in single:
    anchor = "    Devices,\n}"
    replacement = "    Devices,\n    Build,\n}"
    if anchor not in single:
        raise SystemExit("Request::Devices anchor changed")
    single = single.replace(anchor, replacement, 1)

if '("build-id", None) => return Some(Request::Build)' not in single:
    anchor = "    let command = match (verb, argument) {\n"
    addition = '        ("build-id", None) => return Some(Request::Build),\n        ("quit-for-update", None) => ControlCommand::QuitForUpdate,\n'
    if anchor not in single:
        raise SystemExit("request parser anchor changed")
    single = single.replace(anchor, anchor + addition, 1)

write(single_path, single)


# The old instance exits through the ordinary Action::Quit path so settings,
# session state, tray and playback receive the same shutdown handling.
app_path = "src/app.rs"
app = read(app_path)
if "ControlCommand::QuitForUpdate => Some(Action::Quit)" not in app:
    anchor = "                ControlCommand::Show => Some(Action::ShowWindow),\n"
    addition = "                ControlCommand::QuitForUpdate => Some(Action::Quit),\n"
    if anchor not in app:
        raise SystemExit("App control-command Show anchor changed")
    app = app.replace(anchor, anchor + addition, 1)
write(app_path, app)


# ---------------------------------------------------------------------------
# The release build must embed the exact source SHA into every platform binary.
# This is also asserted in the packaged-binary verification, not only in source.
# ---------------------------------------------------------------------------
workflow_path = ".github/workflows/sync-upstream-macos.yml"
workflow = read(workflow_path)
workflow = workflow.replace(
    "const INSTANCE_PORT: u16 = 47_114;",
    f"const INSTANCE_PORT: u16 = {PORT};",
)

mac_a = "          cargo build --release --locked --target aarch64-apple-darwin\n"
mac_x = "          cargo build --release --locked --target x86_64-apple-darwin\n"
mac_a_new = '          FASTPOTIFY_PROXY_BUILD_SHA="${{ needs.sync.outputs.build_sha }}" cargo build --release --locked --target aarch64-apple-darwin\n'
mac_x_new = '          FASTPOTIFY_PROXY_BUILD_SHA="${{ needs.sync.outputs.build_sha }}" cargo build --release --locked --target x86_64-apple-darwin\n'
if mac_a_new not in workflow:
    if mac_a not in workflow:
        raise SystemExit("macOS arm64 cargo build anchor changed")
    workflow = workflow.replace(mac_a, mac_a_new, 1)
if mac_x_new not in workflow:
    if mac_x not in workflow:
        raise SystemExit("macOS x86 cargo build anchor changed")
    workflow = workflow.replace(mac_x, mac_x_new, 1)

linux = "          cargo build --release --locked --target x86_64-unknown-linux-gnu\n"
linux_new = '          FASTPOTIFY_PROXY_BUILD_SHA="${{ needs.sync.outputs.build_sha }}" cargo build --release --locked --target x86_64-unknown-linux-gnu\n'
if linux_new not in workflow:
    if linux not in workflow:
        raise SystemExit("Linux cargo build anchor changed")
    workflow = workflow.replace(linux, linux_new, 1)

windows = "          cargo build --release --locked --target x86_64-pc-windows-msvc\n"
windows_new = '          $env:FASTPOTIFY_PROXY_BUILD_SHA = "${{ needs.sync.outputs.build_sha }}"\n          cargo build --release --locked --target x86_64-pc-windows-msvc\n'
if windows_new not in workflow:
    if windows not in workflow:
        raise SystemExit("Windows cargo build anchor changed")
    workflow = workflow.replace(windows, windows_new, 1)

# Prove the build identity survived optimisation and packaging.
mac_repo = '            grep -Fq "github.com/OthinusG/fastpotify-proxy_ver" "$strings_file"\n'
mac_sha = '            grep -Fq "${{ needs.sync.outputs.build_sha }}" "$strings_file"\n'
if mac_sha not in workflow:
    if mac_repo not in workflow:
        raise SystemExit("macOS binary verification anchor changed")
    workflow = workflow.replace(mac_repo, mac_repo + mac_sha, 1)

win_markers = 'foreach ($marker in @("HTTP proxy server", "Proxy status", "github.com/OthinusG/fastpotify-proxy_ver"))'
win_markers_new = 'foreach ($marker in @("HTTP proxy server", "Proxy status", "github.com/OthinusG/fastpotify-proxy_ver", "${{ needs.sync.outputs.build_sha }}"))'
if win_markers_new not in workflow:
    if win_markers not in workflow:
        raise SystemExit("Windows binary verification anchor changed")
    workflow = workflow.replace(win_markers, win_markers_new, 1)

linux_repo = '          grep -Fq "github.com/OthinusG/fastpotify-proxy_ver" "$RUNNER_TEMP/fastpotify-proxy-strings.txt"\n'
linux_sha = '          grep -Fq "${{ needs.sync.outputs.build_sha }}" "$RUNNER_TEMP/fastpotify-proxy-strings.txt"\n'
if linux_sha not in workflow:
    if linux_repo not in workflow:
        raise SystemExit("Linux binary verification anchor changed")
    workflow = workflow.replace(linux_repo, linux_repo + linux_sha, 1)

write(workflow_path, workflow)


require(
    single_path,
    f"const INSTANCE_PORT: u16 = {PORT};",
    "const BUILD_ID: &str",
    "QuitForUpdate",
    "BUILD_REPLY",
    'send("build-id")',
    'send("quit-for-update")',
    "Some(Request::Build)",
)
require(app_path, "ControlCommand::QuitForUpdate => Some(Action::Quit)")
require(
    workflow_path,
    f"const INSTANCE_PORT: u16 = {PORT};",
    'FASTPOTIFY_PROXY_BUILD_SHA="${{ needs.sync.outputs.build_sha }}" cargo build --release --locked --target aarch64-apple-darwin',
    '$env:FASTPOTIFY_PROXY_BUILD_SHA = "${{ needs.sync.outputs.build_sha }}"',
    'FASTPOTIFY_PROXY_BUILD_SHA="${{ needs.sync.outputs.build_sha }}" cargo build --release --locked --target x86_64-unknown-linux-gnu',
    'grep -Fq "${{ needs.sync.outputs.build_sha }}" "$strings_file"',
)

print("Fastpotify Proxy build-aware instance handoff is repaired and verified.")
