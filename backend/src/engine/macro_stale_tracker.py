"""
macro_stale_tracker.py — Layer 1: Phát hiện & Theo dõi dữ liệu vĩ mô cũ

Kiến trúc:
  t_i = Δt giữa ngày hiện tại và MAX(date) trong macro_history (SQL).
  Phân tầng: < 7d (NORMAL), 7-30d (WARNING), 30-60d (TERMINAL), ≥ 60d (EVICT).

Layer 2: Tái phân bổ trọng số L1 khống chế trần.
  w_i_tilde = w_i * max(0, 1 - t_i / 30)
  Nếu Σ w_i_tilde = 0 → Veto khẩn cấp.
  w_i_final = L1_norm(cap_0.50(w_i_tilde / Σ w_i_tilde))

Layer 3: Tích hợp vào Confidence Layer & Decision Guard.
  fresh_ratio = Σ I(t_i < 7) / N
  Nếu fresh_ratio < 0.50 hoặc ≥ 50% TERMINAL → DỪNG NGOÀI.

Warm-up:
  Biến TERMINAL quay lại → yêu cầu 5 ngày dữ liệu liên tục trước khi phục hồi weight.
"""

# WHY: Module này tồn tại vì dữ liệu vĩ mô fetch theo chu kỳ ngày/nguồn bên thứ ba có thể
# chết âm thầm — nếu không phát hiện độ cũ (staleness), Decision Guard vẫn dùng trọng số
# cũ và đưa quyết định dựa trên dữ liệu hết hạn. Nó nằm tách ở Layer 1 để mọi tầng cao hơn
# (confidence, decision guard, governor) dùng chung một nguồn trọng số thống nhất thay vì
# mỗi nơi tự đoán.
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Trọng số cơ sở cho 24 biến vĩ mô ──
# WHY: Lãi suất US (0.26 tổng) và DXY (0.08) được đặt cao nhất vì là kênh dẫn dắt dòng
# vốn toàn cầu; KOSPI/TAIEX/SHENZHEN phản ánh Asia rotation — dòng vốn thường rút khỏi
# VN sau khi rút khỏi các thị trường này trước. Tổng = 1.0 để normalize dễ.
# Tổng = 1.0. Nhóm quan trọng nhất: lãi suất US + DXY + KOSPI/TAIEX (Asia rotation)
MACRO_BASE_WEIGHTS: dict[str, float] = {
    "INTERBANK_ON": 0.10,
    "INTERBANK_1W": 0.08,
    "INTERBANK_2W": 0.06,
    "INTERBANK_1M": 0.05,
    "US10Y": 0.08,
    "US2Y": 0.06,
    "US5Y": 0.04,
    "US30Y": 0.03,
    "DXY": 0.08,
    "KOSPI": 0.05,
    "TAIEX": 0.04,
    "SHENZHEN": 0.03,
    "SH_COMP": 0.03,
    "HANG_SENG": 0.03,
    "GOLD_XAU": 0.04,
    "XAGUSD": 0.02,
    "BRENT_OIL": 0.03,
    "WTI_OIL": 0.02,
    "COPPER_HG": 0.02,
    "BTC": 0.02,
    "VIX": 0.03,
    "SP500": 0.03,
    "NASDAQ": 0.03,
    "USD_VND": 0.03,
}

# WHY: Phân tầng theo chu kỳ cập nhật thực tế của nguồn dữ liệu — nhiều nguồn vĩ mô chỉ
# công bố hàng tuần, nên <7 ngày = NORMAL, tới 30 ngày vẫn còn ý nghĩa (WARNING), trên 60
# ngày dữ liệu coi như vô dụng (EVICT, bị loại khỏi mọi tính toán).
# Phân tầng (ngày)
TIER_NORMAL = 7  # < 7d
TIER_WARNING = 30  # 7-30d
TIER_TERMINAL = 60  # 30-60d
# ≥ 60d = EVICT

# WHY: WARMUP_DAYS = 5 — sau khi biến quay lại cần ~1 tuần phiên liên tục để chứng minh
# nguồn đã ổn định trước khi khôi phục toàn bộ trọng số, tránh tin vào 1-2 ngày dữ liệu lẻ.
# WEIGHT_CAP = 0.50 — một biến không được chiếm quá nửa tổng, đề phòng normalize bị méo
# khi phần lớn biến chết chỉ còn lại 1-2 biến sống.
WARMUP_DAYS = 5  # số ngày liên tục cần để phục hồi
WEIGHT_CAP = 0.50  # trần trọng số đơn lẻ
FRESH_RATIO_VETO = 0.50
TERMINAL_RATIO_VETO = 0.50


class StaleTracker:
    """Singleton theo dõi độ tươi của dữ liệu vĩ mô.

    Usage:
        tracker = StaleTracker.get_instance()
        state = tracker.update(db_path="...", today="2026-07-22")
        weights = state["weights"]  # per-variable final weights
    """

    _instance: StaleTracker | None = None

    def __init__(self, db_path: str | None = None, state_path: str | None = None):
        self.db_path = db_path or ""
        self.state_path = state_path or ""

        # Warm-up counter: {variable: consecutive_fresh_days}
        self._warmup: dict[str, int] = {}

    @classmethod
    def get_instance(cls, db_path: str | None = None, state_path: str | None = None) -> StaleTracker:
        if cls._instance is None:
            cls._instance = cls(db_path=db_path or "", state_path=state_path or "")
        if db_path:
            cls._instance.db_path = db_path
        if state_path:
            cls._instance.state_path = state_path
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    # ── Public API ──

    def update(self, db_path: str | None = None, today: str | None = None) -> dict:
        """Query macro_history, compute t_i, tiers, weights, warm-up.

        Returns dict với:
          - variables: {name: {t_i, tier, base_weight, final_weight}}
          - fresh_ratio, terminal_ratio, veto, weights (per-variable dict)
        """
        if db_path:
            self.db_path = db_path
        if today is None:
            today = datetime.now().strftime("%Y-%m-%d")

        # Load warm-up state
        self._load_warmup()

        import sqlite3

        conn = sqlite3.connect(self.db_path)
        today_dt = datetime.strptime(today, "%Y-%m-%d")

        variables: dict = {}
        for name, base_w in MACRO_BASE_WEIGHTS.items():
            row = conn.execute(
                "SELECT MAX(date) FROM macro_history WHERE variable = ?",
                (name,),
            ).fetchone()
            last_date = row[0] if row and row[0] else None

            if last_date is None:
                # WHY: 999 là sentinel lớn để biến chưa từng có dữ liệu tự động rơi vào
                # tier EVICT và bị triệt tiêu trọng số (1 - 999/30 < 0) mà không cần xử lý riêng.
                t_i = 999  # chưa bao giờ có dữ liệu
                stale_type = "NEVER_SEEN"
            else:
                last_dt = datetime.strptime(last_date, "%Y-%m-%d")
                t_i = (today_dt - last_dt).days

                if t_i < TIER_NORMAL:
                    stale_type = "NORMAL"
                elif t_i < TIER_WARNING:
                    stale_type = "WARNING"
                elif t_i < TIER_TERMINAL:
                    stale_type = "TERMINAL"
                else:
                    stale_type = "EVICT"

            variables[name] = {
                "t_i": t_i,
                "tier": stale_type,
                "base_weight": base_w,
            }

        conn.close()

        # ── Warm-up: biến TERMINAL quay lại → cần 5 ngày liên tục ──
        # (warmup được cập nhật từ bên ngoài qua record_fresh)
        for name in variables:
            is_fresh = variables[name]["tier"] == "NORMAL"
            if is_fresh:
                self._warmup[name] = self._warmup.get(name, 0) + 1
            else:
                self._warmup[name] = 0

        # WHY: Suy giảm tuyến tính theo thời gian (1 - t_i/30) — trọng số giảm đều từ 100%
        # xuống 0 trong 30 ngày thay vì cắt đột ngột theo tier, giữ mượt khi dữ liệu già dần.
        # ── L1 weight redistribution ──
        raw_weights: dict[str, float] = {}
        total_tilde = 0.0
        for name, v in variables.items():
            w_tilde = v["base_weight"] * max(0.0, 1.0 - v["t_i"] / 30.0)

            # WHY: Warm-up factor tỷ lệ thuận (wu/5) — biến TERMINAL mới hồi phục được tăng
            # trọng số từ từ thay vì bật ngay 100%, giảm thiểu rủi ro nguồn vẫn chưa ổn định.
            # Warm-up penalty: nếu < WARMUP_DAYS ngày liên tục, giảm weight
            wu = self._warmup.get(name, 0)
            if wu < WARMUP_DAYS and v["tier"] in ("TERMINAL", "EVICT"):
                warmup_factor = wu / WARMUP_DAYS  # 0.0 → 1.0 sau 5 ngày
                w_tilde *= warmup_factor

            raw_weights[name] = w_tilde
            total_tilde += w_tilde

        # WHY: Tổng trọng số giảm về 0 nghĩa là mọi biến đều chết hẳn — lúc này không đủ
        # thông tin để đánh giá macro, phải veto khẩn cấp thay vì mặc định "an toàn".
        # Emergency veto: toàn bộ vĩ mô mất hiệu lực
        veto = False
        if total_tilde <= 0.0:
            veto = True
            # Gán weight đều để tránh lỗi chia 0
            n = len(variables)
            final_weights = {name: 1.0 / n for name in variables}
        else:
            # WHY: L1 normalize → cap 0.50 → re-normalize: hai vòng này đảm bảo tổng luôn
            # bằng 1.0 đồng thời một biến sống sót không được thống trị toàn bộ phân bổ.
            # L1 normalize
            w_raw = {name: w / total_tilde for name, w in raw_weights.items()}

            # Cap 0.50
            w_capped = {name: min(WEIGHT_CAP, w) for name, w in w_raw.items()}

            # Re-L1 normalize
            cap_total = sum(w_capped.values())
            if cap_total > 0.0:
                final_weights = {name: w / cap_total for name, w in w_capped.items()}
            else:
                n = len(variables)
                final_weights = {name: 1.0 / n for name in variables}

        # Gắn final weight vào variables
        for name in variables:
            variables[name]["final_weight"] = round(final_weights.get(name, 0.0), 6)
            variables[name]["warmup_days"] = self._warmup.get(name, 0)

        # ── Fresh ratio & terminal ratio ──
        n_total = len(variables)
        n_fresh = sum(1 for v in variables.values() if v["tier"] == "NORMAL")
        n_terminal = sum(1 for v in variables.values() if v["tier"] in ("TERMINAL", "EVICT"))
        fresh_ratio = n_fresh / n_total if n_total > 0 else 0.0
        terminal_ratio = n_terminal / n_total if n_total > 0 else 0.0

        # WHY: Hai tiêu chí veto bổ sung nhau — fresh_ratio < 50% (dưới nửa dữ liệu mới)
        # hoặc terminal_ratio >= 50% (nửa dữ liệu đã quá hạn) đều đủ để chặn quyết định,
        # tránh chỉ dựa vào một phép đo đơn lẻ có thể sai lệch.
        # Decision check
        veto = veto or (fresh_ratio < FRESH_RATIO_VETO) or (terminal_ratio >= TERMINAL_RATIO_VETO)

        # Save warmup state
        self._save_warmup()

        return {
            "today": today,
            "variables": variables,
            "fresh_ratio": round(fresh_ratio, 4),
            "terminal_ratio": round(terminal_ratio, 4),
            "veto": veto,
            "weights": {name: v["final_weight"] for name, v in variables.items()},
            "tiers": {name: v["tier"] for name, v in variables.items()},
        }

    def record_fresh(self, variable: str, success: bool):
        """Ghi nhận kết quả fetch cho warm-up tracking.

        Gọi từ daily_updater sau mỗi lần fetch từng ticker.
        """
        if success:
            self._warmup[variable] = self._warmup.get(variable, 0) + 1
        else:
            self._warmup[variable] = 0

    # ── Persistence ──

    def _warmup_path(self) -> Path:
        if self.state_path:
            return Path(self.state_path)
        return Path(self.db_path).parent / "macro_warmup_state.json"

    def _save_warmup(self):
        try:
            path = self._warmup_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"warmup": self._warmup, "updated": datetime.now().isoformat()}),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as e:
            logger.warning("StaleTracker: failed to save warmup state: %s", e)

    def _load_warmup(self):
        try:
            path = self._warmup_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                self._warmup = data.get("warmup", {})
        except json.JSONDecodeError, OSError, TypeError, ValueError, KeyError:
            logger.debug("StaleTracker: không đọc được warmup state — khởi tạo lại rỗng")
            self._warmup = {}
