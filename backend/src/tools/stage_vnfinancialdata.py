"""stage_vnfinancialdata.py - Offline staging vnfinancialdata (read-only, không đụng runtime).

WHY: vnfinancialdata (PyPI 0.1.2, dataset v1.0.0) chỉ phủ dữ liệu NĂM 2009-2025,
HSX/HNX, không có publication_date. Dùng làm nguồn ĐỐI CHIẾU offline cho số năm
đã có trong financial_facts.db - KHÔNG import trong runtime (db_core/daily_updater).

Contract:
  - Nguồn: HF dataset thanhnp-uel/vietnam-listed-companies-financial-statements
  - Verify SHA256 theo metadata/parquet_checksums.csv (công khai trên dataset).
  - Ghi staging_manifest.json (sha256 + size + provenance + as_of) sau khi verify.
  - Fail-closed: checksum lệch -> exit 1, không ghi manifest "thành công".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


# ── Sentinel v2.1 (Anchor Fix) ────────────────────────────────────────
def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    for p in (root_path, root_path / "backend", root_path / "backend" / "src"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return root_path


PROJECT_ROOT = _hydrate_path()
STAGING_ROOT = PROJECT_ROOT / "backend" / "data" / "staging" / "vnfinancialdata"

HF_REPO = "thanhnp-uel/vietnam-listed-companies-financial-statements"
BASE_URL = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main/"

PARQUET_FILES = [
    "data/balance_sheet/HNX.parquet",
    "data/balance_sheet/HSX.parquet",
    "data/cash_flow/HNX.parquet",
    "data/cash_flow/HSX.parquet",
    "data/income_statement/HNX.parquet",
    "data/income_statement/HSX.parquet",
]

METADATA_FILES = [
    "metadata/parquet_checksums.csv",
    "metadata/dataset_fingerprint.json",
    "metadata/dataset_manifest.json",
    "metadata/item_dictionary.csv",
]

CHECKSUM_CSV = "metadata/parquet_checksums.csv"
MANIFEST_NAME = "staging_manifest.json"

_UA = "Mozilla/5.0 (ptck-staging; +https://github.com)"


def _sha256_file(path: Path) -> str:
    """SHA256 hex của file (đọc theo chunk, ổn định cho file lớn)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_checksum_path(raw: str) -> str:
    """'processed\\long\\balance_sheet\\HNX.parquet' -> 'data/balance_sheet/HNX.parquet'."""
    norm = raw.replace("\\", "/")
    marker = "/long/"
    if marker in norm:
        norm = "data/" + norm.split(marker, 1)[1]
    return norm


def load_published_checksums(csv_path: Path) -> dict[str, tuple[str, int]]:
    """Parse parquet_checksums.csv -> {rel_path: (sha256, size_bytes)}.

    Raise FileNotFoundError nếu CSV thiếu (fail-closed, không bịa checksum).
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"checksum manifest missing: {csv_path}")
    table: dict[str, tuple[str, int]] = {}
    with open(csv_path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if not lines:
        raise ValueError(f"checksum manifest empty: {csv_path}")
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) != 3:
            raise ValueError(f"malformed checksum row: {ln!r}")
        rel, sha, size = parts[0], parts[1], parts[2]
        table[_normalize_checksum_path(rel)] = (sha.strip(), int(size))
    return table


def verify_staging(staging_dir: Path, manifest: dict[str, tuple[str, int]]) -> dict[str, bool]:
    """Verify từng parquet trong staging_dir khớp manifest (fail-closed)."""
    results: dict[str, bool] = {}
    for rel, (expected_sha, _size) in sorted(manifest.items()):
        p = staging_dir / rel
        if not p.exists():
            results[rel] = False
            continue
        results[rel] = _sha256_file(p) == expected_sha
    return results


def download_file(rel: str, dest_root: Path, timeout: int = 120) -> Path:
    """Tải 1 file từ HF resolve URL về dest_root/rel (tạo thư mục cha)."""
    url = BASE_URL + rel
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
    return dest


def _write_manifest(staging_dir: Path, checksums: dict[str, tuple[str, int]], results: dict[str, bool], as_of: str) -> Path:
    """Ghi staging_manifest.json (chỉ ghi verified=True; lệch -> ghi verified=False + không đánh dấu OK)."""
    manifest_path = staging_dir / MANIFEST_NAME
    payload = {
        "source": {"hf_repo": HF_REPO, "dataset_revision": "v1.0.0"},
        "schema": "vnfinancialdata-long-v1",
        "as_of": as_of,
        "files": [],
        "all_verified": bool(results) and all(results.values()),
    }
    for rel, (sha, size) in sorted(checksums.items()):
        payload["files"].append(
            {
                "rel_path": rel,
                "sha256": sha,
                "size_bytes": size,
                "verified": results.get(rel, False),
            }
        )
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def run(out_dir: Path, dry_run: bool = False, timeout: int = 120) -> int:
    """Chạy staging: tải metadata + parquet, verify SHA256, ghi manifest."""

    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Tải checksum manifest + metadata trước (fail-closed nếu không lấy được)
    checksum_path = out_dir / CHECKSUM_CSV
    if dry_run:
        if not checksum_path.exists():
            print(f"[dry-run] checksum manifest chưa có: {checksum_path}")
            return 1
    else:
        download_file(CHECKSUM_CSV, out_dir, timeout=timeout)
        for rel in METADATA_FILES:
            if rel == CHECKSUM_CSV:
                continue
            download_file(rel, out_dir, timeout=timeout)

    checksums = load_published_checksums(checksum_path)
    print(f"[staging] checksum manifest: {len(checksums)} file parquet")

    # 2. Tải parquet (nếu không phải dry-run)
    if not dry_run:
        for rel in PARQUET_FILES:
            download_file(rel, out_dir, timeout=timeout)
            print(f"[staging] downloaded {rel}")

    # 3. Verify SHA256 (bất kỳ lệch nào -> fail-closed)
    results = verify_staging(out_dir, checksums)
    ok = all(results.values())
    for rel, good in sorted(results.items()):
        status = "OK " if good else "FAIL"
        print(f"[verify] {status} {rel}")
    if not ok:
        print("[staging] FAIL: có file checksum lệch - KHÔNG ghi manifest thành công")
        return 1

    as_of = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_path = _write_manifest(out_dir, checksums, results, as_of)
    print(f"[staging] manifest: {manifest_path} (all_verified=True)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline staging vnfinancialdata")
    parser.add_argument("--out-dir", type=Path, default=STAGING_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="verify file đã có, không tải mới")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    return run(args.out_dir, dry_run=args.dry_run, timeout=args.timeout)


if __name__ == "__main__":
    sys.exit(main())
