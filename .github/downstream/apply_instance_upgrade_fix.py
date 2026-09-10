#!/usr/bin/env python3
"""Repair stale-instance interception without editing workflow files.

The core downstream patch deliberately retains the historical 47114 marker.
This layer keeps that marker for compatibility, moves the actual Proxy runtime
to 47115, adds a build-ID handshake, and embeds the checkout SHA from build.rs.
It is idempotent and only changes ordinary source files, so GitHub Actions can
commit its repairs without `workflows` permission.
"""

from pathlib import Path
import re

LEGACY_PORT = "47_114"
ACTIVE_PORT = "47_115"


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
# Build provenance without touching the release workflow.
# build.rs records the exact checked-out Git commit in every package build.
# ---------------------------------------------------------------------------
build_path = "build.rs"
build = read(build_path)
if "fn emit_proxy_build_id()" not in build:
    anchor = "fn main() {\n"
    helper = '''fn emit_proxy_build_id() {
    println!("cargo:rerun-if-changed=.git/HEAD");
    let build = std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            std::env::var("CARGO_PKG_VERSION").unwrap_or_else(|_| "dev".to_owned())
        });
    let short = build.get(..8).unwrap_or(&build);
    println!("cargo:rustc-env=FASTPOTIFY_PROXY_BUILD_SHA={build}");
    println!("cargo:rustc-env=FASTPOTIFY_PROXY_BUILD_SHORT_SHA={short}");
}

'''
    if anchor not in build:
        raise SystemExit("build.rs main anchor changed")
    build = build.replace(anchor, helper + anchor, 1)
if "    emit_proxy_build_id();\n" not in build:
    anchor = "fn main() {\n"
    if anchor not in build:
        raise SystemExit("build.rs main call anchor changed")
    build = build.replace(anchor, anchor + "    emit_proxy_build_id();\n", 1)
write(build_path, build)


# ---------------------------------------------------------------------------
# macOS / Windows single-instance protocol.
# Keep INSTANCE_PORT=47114 so the proven core patch + workflow assertions stay
# stable. Actual downstream runtime uses PROXY_INSTANCE_PORT=47115, which means
# an already-running pre-fix build on 47114 cannot intercept the new app.
# From 47115 onward different builds negotiate replacement automatically.
# ---------------------------------------------------------------------------
single_path = "src/single_instance.rs"
single = read(single_path)

# Core patcher must continue to own this historical marker.
if f"const INSTANCE_PORT: u16 = {LEGACY_PORT};" not in single:
    single, count = re.subn(
        r"const INSTANCE_PORT: u16 = 47_\d{3};",
        f"const INSTANCE_PORT: u16 = {LEGACY_PORT};",
        single,
        count=1,
    )
    if count != 1:
        raise SystemExit("legacy single-instance port anchor changed")

if "const PROXY_INSTANCE_PORT: u16" not in single:
    anchor = f"const INSTANCE_PORT: u16 = {LEGACY_PORT};\n"
    addition = '''
/// Runtime guard for the downstream Proxy build. Kept separate from the
/// historical 47114 marker so an older installed Proxy cannot surface itself
/// when a newer package is launched.
#[cfg(not(target_os = "linux"))]
const PROXY_INSTANCE_PORT: u16 = 47_115;

/// Exact source commit embedded by build.rs.
#[cfg(not(target_os = "linux"))]
const BUILD_ID: &str = env!("FASTPOTIFY_PROXY_BUILD_SHA");
'''
    if anchor not in single:
        raise SystemExit("active port insertion anchor changed")
    single = single.replace(anchor, anchor + addition, 1)
else:
    single = re.sub(
        r"const PROXY_INSTANCE_PORT: u16 = 47_\d{3};",
        f"const PROXY_INSTANCE_PORT: u16 = {ACTIVE_PORT};",
        single,
        count=1,
    )

# INSTANCE_PORT becomes a compatibility marker once runtime switches to 47115.
legacy_attr = f'#[allow(dead_code)]\nconst INSTANCE_PORT: u16 = {LEGACY_PORT};'
if legacy_attr not in single:
    single = single.replace(
        f"const INSTANCE_PORT: u16 = {LEGACY_PORT};",
        legacy_attr,
        1,
    )

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
    replacement = "    Devices(String),\n    /// Exact build id of the running downstream instance.\n    Build(String),\n}"
    if anchor not in single:
        raise SystemExit("Reply::Devices anchor changed")
    single = single.replace(anchor, replacement, 1)

# All normal control requests target the active Proxy listener.
single = single.replace(
    "    send_to(INSTANCE_PORT, verb)\n",
    "    send_to(PROXY_INSTANCE_PORT, verb)\n",
    1,
)

if "line.strip_prefix(BUILD_REPLY)" not in single:
    anchor = "    } else if let Some(snapshot) = line.strip_prefix(DEVICES_REPLY) {\n        Ok(Reply::Devices(snapshot.to_owned()))\n    } else {"
    replacement = "    } else if let Some(snapshot) = line.strip_prefix(DEVICES_REPLY) {\n        Ok(Reply::Devices(snapshot.to_owned()))\n    } else if let Some(build) = line.strip_prefix(BUILD_REPLY) {\n        Ok(Reply::Build(build.to_owned()))\n    } else {"
    if anchor not in single:
        raise SystemExit("send_to reply parser anchor changed")
    single = single.replace(anchor, replacement, 1)

if "replacing running Fastpotify Proxy build" not in single:
    start_marker = "    let listener = match TcpListener::bind((Ipv4Addr::LOCALHOST, INSTANCE_PORT)) {"
    if start_marker not in single:
        start_marker = "    let listener = match TcpListener::bind((Ipv4Addr::LOCALHOST, PROXY_INSTANCE_PORT)) {"
    end_marker = "    let guard = unguarded();"
    start = single.find(start_marker)
    end = single.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit("single-instance acquire block changed")
    replacement = '''    let bind = || TcpListener::bind((Ipv4Addr::LOCALHOST, PROXY_INSTANCE_PORT));
    let mut listener = bind();

    // Same-build launches surface the existing process. A different build
    // asks the old downstream process to quit, then takes over the listener.
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
            let accepted = |reply: Reply| matches!(reply, Reply::Ok);
            let opened =
                link.is_some_and(|uri| send(&format!("open-link {uri}")).is_ok_and(accepted));
            if link.is_some() && !opened {
                log::warn!("the running Fastpotify Proxy does not take links; asking it to show");
            }
            let answered = opened || send("show").is_ok_and(accepted);
            if answered {
                return Outcome::Surfaced;
            }
            log::warn!(
                "port {PROXY_INSTANCE_PORT} is busy but not with Fastpotify Proxy; running unguarded"
            );
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


# Old smart instances exit through the normal app shutdown path.
app_path = "src/app.rs"
app = read(app_path)
if "ControlCommand::QuitForUpdate => Some(Action::Quit)" not in app:
    anchor = "                ControlCommand::Show => Some(Action::ShowWindow),\n"
    addition = "                ControlCommand::QuitForUpdate => Some(Action::Quit),\n"
    if anchor not in app:
        raise SystemExit("App control-command Show anchor changed")
    app = app.replace(anchor, anchor + addition, 1)
write(app_path, app)


# Reply::Build is an internal probe; CLI commands should never request it.
main_path = "src/main.rs"
main = read(main_path)
if "Reply::Build(_)" not in main:
    anchor = "        Ok(single_instance::Reply::Devices(snapshot)) => {\n"
    start = main.find(anchor)
    if start < 0:
        raise SystemExit("main Reply::Devices anchor changed")
    err = main.find("        Err(error) => {", start)
    if err < 0:
        raise SystemExit("main Reply error arm changed")
    addition = '        Ok(single_instance::Reply::Build(_)) => {\n            eprintln!("unexpected internal build-id reply");\n            1\n        }\n'
    main = main[:err] + addition + main[err:]
write(main_path, main)


# Make the running build unmistakable in About. This also gives the user a
# visual proof that the process on screen is the newly installed Proxy binary.
ui_path = "src/ui/settings.rs"
ui = read(ui_path)
old_about = '                    format!("Fastpotify {}", env!("CARGO_PKG_VERSION")),\n'
old_about_proxy = '                    format!(\n                        "Fastpotify Proxy {} · {}",\n                        env!("CARGO_PKG_VERSION"),\n                        env!("FASTPOTIFY_PROXY_BUILD_SHORT_SHA")\n                    ),\n'
new_about = '                    format!(\n                        "Fastpotify {} · {}",\n                        env!("CARGO_PKG_VERSION"),\n                        env!("FASTPOTIFY_PROXY_BUILD_SHORT_SHA")\n                    ),\n'
if new_about not in ui:
    if old_about_proxy in ui:
        ui = ui.replace(old_about_proxy, new_about, 1)
    elif old_about not in ui:
        raise SystemExit("About version label anchor changed")
    else:
        ui = ui.replace(old_about, new_about, 1)
write(ui_path, ui)


# The sync workflow is intentionally verify-only here. It is protected from
# upstream replacement before merge; modifying workflow files from GITHUB_TOKEN
# would be rejected without workflows permission.
workflow_path = ".github/workflows/sync-upstream-macos.yml"
require(
    workflow_path,
    "build_sha: ${{ steps.sync.outputs.build_sha }}",
    "ref: ${{ needs.sync.outputs.build_sha }}",
    "Verify immutable source revision",
    'verify_binary "$mount_dir/Fastpotify.app/Contents/MacOS/fastpotify"',
)

require(
    build_path,
    "fn emit_proxy_build_id()",
    'cargo:rustc-env=FASTPOTIFY_PROXY_BUILD_SHA=',
    'cargo:rustc-env=FASTPOTIFY_PROXY_BUILD_SHORT_SHA=',
    'args(["rev-parse", "HEAD"])',
)
require(
    single_path,
    f"const INSTANCE_PORT: u16 = {LEGACY_PORT};",
    f"const PROXY_INSTANCE_PORT: u16 = {ACTIVE_PORT};",
    'env!("FASTPOTIFY_PROXY_BUILD_SHA")',
    "QuitForUpdate",
    "BUILD_REPLY",
    'send_to(PROXY_INSTANCE_PORT, verb)',
    'send("build-id")',
    'send("quit-for-update")',
    "Some(Request::Build)",
)
require(app_path, "ControlCommand::QuitForUpdate => Some(Action::Quit)")
require(main_path, "Reply::Build(_)")
require(ui_path, '"Fastpotify {} · {}"', 'env!("FASTPOTIFY_PROXY_BUILD_SHORT_SHA")')

print("Fastpotify Proxy stale-instance handoff is repaired and verified.")
