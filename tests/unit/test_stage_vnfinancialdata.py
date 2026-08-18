"""TDD tests - stage_vnfinancialdata.py (offline staging, không đụng runtime).

WHY: vnfinancialdata chỉ phủ dữ liệu NĂM (2009-2025), HSX/HNX, không có
publication_date. Script này chỉ tải Parquet về backend/data/staging/ + verify
SHA256 theo checksum công khai của dataset. Fail-closed: checksum sai -> lỗi.
"""

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from src.tools.stage_vnfinancialdata import (
    HF_REPO,
    METADATA_FILES,
    PARQUET_FILES,
    _sha256_file,
    load_published_checksums,
    verify_staging,
)


# ===================================================================
# TEST 1: Danh sách file Parquet + metadata
# ===================================================================
class TestFileRegistry:
    def test_six_parquet_files(self):
        assert len(PARQUET_FILES) == 6
        assert "data/balance_sheet/HNX.parquet" in PARQUET_FILES
        assert "data/income_statement/HSX.parquet" in PARQUET_FILES

    def test_metadata_includes_checksum_manifest(self):
        assert "metadata/parquet_checksums.csv" in METADATA_FILES
        assert "metadata/dataset_fingerprint.json" in METADATA_FILES

    def test_hf_repo_correct(self):
        assert HF_REPO == "thanhnp-uel/vietnam-listed-companies-financial-statements"


# ===================================================================
# TEST 2: SHA256 file
# ===================================================================
class TestSha256:
    def test_known_bytes(self, tmp_path):
        f = tmp_path / "x.bin"
        f.write_bytes(b"hello world")
        assert _sha256_file(f) == hashlib.sha256(b"hello world").hexdigest()


# ===================================================================
# TEST 3: Parse published checksum CSV
# ===================================================================
class TestParseChecksums:
    def test_parse_manifest(self, tmp_path):
        csv = tmp_path / "parquet_checksums.csv"
        csv.write_text(
            "file,sha256,size_bytes\n"
            "processed\\long\\balance_sheet\\HNX.parquet,"
            "fbf6c8a2d97b134f942383950ff3598fb778a65fd146b963dfe61d76d83906f7,1704136\n"
        )
        table = load_published_checksums(csv)
        assert table["data/balance_sheet/HNX.parquet"] == (
            "fbf6c8a2d97b134f942383950ff3598fb778a65fd146b963dfe61d76d83906f7",
            1704136,
        )

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_published_checksums(tmp_path / "no_such.csv")


# ===================================================================
# TEST 4: Verify staging (fail-closed, không bịa số)
# ===================================================================
class TestVerifyStaging:
    def _write(self, d: Path, rel: str, data: bytes):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_all_match_passes(self, tmp_path):
        data = b"parquet-content-A"
        self._write(tmp_path, "data/balance_sheet/HNX.parquet", data)
        manifest = {
            "data/balance_sheet/HNX.parquet": (
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
        }
        results = verify_staging(tmp_path, manifest)
        assert results["data/balance_sheet/HNX.parquet"] is True

    def test_checksum_mismatch_fails(self, tmp_path):
        data = b"parquet-content-B"
        self._write(tmp_path, "data/balance_sheet/HSX.parquet", data)
        manifest = {
            "data/balance_sheet/HSX.parquet": ("0" * 64, len(data)),
        }
        results = verify_staging(tmp_path, manifest)
        assert results["data/balance_sheet/HSX.parquet"] is False

    def test_missing_file_fails(self, tmp_path):
        manifest = {"data/balance_sheet/HNX.parquet": ("0" * 64, 1)}
        results = verify_staging(tmp_path, manifest)
        assert results["data/balance_sheet/HNX.parquet"] is False

    def test_metadata_file_not_required_for_parquet_verify(self, tmp_path):
        # metadata (checksum csv/fingerprint) tải từ HF, KHÔNG thuộc verify parquet
        data = b"x"
        self._write(tmp_path, "data/cash_flow/HNX.parquet", data)
        manifest = {"data/cash_flow/HNX.parquet": (hashlib.sha256(data).hexdigest(), 1)}
        results = verify_staging(tmp_path, manifest)
        assert results["data/cash_flow/HNX.parquet"] is True


# ===================================================================
# TEST 5: Download file từ HF resolve URL (mock HTTP, fail-closed)
# ===================================================================
class TestDownloadFile:
    def test_download_writes_bytes(self, tmp_path):
        from src.tools.stage_vnfinancialdata import BASE_URL, download_file

        class FakeResp:
            def __init__(self, data: bytes):
                self._data = data
                self._offset = 0

            def read(self, n: int = -1):
                if self._offset >= len(self._data):
                    return b""
                chunk = self._data[self._offset : self._offset + n]
                self._offset += len(chunk)
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        fake = FakeResp(b"PARQUET-BYTES")
        with patch("urllib.request.urlopen", return_value=fake) as mock_uo:
            dest = download_file("data/balance_sheet/HNX.parquet", tmp_path, timeout=30)
        assert (
            mock_uo.call_args[0][0].full_url
            == BASE_URL + "data/balance_sheet/HNX.parquet"
        )
        assert dest.read_bytes() == b"PARQUET-BYTES"
        assert dest.parent.is_dir()

    def test_download_http_error_raises(self, tmp_path):
        from urllib.error import HTTPError

        from src.tools.stage_vnfinancialdata import download_file

        with patch(
            "urllib.request.urlopen",
            side_effect=HTTPError("url", 404, "Not Found", {}, None),
        ):
            with pytest.raises(HTTPError):
                download_file("data/balance_sheet/HNX.parquet", tmp_path)


# ===================================================================
# TEST 6: Normalize checksum path (processed\long\... -> data/...)
# ===================================================================
class TestNormalizeChecksumPath:
    def test_backslash_long_style(self):
        from src.tools.stage_vnfinancialdata import _normalize_checksum_path

        assert (
            _normalize_checksum_path(r"processed\long\balance_sheet\HNX.parquet")
            == "data/balance_sheet/HNX.parquet"
        )

    def test_forward_slash_style(self):
        from src.tools.stage_vnfinancialdata import _normalize_checksum_path

        assert (
            _normalize_checksum_path("processed/long/income_statement/HSX.parquet")
            == "data/income_statement/HSX.parquet"
        )


# ===================================================================
# TEST 7: run() dry-run fail-closed khi chưa có checksum manifest
# ===================================================================
class TestRunDryRun:
    def test_dry_run_without_manifest_returns_1(self, tmp_path):
        from src.tools.stage_vnfinancialdata import run

        assert run(tmp_path, dry_run=True) == 1

    def test_dry_run_with_manifest_verifies(self, tmp_path):
        import hashlib as _hashlib

        from src.tools.stage_vnfinancialdata import CHECKSUM_CSV, run

        csv_dir = tmp_path / CHECKSUM_CSV.split("/")[0]
        csv_dir.mkdir(parents=True, exist_ok=True)
        data = b"parquet-content"
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / "balance_sheet").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / "balance_sheet" / "HNX.parquet").write_bytes(data)
        checksum_csv = tmp_path / CHECKSUM_CSV
        checksum_csv.write_text(
            "file,sha256,size_bytes\n"
            "processed\\long\\balance_sheet\\HNX.parquet,"
            f"{_hashlib.sha256(data).hexdigest()},15\n"
        )
        assert run(tmp_path, dry_run=True) == 0
