"""
test_tauri_config_guard.py — WHY: Tauri v2 sidecar security/config guard.

BOUNDARY: Forbids shell.sidecar/shell.scope in plugins, bans
shell:allow-sidecar in capabilities, ensures catch-up sidecar binaries exist,
and blocks BaseDirectory::Resource in main.rs. A security leak here exposes
the user's machine to arbitrary binary execution via the Tauri shell plugin.
Do NOT add new shell permissions without corresponding assertions here.
"""
import json
import pathlib
import pytest


TAURI_DIR = pathlib.Path(__file__).parents[2] / "frontend" / "src-tauri"


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
