"""test_daily_updater_hardening.py — Regression cho IP-ban protection + pipeline fixes.

WHY: 31/07/2026 quét full 1514 mã phát hiện 6 lớp bug mà suite cũ KHÔNG bắt được
(nên "thêm module mới dính bug mà không biết"):
  1. Fixed User-Agent fingerprint -> bị KBS/TCBS/SSI ban IP khi quét toàn thị trường.
  2. Double TextIOWrapper trên sys.stdout.buffer -> "I/O operation on closed file"
     ngẫu nhiên giữa pipeline (GC đóng buffer dùng chung).
  3. `import json` cục bộ trong run_daily_update shadow module-level -> UnboundLocalError.
  4. `MarketBehaviorEngine.scan()` (API không tồn tại) -> AttributeError.
  5. `CompanyHealthV2.analyze_many()` trả dict chứ không phải list -> lỗi `.archetype`.
  6. CSI history append không dedupe (symbol,date) -> trùng record khi chạy lại.

Các test này là static-source + API contract nhẹ (chạy nhanh, không cần network),
để maintainer bắt regression ngay mà không phải chạy full-market 4 phút.

LƯU Ý MÔI TRƯỜNG: `python -m pytest tests/...` (từ backend/) chạy đúng. Chạy từ
project root sẽ ưu tiên `backend/pyproject.toml` → rootdir lệch → capture vỡ
("I/O operation on closed file" ở teardown). Lỗi capture teardown tương tự cũng
xảy ra ở test_bug_regression.py NGAY CẢ KHI CHƯA CÓ thay đổi này (pre-existing):
~20 module trong backend/src có module-level `sys.stdout = io.TextIOWrapper(...)`
(market_snapshot, confidence_layer...) — khi pytest đã capture stdout, import các
module này double-wrap → capture đọc file đã đóng. Đây là class bug GIỐNG hệt
double-wrap đã fix trong daily_updater.py; chưa xử lý hết vì nằm ngoài scope.
"""

import ast
import re
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT

DAILY_UPDATER = PROJECT_ROOT / "backend" / "src" / "daily_updater.py"
VNSTOCK_PROVIDER = PROJECT_ROOT / "backend" / "src" / "providers" / "vnstock_provider.py"
PTCK = PROJECT_ROOT / "ptck.py"
MANIFEST = PROJECT_ROOT / "SYSTEM_MANIFEST.yaml"


def _src(path: Path) -> str:
    # WHY utf-8-sig: daily_updater.py có BOM (U+FEFF) pre-existing; đọc sai encoding
    # sẽ làm ast.parse / string-match sai.
    return path.read_text(encoding="utf-8-sig")


def _compact(src: str) -> str:
    # WHY: ruff format tự wrap tham số xuống nhiều dòng nên assertion kiểu chuỗi
    # literal chính xác sẽ vỡ vì formatting, dù logic thật vẫn đúng. Loại bỏ toàn
    # bộ whitespace để assertion còn kiểm tra "param được khai báo/truyền" mà
    # không phụ thuộc cách trình bày từng dòng.
    return re.sub(r"\s+", "", src)


# ============================================================
# BUG 1: IP-ban — random_agent + throttle + batch-size
# ============================================================
class TestIpBanProtection:
    """Chống ban IP khi quét full-market 1514 mã."""

    def test_trading_uses_random_agent(self):
        """KBS rate-limiter fingerprint theo UA cố định; random_agent=True bắt buộc.

        Trading được đóng gói trong VnstockProvider.price_board() (providers/);
        daily_updater gọi qua provider, không import vnstock trực tiếp.
        """
        provider_src = _src(VNSTOCK_PROVIDER)
        assert "random_agent" in provider_src, (
            "Mất random_agent — KBS sẽ fingerprint UA cố định và ban IP khi quét full-market."
        )
        updater_src = _src(DAILY_UPDATER)
        assert "price_board(active_batch)" in updater_src, "daily_updater phải gọi price_board qua provider."

    def test_update_market_batch_accepts_batch_size_and_throttle(self):
        """update_market_batch phải nhận batch_size + throttle_sec để điều tiết rate."""
        src = _src(DAILY_UPDATER)
        assert "def update_market_batch(" in src
        assert "batch_size: int = 50" in src, "Mất tham số batch_size — payload quá lớn dễ bị chặn."
        assert "throttle_sec: float = 1.8" in src, "Mất tham số throttle_sec — hết cách giãn rate."

    def test_throttle_jitter_scales_with_throttle_sec(self):
        """Delay giữa batch phải dùng throttle_sec (0.6x-1.0x), không hardcode."""
        src = _src(DAILY_UPDATER)
        assert "random.uniform(0.6 * throttle_sec, throttle_sec)" in src, (
            "Delay giữa batch hardcode — không phân tán rate, dễ dính rate-limit."
        )

    def test_run_daily_update_forwards_throttle_params(self):
        """run_daily_update phải truyền batch_size/throttle_sec xuống update_market_batch."""
        src = _src(DAILY_UPDATER)
        assert (
            "def run_daily_update(target_date=None, manifest_path=None, batch_size: int = 50, throttle_sec: float = 1.8):"
        ) in src, "run_daily_update mất tham số chống ban IP."
        assert "update_market_batch(" in _compact(src)
        assert "batch_size=batch_size,throttle_sec=throttle_sec" in _compact(src), (
            "Không truyền throttle xuống update_market_batch — chống ban IP bị vô hiệu."
        )

    def test_main_argparse_has_batch_and_throttle(self):
        """__main__ argparse phải có --batch-size và --throttle để CLI dùng được."""
        compact = _compact(_src(DAILY_UPDATER))
        assert 'parser.add_argument("--batch-size"' in compact
        assert 'parser.add_argument("--throttle"' in compact
        assert "run_daily_update(args.date,args.manifest,batch_size=args.batch_size,throttle_sec=args.throttle)" in compact

    def test_ptck_forwards_cli_flags(self):
        """ptck.py cmd_daily_update phải forward --batch-size/--throttle qua subprocess."""
        src = _src(PTCK)
        assert "batch_size = getattr(args, 'batch_size', 50)" in src
        assert "throttle = getattr(args, 'throttle', 1.8)" in src
        assert 'cmd += ["--batch-size", str(batch_size), "--throttle", str(throttle)]' in src, (
            "ptck.py không forward flags chống ban IP xuống daily_updater."
        )

    def test_ptck_parser_exposes_flags(self):
        """Parser daily-update phải expose --batch-size và --throttle."""
        src = _src(PTCK)
        assert 'p_du.add_argument("--batch-size"' in src
        assert 'p_du.add_argument("--throttle"' in src

    def test_manifest_documents_flags(self):
        """SYSTEM_MANIFEST.yaml phải ghi CLI chính xác (single source of truth)."""
        src = _src(MANIFEST)
        assert "[--batch-size N] [--throttle S]" in src, (
            "SYSTEM_MANIFEST.yaml lệch với CLI thật — vi phạm single source of truth."
        )


# ============================================================
# BUG 2: Double TextIOWrapper trên stdout
# ============================================================
class TestStdoutSingleWrap:
    """WHY: wrap sys.stdout 2 lần tạo 2 TextIOWrapper chung buffer; GC thu hồi
    wrapper cũ -> đóng buffer -> 'I/O operation on closed file' ngẫu nhiên."""

    def test_module_wraps_stdout_exactly_once(self):
        src = _src(DAILY_UPDATER)
        # Chỉ được có đúng 1 chỗ wrap sys.stdout.buffer (module scope).
        assert src.count("TextIOWrapper(sys.stdout.buffer") == 1, (
            f"Phát hiện {src.count('TextIOWrapper(sys.stdout.buffer')} TextIOWrapper — "
            "double-wrap stdout sẽ gây 'I/O operation on closed file' giữa pipeline."
        )

    def test_main_does_not_rewrap_stdout(self):
        """__main__ không được wrap sys.stdout lần nữa (đã wrap ở module scope)."""
        src = _src(DAILY_UPDATER)
        main_block = src.split("if __name__", 1)[1]
        assert "TextIOWrapper" not in main_block, "__main__ re-wrap sys.stdout — gây double-wrap, pipeline chết ngẫu nhiên."

    def test_console_handler_reuses_stdout(self):
        """Console handler phải dùng sys.stdout hiện tại, không wrap riêng."""
        src = _src(DAILY_UPDATER)
        assert "console_handler = logging.StreamHandler(sys.stdout)" in src
        assert "console_handler.stream" not in src, "Console handler wrap stream riêng — double wrapper trên cùng buffer."


# ============================================================
# BUG 3: `import json` cục bộ shadow module-level
# ============================================================
class TestNoLocalJsonShadow:
    """WHY: `import json` trong thân hàm biến json thành local cho cả scope hàm;
    mọi tham chiếu trước dòng import -> UnboundLocalError."""

    def test_run_daily_update_has_no_local_json_import(self):
        tree = ast.parse(_src(DAILY_UPDATER))
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_daily_update":
                target = node
                break
        assert target is not None, "run_daily_update không tồn tại."
        for sub in ast.walk(target):
            if isinstance(sub, ast.Import) and any(a.name == "json" for a in sub.names):
                pytest.fail("`import json` trong run_daily_update shadow module-level json -> UnboundLocalError.")
            if isinstance(sub, ast.ImportFrom) and sub.module == "json":
                pytest.fail("`from json import ...` trong run_daily_update cũng shadow module-level json.")


# ============================================================
# BUG 4+5: API contract sai (scan_multi / analyze_many dict)
# ============================================================
class TestEngineApiContracts:
    """WHY: gọi API không tồn tại hoặc sai kiểu trả về gây AttributeError giữa
    pipeline — suite cũ chỉ test import, không test contract method."""

    def test_market_behavior_uses_scan_multi(self):
        from src.financial.market_behavior_engine import MarketBehaviorEngine

        assert hasattr(MarketBehaviorEngine, "scan_multi"), "MarketBehaviorEngine mất scan_multi."
        assert not hasattr(MarketBehaviorEngine, "scan"), (
            "MarketBehaviorEngine có scan (API cũ) — daily_updater gọi scan_multi, nếu tồn tại "
            "cả 2 dễ gọi nhầm. scan là tên lỗi từng gây AttributeError."
        )
        src = _src(DAILY_UPDATER)
        assert "mb.scan_multi(target_symbols)" in src, (
            "daily_updater gọi scan thay vì scan_multi — AttributeError giữa Governor block."
        )

    def test_company_health_analyze_many_returns_dict(self):
        from src.financial.company_health_v2 import CompanyHealthV2

        result = CompanyHealthV2().analyze_many(["FPT"])
        assert isinstance(result, dict), (
            f"analyze_many trả {type(result).__name__} thay vì dict — daily_updater iterate .values() sẽ hỏng."
        )

    def test_daily_updater_handles_dict_result(self):
        """daily_updater Step 9 phải xử lý dict (filter None) chứ không iterate raw."""
        src = _src(DAILY_UPDATER)
        assert "ch_states.values()" in src and "s is not None" in src, (
            "daily_updater không handle dict trả về của analyze_many — lỗi '.archetype' trên str."
        )


# ============================================================
# BUG 6: CSI history dedupe (symbol,date)
# ============================================================
class TestCsiHistoryDedupe:
    """WHY: chạy daily-update nhiều lần/ngày sẽ append trùng (symbol,date);
    90d window phình to và drift detection đọc sai baseline."""

    def test_step_11a_dedupes_by_symbol_date(self):
        src = _src(DAILY_UPDATER)
        assert 'csi_path = PROJECT_ROOT / "backend" / "data" / "output" / "csi_history.json"' in src
        assert "seen = {}" in src, "Mất dedupe map — re-run sẽ tạo bản ghi trùng (symbol,date)."
        assert 'key = (r.get("symbol"), r.get("date"))' in src, "Mất key dedupe (symbol,date)."
        assert "list(seen.values())" in src

    def test_dedupe_logic_collapses_duplicates(self):
        """Test logic dedupe độc lập (không cần DB): bản ghi cũ + mới cùng (symbol,date)
        -> chỉ giữ bản mới nhất."""
        records = [
            {"symbol": "FPT", "date": "2026-07-31", "p_gain": 0.30},
            {"symbol": "FPT", "date": "2026-07-31", "p_gain": 0.38},
            {"symbol": "ACB", "date": "2026-07-31", "p_gain": 0.33},
            {"symbol": "FPT", "date": "2026-07-30", "p_gain": 0.40},
        ]
        seen = {}
        for r in records:
            key = (r["symbol"], r["date"])
            seen[key] = r
        deduped = list(seen.values())
        assert len(deduped) == 3
        fpt_today = [r for r in deduped if r["symbol"] == "FPT" and r["date"] == "2026-07-31"]
        assert len(fpt_today) == 1 and fpt_today[0]["p_gain"] == 0.38, (
            "Dedupe phải giữ bản ghi MỚI nhất khi trùng (symbol,date)."
        )


# ============================================================
# BUG 7: Per-symbol absorption NoneType .get() (EOD 06/08/2026)
# ============================================================
class TestPerSymbolAbsorptionNullGuard:
    """WHY: 06/08/2026 EOD crash — `ar.get(...)` trên NoneType khi
    PerSymbolAbsorption.analyze() trả None (symbol chưa đủ dữ liệu hấp thụ).
    Absorption result phải được null-safe trước khi gọi .get()."""

    def test_abs_block_guards_none_result(self):
        src = _src(DAILY_UPDATER)
        assert "if not ar:" in src, "Thiếu null-guard cho ar — `ar.get()` trên NoneType crash EOD 06/08/2026."

    def test_abs_block_uses_get_not_subscript(self):
        """Truy cập phase/hdr/sdi/vqa qua .get() để không KeyError khi default result
        thiếu key (VD `_default_result` không có vqa)."""
        src = _src(DAILY_UPDATER)
        assert 'ar.get("phase")' in src, "dùng ar['phase'] — KeyError nếu result thiếu key."
        assert 'ar.get("vqa", {}).get("classification")' in src

    def test_abs_block_keeps_governor_lock_default(self):
        src = _src(DAILY_UPDATER)
        assert 'ar.get("governor_lock", False)' in src, "Mất default False cho governor_lock — NoneType leak vào báo cáo."


# ============================================================
# BUG 8: Prediction Registry price lookup NoneType (BVB 2026-07-16)
# ============================================================
class TestPredictionRegistryPriceLookup:
    """WHY: `[PR] Price lookup fail BVB @ 2026-07-16: float() argument must be a
    string or a real number, not 'NoneType'` — row tồn tại nhưng close = NULL.
    Guard phải kiểm tra row[0] is not None trước float()."""

    PRED_REG = PROJECT_ROOT / "backend" / "src" / "telemetry" / "prediction_registry.py"

    def test_price_lookup_guards_null_close(self):
        src = _src(self.PRED_REG)
        assert "if row and row[0] is not None:" in src, (
            "Mất null-guard cho close NULL — float(None) crash khi BVB chưa có close."
        )

    def test_nearest_price_lookup_guards_null_close(self):
        src = _src(self.PRED_REG)
        assert src.count("if row and row[0] is not None:") == 2, (
            "Cả _get_price_at_date VÀ _get_nearest_price phải guard null close."
        )

    def test_price_lookup_returns_none_on_missing(self):
        """Contract: không có giá -> trả None (không raise), caller phải handle."""
        import src.telemetry.prediction_registry as pr

        assert pr._get_price_at_date("SỐ-KHÔNG-TỒN-TẠI", "2099-01-01") is None
        assert pr._get_nearest_price("SỐ-KHÔNG-TỒN-TẠI", "2099-01-01", before=True) is None
