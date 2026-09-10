#!/usr/bin/env python3
"""Idempotently re-apply every Fastpotify Proxy downstream customization."""

from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent

# Keep the large, already-proven proxy patch logic unchanged, then apply the
# build-aware single-instance/update handoff fix. The workflow runs this
# wrapper twice and requires the second final state to be identical.
runpy.run_path(str(HERE / "apply_proxy_core.py"), run_name="__main__")
runpy.run_path(str(HERE / "apply_instance_upgrade_fix.py"), run_name="__main__")
