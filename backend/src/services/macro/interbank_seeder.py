import sys
import logging
import re
from pathlib import Path
from datetime import datetime

def _hydrate_path():
    if getattr(sys, 'frozen', False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    backend_dir = root_path / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

import pandas as pd
from src.database.db_core import get_connection, save_data_upsert

logger = logging.getLogger(__name__)

VIETNAMBiz_BUILD_ID = "4rZHofl9s0ftfNuzY0Phf"
VIETNAMBiz_RATES_URL = (
    f"https://data.vietnambiz.vn/_next/data/{VIETNAMBiz_BUILD_ID}/currency-interest-rate.json"
)

INTERBANK_VARIABLES = ["INTERBANK_ON", "INTERBANK_1W", "INTERBANK_2W", "INTERBANK_1M"]


def _normalize(val) -> float | None:
    """Ép giá trị về float sạch, xoá ký tự rác / khoảng trắng."""
    if val is None:
        return None
    cleaned = re.sub(r'[^0-9\.]', '', str(val).replace(',', '.'))
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _get_latest_from_db(variable: str) -> tuple[float, str] | tuple[None, None]:
    """Lấy giá trị và ngày mới nhất của 1 biến từ DB."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT value, date FROM macro_history WHERE variable = ? ORDER BY date DESC LIMIT 1",
                (variable,),
            ).fetchone()
            if row is None:
                return None, None
            return float(row[0]), str(row[1])
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
#  SOURCE 1 — SBV Portal API (primary)
# ---------------------------------------------------------------------------
def _try_sbv() -> dict[str, float | None]:
    """Gọi SBV API. Trả về dict {ON, 1W, 2W, 1M}."""
    try:
        import requests
        resp = requests.get(
            "https://portal.sbv.gov.vn/api/public/lai-suat-lien-ngan-hang",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"SBV API HTTP {resp.status_code}")
            return {}

        data = resp.json()
        if isinstance(data, list):
            return {
                "ON": _normalize(data[0].get("rate")) if len(data) > 0 else None,
                "1W": _normalize(data[1].get("rate")) if len(data) > 1 else None,
                "2W": _normalize(data[2].get("rate")) if len(data) > 2 else None,
                "1M": _normalize(data[3].get("rate")) if len(data) > 3 else None,
            }
        elif isinstance(data, dict):
            return {"ON": _normalize(data.get("rate"))}
    except Exception as e:
        logger.warning(f"SBV API không khả dụng: {e}")
    return {}


# ---------------------------------------------------------------------------
#  SOURCE 2 — VietnamBiz (SSG Next.js data, ON rate confirmed working)
# ---------------------------------------------------------------------------
def _try_vietnambiz() -> float | None:
    """Lấy Lãi suất liên ngân hàng _ON từ VietnamBiz."""
    try:
        import requests
        resp = requests.get(
            VIETNAMBiz_RATES_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        items = resp.json().get("pageProps", {}).get("data", [])
        for item in items:
            title = item.get("title", "")
            if "Lãi suất liên ngân hàng _ON" in title or "Lai suat lien ngan hang _ON" in title:
                return _normalize(item.get("value"))
    except Exception as e:
        logger.warning(f"VietnamBiz API không khả dụng: {e}")
    return None


# ---------------------------------------------------------------------------
#  SOURCE 3 — CafeF article scraping (full curve ON / 1W / 2W / 1M)
# ---------------------------------------------------------------------------
CAFEF_ARTICLE_URLS = [
    # Danh sách URL bài viết gần đây về lãi suất liên ngân hàng
    "https://cafef.vn/lai-suat-lien-ngan-hang-tang-vot-len-7-ngan-hang-nha-nuoc-bom-luong-lon-vnd-ho-tro-he-thong-188251202103649843.chn",
    "https://cafef.vn/cap-nhat-thi-truong-tien-te-lai-suat-qua-dem-lien-ngan-hang-giam-manh-ty-gia-usd-lao-doc-188251226110319552.chn",
    "https://cafef.vn/lai-suat-lien-ngan-hang-tiep-tuc-giam-kenh-cho-vay-omo-cua-nhnn-bi-e-ty-gia-usd-tu-do-mat-moc-27000-dong-188251218143455335.chn",
]


def _parse_cafef_rates(html: str) -> dict[str, float | None]:
    """Trích xuất lãi suất liên ngân hàng từ nội dung bài viết CafeF.

    Pattern điển hình:
      "lãi suất qua đêm ... X%/năm; kỳ hạn 1 tuần ... Y%/năm;
       kỳ hạn 2 tuần ... Z%/năm và 1 tháng là W%/năm"
    """
    results = {"ON": None, "1W": None, "2W": None, "1M": None}

    patterns = {
        "ON": r'qua đêm[^;.]*?(\d+[\.,]?\d*)\s*%/năm',
        "1W": r'1 tuần[^;.%]*?(\d+[\.,]?\d*)\s*%/năm',
        "2W": r'2 tuần[^;.%]*?(\d+[\.,]?\d*)\s*%/năm',
        "1M": r'1 tháng[^;.]{0,80}?(\d+[\.,]?\d*)\s*%/năm',
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            results[key] = _normalize(m.group(1))

    return results


def _try_cafef_articles() -> dict[str, float | None]:
    """Duyệt danh sách URL bài viết CafeF, parse nếu tìm thấy."""
    import requests

    for url in CAFEF_ARTICLE_URLS:
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            rates = _parse_cafef_rates(resp.text)
            if rates["ON"] is not None or rates["1W"] is not None:
                logger.info(f"Đã parse lãi suất từ CafeF: {rates}")
                return rates
        except Exception as e:
            logger.warning(f"CafeF article error ({url[:60]}...): {e}")
            continue
    return {}


# ---------------------------------------------------------------------------
#  ORCHESTRATOR
# ---------------------------------------------------------------------------
def refresh_interbank_rate() -> bool:
    """Pipeline 3 tầng: SBV → VietnamBiz → CafeF article.

    Returns:
        True nếu seed được ít nhất INTERBANK_ON, False nếu hoàn toàn thất bại.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Log độ trễ dữ liệu hiện tại ---
    for var in INTERBANK_VARIABLES:
        val, dt = _get_latest_from_db(var)
        if val is not None:
            try:
                days_old = (datetime.now() - datetime.strptime(dt, "%Y-%m-%d")).days
                logger.info(f"{var} hiện tại: {val} (từ {dt}, {days_old} ngày trước)")
            except Exception:
                pass

    # --- Tầng 1: SBV API ---
    rates = _try_sbv()
    source_tag = "SBV"

    # --- Tầng 2: VietnamBiz (ON rate) ---
    if rates.get("ON") is None:
        on_rate = _try_vietnambiz()
        if on_rate is not None:
            rates["ON"] = on_rate
            source_tag = "VietnamBiz"
            logger.info(f"FALLBACK → VietnamBiz: ON = {on_rate}")

    # --- Tầng 3: CafeF articles (full curve nếu SBV không trả đủ) ---
    if len(rates) < 3:  # chưa đủ ON + 1W + 2W + 1M
        cafef_rates = _try_cafef_articles()
        for k in ("ON", "1W", "2W", "1M"):
            if rates.get(k) is None and cafef_rates.get(k) is not None:
                rates[k] = cafef_rates[k]
                if source_tag == "SBV":
                    source_tag = "SBV+CafeF"

    # --- Kiểm tra: ít nhất ON phải có ---
    if rates.get("ON") is None:
        logger.warning("Tất cả nguồn đều fail — giữ dữ liệu cũ.")
        return False

    # --- Ghi DB ---
    mapping = {
        "INTERBANK_ON": rates["ON"],
        "INTERBANK_1W": rates.get("1W"),
        "INTERBANK_2W": rates.get("2W"),
        "INTERBANK_1M": rates.get("1M"),
    }

    rows = []
    for var, val in mapping.items():
        if val is not None:
            rows.append({"variable": var, "date": today, "value": val})

    if not rows:
        return False

    df = pd.DataFrame(rows)
    try:
        with get_connection() as conn:
            save_data_upsert("macro_history", df, conn)
        for row in rows:
            logger.info(f"Đã cập nhật {row['variable']}: {row['value']}% (nguồn: {source_tag})")
        return True
    except Exception as e:
        logger.error(f"Seed interbank rates thất bại: {e}")
        return False
