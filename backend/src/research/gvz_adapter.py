"""gvz_adapter.py — Adapter CBOE Gold Volatility Index (GVZ) → PIT daily series.

LANE STRICT PIT (daily index, không revision → không cần vintage ladder):
  - Nguồn: FRED `GVZCLS` (CBOE GVZ — implied 30d vol từ option COMEX).
    CSV endpoint: https://fred.stlouisfed.org/graph/fredgraph.csv?id=GVZCLS
    Cache: backend/data/cache/gvz/gvzcls_fred.csv (đã fetch 2026-08-18).
  - observation_date = ngày giao dịch CBOE. Giá trị ngày t phản ánh kỳ vọng
    vol tại close t (implied, KHÔNG cần chờ future).
  - publication_date = observation_date + 1 ngày giao dịch (giá trị ngày t chỉ
    dùng được từ t+1) — đúng chuẩn FRED daily release sau close.
  - Provenance: fred / GVZCLS / fetch timestamp. KHÔNG synthetic lag tuỳ tiện.

Vai trò: chỉ phục vụ Interval Gate (σ_dyn, nhánh RISK/uncertainty), KHÔNG phải
demand signal (mean μ giữ M1+CB_IFS_z). Không đụng gold_h2_series (đó là monthly
reserve layer). Read-only: không ghi DB.

Usage (từ project root):
  python -X utf8 backend/src/research/gvz_adapter.py            # audit nhanh
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# ── Sentinel v2.1 (Anchor) ──────────────────────────────────────────────────
_current = Path(__file__).resolve().parent
PROJECT_ROOT = _current
while PROJECT_ROOT != PROJECT_ROOT.parent:
    if (PROJECT_ROOT / "AGENTS.md").exists() and (PROJECT_ROOT / "backend").is_dir():
        break
    PROJECT_ROOT = PROJECT_ROOT.parent
BACKEND = PROJECT_ROOT / "backend"
DATA_DIR = BACKEND / "data"
CACHE_FILE = DATA_DIR / "cache" / "gvz" / "gvzcls_fred.csv"
SOURCE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=GVZCLS"
for _p in [str(BACKEND / "src"), str(BACKEND), str(PROJECT_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError, OSError, ValueError:
    pass

PROVENANCE = "fred/GVZCLS"
FETCH_DATE = "2026-08-18"


def load_gvz(cache_file: Path | str = CACHE_FILE) -> pd.DataFrame:
    """Đọc cache CSV → DataFrame [observation_date, value] (sort, numeric)."""
    df = pd.read_csv(cache_file)
    out = pd.DataFrame()
    out["observation_date"] = pd.to_datetime(df["observation_date"])
    out["value"] = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    out = out.dropna(subset=["observation_date"]).sort_values("observation_date")
    out = out.drop_duplicates("observation_date", keep="last")
    return out.reset_index(drop=True)


def business_day_after(dates: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Ngày giao dịch liền sau (kỳ vọng phiên tiếp; dùng calendar B để không
    nhảy cuối tuần — đủ cho daily index release semantics)."""
    return dates + pd.offsets.BDay(1)


def pit_series(df: pd.DataFrame) -> pd.Series:
    """PIT series: index = publication_date (obs+1 trading day), value = GVZ.

    Tại ngày t, giá trị dùng được là GVZ của ngày t-1 (obs <= t-1) → reindex
    với publication date, ffill; chỉ trả index >= ngày pub đầu tiên.
    """
    s = df.set_index("observation_date")["value"]
    pub = business_day_after(s.index)
    s2 = s.copy()
    s2.index = pub
    return s2.sort_index()


def pit_align(gvz_pub: pd.Series, target_idx: pd.DatetimeIndex) -> pd.Series:
    """Align PIT GVZ lên target index (panel): tại t dùng GVZ pub <= t (ffill)."""
    return gvz_pub.reindex(target_idx, method="ffill")


def audit() -> None:
    df = load_gvz()
    print(
        f"[gvz] source={PROVENANCE} rows={len(df)} "
        f"range={df['observation_date'].min().date()} -> {df['observation_date'].max().date()}"
    )
    na = int(df["value"].isna().sum())
    print(f"[gvz] NA={na} ({na / len(df) * 100:.2f}%) | non-NA={int(df['value'].notna().sum())}")
    s = pit_series(df)
    print(f"[gvz] PIT series: {s.index.min().date()} -> {s.index.max().date()} ({len(s)} ngày)")


if __name__ == "__main__":
    audit()
