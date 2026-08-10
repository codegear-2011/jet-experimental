"""
jet._engine

Single source of truth for "should Jet use the Rust core or pure
Python?" -- used by jet.router, jet.server, and jet.response so all
three always agree on which engine is active.

Detection order:
    1. If JET_FORCE_PYTHON is set to a truthy value, always use pure
       Python -- even if `jet_core` is installed. This is the
       developer escape hatch: you can `pip install jet-framework[speed]`
       and still force pure-Python behavior per-run, per-process, or
       per-environment, with zero code changes.
    2. Otherwise, use the Rust core if `jet_core` is importable.
    3. Otherwise, fall back to pure Python automatically.

Usage in your own code (rarely needed -- Jet does this for you):

    from jet._engine import HAS_RUST_CORE, core

    if HAS_RUST_CORE:
        core.Router()
"""

import os

_FORCE_PYTHON = os.environ.get("JET_FORCE_PYTHON", "").strip().lower() in (
    "1", "true", "yes", "on",
)

core = None
HAS_RUST_CORE = False

if not _FORCE_PYTHON:
    try:
        import jet_core as core  # noqa: F401 (re-exported)
        HAS_RUST_CORE = True
    except ImportError:
        core = None
        HAS_RUST_CORE = False


def engine_name():
    """Human-readable engine name, used in the startup banner."""
    if HAS_RUST_CORE:
        return "rust"
    if _FORCE_PYTHON:
        return "python (forced via JET_FORCE_PYTHON)"
    return "python"
