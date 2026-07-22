import os
import sys


def check_sentinels(target_dir="src"):
    """
    Strict Sentinel Verifier v2.1 (Anchor Fix)
    Requirements:
    1. Every module MUST have '_hydrate_path' signature.
    2. Version MUST be v2.1 (contains 'Anchor Fix').
    """
    print(f"[Strict Sentinel Check] Scanning directory: {target_dir}...")

    missing_files = []
    outdated_files = []
    py_files = []

    # Kiem tra cac file o Root truoc
    root_scripts = ["screener.py", "src/background_sweep.py"]
    for s in root_scripts:
        if os.path.exists(s): py_files.append(s)

    # Kiem tra thu muc src
    for root, _, files in os.walk(target_dir):
        for file in files:
            if file.endswith(".py") and file != "__init__.py" and os.path.join(root, file) not in py_files:
                py_files.append(os.path.join(root, file))

    for file_path in py_files:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            print(f"Warning: Could not read {file_path}: {e}")
            continue

        if "_hydrate_path" not in content:
            missing_files.append(file_path)
        elif "v2.1" not in content and "Anchor Fix" not in content:
            outdated_files.append(file_path)

    if missing_files or outdated_files:
        if missing_files:
            print(f"ERROR: Found {len(missing_files)} files missing Sentinel:")
            for f in missing_files: print(f"   - {f}")
        if outdated_files:
            print(f"WARNING: Found {len(outdated_files)} files on outdated Sentinel (v2.0):")
            for f in outdated_files: print(f"   - {f}")
        return False

    print(f"SUCCESS: All {len(py_files)} modules are HARDENED with Sentinel v2.1.")
    return True

if __name__ == "__main__":
    if not check_sentinels():
        sys.exit(1)
    sys.exit(0)
