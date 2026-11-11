"""
test_tauri_config_guard.py — WHY: Tauri v2 sidecar security/config guard.

BOUNDARY: Forbids shell.sidecar/shell.scope in plugins, bans
shell:allow-sidecar in capabilities, ensures catch-up sidecar binaries exist,
and blocks BaseDirectory::Resource in main.rs. A security leak here exposes
the user's machine to arbitrary binary execution via the Tauri shell plugin.
Do NOT add new shell permissions without corresponding assertions here.
"""
import importlib.util
import json
import pathlib
import re
import subprocess

import pytest

TAURI_DIR = pathlib.Path(__file__).parents[2] / "frontend" / "src-tauri"
REPO_ROOT = pathlib.Path(__file__).parents[2]

# [WHY]: Tauri bundle.resources keys must stay machine-relative. An absolute
# drive path baked into a tracked config makes a build from one machine point
# at a folder on another drive/machine (the 2026-11-09 E:-drive ghost: the
# installer pipeline rewrote this file with `str(data_template)` and the stale
# absolute key survived the 2026-11 move from E: to D:).
# [INVARIANT]: no `bundle.resources` key/value may match ^[A-Za-z]:[\\/].
_DRIVE_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")

# [WHY]: Build the external-drive needles at runtime instead of writing the
# literal path into this file, otherwise the repository scan below would flag
# its own guard (self-detection) and force a meaningless allowlist.
_EXTERNAL_DRIVE = "E" + ":"
_EXTERNAL_WORKSPACE_NEEDLES = (
    _EXTERNAL_DRIVE + "\\DEV",      # raw Windows path
    _EXTERNAL_DRIVE + "/DEV",       # POSIX-style path
    _EXTERNAL_DRIVE + "\\\\DEV",    # JSON-escaped path (double backslash)
)

# [WHY]: Only source/config/text files can carry a stale workspace path.
# Binary DBs and generated bundles (node_modules/target/dist/build output)
# are excluded so the test stays fast and does not depend on local artifacts.
_TEXT_SUFFIXES = {
    ".py", ".rs", ".json", ".toml", ".yaml", ".yml", ".ps1", ".bat", ".cmd",
    ".sh", ".md", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".ini",
    ".cfg", ".env", ".txt", ".sql", ".spec",
}
_EXCLUDED_DIRS = {
    ".git", "target", "node_modules", ".venv", "dist", "build", "__pycache__",
    "gen", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".kit",
}


def _tracked_text_files():
    """[WHY] Guard only version-controlled files: the guard must be identical on
    every machine and in CI. Local scratch scripts (e.g. ignored debug probes)
    are a different problem — they are reported by the operator, not silently
    turned into a red build that blames the repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available — cannot enumerate tracked files")

    for rel in result.stdout.decode("utf-8", "ignore").split("\0"):
        if not rel:
            continue
        path = REPO_ROOT / rel
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        rel_parts = path.relative_to(REPO_ROOT).parts
        if set(part.lower() for part in rel_parts) & _EXCLUDED_DIRS:
            continue
        if rel_parts[:2] == ("backend", "data"):
            continue
        yield path


def test_tauri_conf_json_validity():
    conf_path = TAURI_DIR / "tauri.conf.json"
    assert conf_path.exists(), "Missing tauri.conf.json"

    with open(conf_path, "r", encoding="utf-8") as f:
        conf = json.load(f)

    plugins = conf.get("plugins", {})
    if "shell" in plugins:
        shell_cfg = plugins["shell"]
        assert "sidecar" not in shell_cfg, "ERROR TAURI v2: 'sidecar' must not be in plugins.shell!"
        assert "scope" not in shell_cfg, "ERROR TAURI v2: 'scope' must not be in plugins.shell!"


def test_capabilities_permissions_validity():
    cap_dir = TAURI_DIR / "capabilities"
    if not cap_dir.exists():
        return

    invalid_permissions = {"shell:allow-sidecar"}

    for cap_file in cap_dir.glob("*.json"):
        with open(cap_file, "r", encoding="utf-8") as f:
            cap_data = json.load(f)
            perms = set()
            for p in cap_data.get("permissions", []):
                if isinstance(p, str):
                    perms.add(p)
                elif isinstance(p, dict) and "identifier" in p:
                    perms.add(p["identifier"])

            forbidden_used = perms.intersection(invalid_permissions)
            assert not forbidden_used, (
                f"File {cap_file.name} contains forbidden/non-existent permission: {forbidden_used}"
            )


def test_erl_catchup_sidecar_binaries_exist():
    app_dir = TAURI_DIR / "binaries"
    binary_standard = app_dir / "uv_backend.exe"
    binary_triple = app_dir / "uv_backend-x86_64-pc-windows-msvc.exe"

    assert app_dir.exists(), "Missing binaries directory in src-tauri"
    assert binary_triple.exists() or binary_standard.exists(), (
        "ERROR ERL: Missing Sidecar executable for Catch-up Scan!"
    )


def test_erl_catchup_path_resolution_guard():
    main_rs = TAURI_DIR / "src" / "main.rs"
    assert main_rs.exists(), "Missing main.rs"

    with open(main_rs, "r", encoding="utf-8") as f:
        code = f.read()

    assert "BaseDirectory::Resource" not in code or "uv_backend" not in code, (
        "ERROR RUST: Forbidden use of BaseDirectory::Resource to spawn Sidecar uv_backend!"
    )


# ---------------------------------------------------------------------------
# Workspace path governance (2026-11-11)
#
# [WHY]: The project was moved from an external drive to `D:\...` in 2026-11.
# A stale absolute path survived inside `bundle.resources` because the build
# pipeline rewrote the tracked config with `str(data_template)`, and
# `src/main.rs` still had the old drive as a discovery fallback. Every build
# or run could silently point at a non-existent (or worse: stale, foreign)
# tree — the exact failure mode the constitution calls "Missing > Fake".
# [INVARIANT]: No tracked source/config may reference the external workspace,
# and every bundle resource key must be relative to the project root.
# ---------------------------------------------------------------------------


def test_bundle_resources_have_no_absolute_drive_path():
    """[WHY] An absolute resource key in a committed config silently re-points
    the installer at a folder outside this checkout; build on a machine that
    still has that drive and the produced bundle ships the wrong tree."""
    conf_path = TAURI_DIR / "tauri.conf.json"
    with open(conf_path, encoding="utf-8") as f:
        conf = json.load(f)

    resources = conf.get("bundle", {}).get("resources", {})
    if isinstance(resources, list):
        keys = list(resources)
        values = []
    else:
        keys = list(resources.keys())
        values = list(resources.values())

    offenders = [
        str(p) for p in (*keys, *values) if _DRIVE_ABSOLUTE.match(str(p))
    ]
    assert not offenders, (
        "tauri.conf.json bundle.resources contains machine-absolute path(s): "
        f"{offenders}. Use a path relative to frontend/src-tauri instead."
    )


def test_relative_resource_key_helper_is_machine_independent(tmp_path):
    """[WHY] The build pipeline used `str(data_template)` as the resources key,
    which is absolute by construction and therefore leaks the build machine's
    drive layout into a version-controlled file. The helper must emit a
    POSIX-style relative path regardless of where the checkout lives."""
    spec = importlib.util.spec_from_file_location(
        "build_windows_installer_under_test",
        REPO_ROOT / "backend" / "build_windows_installer.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tauri_dir = tmp_path / "frontend" / "src-tauri"
    data_template = tmp_path / "backend" / "build" / "installer" / "data_template"
    key = module.relative_resource_key(data_template, tauri_dir)

    assert not _DRIVE_ABSOLUTE.match(key), f"resource key must not be absolute: {key}"
    assert key == "../../backend/build/installer/data_template"


def test_repo_has_no_external_workspace_path():
    """[WHY] A leftover fallback/deploy path makes the runtime silently bind to
    a stale workspace tree if such a folder ever reappears (the 2026-11-09
    E:-drive revival: folder + `.keep` created by an external tool). Guard the
    whole source tree so a future edit cannot re-introduce it."""
    offenders = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle in _EXTERNAL_WORKSPACE_NEEDLES:
            if needle in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)} :: {needle}")

    assert not offenders, (
        "External-drive workspace path found in tracked source/config. "
        "Remove it — the only valid project root is resolved at runtime:\n  "
        + "\n  ".join(offenders)
    )


def test_ghost_workspace_tree_not_resurrected():
    """[WHY] The external-drive checkout was deleted twice (Recycle Bin shows
    2026-11-05 and 2026-11-11 deletions) yet came back with a foreign `.keep`
    placeholder. Fail loudly in CI/dev if the ghost tree exists again, and make
    whoever sees it capture the creator before deleting."""
    ghost = pathlib.Path(_EXTERNAL_DRIVE + "/DEV/opensource_contrib/PTCK_VNSTOCK")
    assert not ghost.exists(), (
        f"Ghost workspace tree resurrected at {ghost}. Do NOT ignore: capture "
        "the creator process (e.g. `fsutil usn readjournal E: csv` as admin) "
        "before deleting, then report which tool recreated it."
    )

