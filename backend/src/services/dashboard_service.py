"""
Dashboard Service Layer v1.0
Trạm biến áp trung tâm — gộp Macro + Breadth + Screener cho trang chủ.
"""
import logging
import sys
from pathlib import Path


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
    return root_path

PROJECT_ROOT = _hydrate_path()

from src.services.macro_service import get_macro_status, get_regime_history
from src.services.screener_service import get_screener_results

logger = logging.getLogger(__name__)


def get_dashboard_data() -> dict:
    """
    Gộp tất cả dữ liệu cần thiết cho trang Dashboard:
    - Macro status (regime, risk level)
    - Market breadth (health score, advancers/decliners)
    - Top leaders (screener candidates)
    - Regime history (for timeline chart)
    - Shadow portfolio cash percentage
    """
    macro = {}
    try:
        macro = get_macro_status()
    except Exception as e:
        logger.error(f"Macro service failed: {e}")

    breadth = {}
    try:
        from src.registry import registry
        breadth_result = registry.breadth_engine.run_breadth_analysis()
        if breadth_result:
            breadth = {
                "healthScoreMa20": breadth_result.get('health_score_ma20', 0),
                "nh10Count": breadth_result.get('nh10_count', 0),
                "advancers": breadth_result.get('advancers', 0),
                "decliners": breadth_result.get('decliners', 0),
                "totalActive": breadth_result.get('total_active', 0),
            }
    except Exception as e:
        logger.error(f"Breadth service failed: {e}")

    top_leaders = []
    try:
        top_leaders = get_screener_results(top_n=10)
    except Exception as e:
        logger.error(f"Screener service failed: {e}")

    regime_history = []
    try:
        regime_history = get_regime_history(limit=90)
    except Exception as e:
        logger.error(f"Regime history failed: {e}")

    shadow_cash = 100.0
    try:
        import json
        import os
        portfolio_path = os.path.join(PROJECT_ROOT, "src", "portfolio", "my_portfolio.json")
        if os.path.exists(portfolio_path):
            with open(portfolio_path, 'r', encoding='utf-8') as f:
                portfolio = json.load(f)
            cash = portfolio.get('cash', 0)
            positions = portfolio.get('positions', [])
            total_position_value = sum(
                p.get('quantity', 0) * p.get('entry_price', 0) * 1000
                for p in positions
            )
            total_nav = cash + total_position_value
            if total_nav > 0:
                shadow_cash = round((cash / total_nav) * 100, 1)
    except Exception as e:
        logger.error(f"Portfolio service failed: {e}")

    regime_status = macro.get('regime_status', 'UNKNOWN')
    risk_level = macro.get('risk_level', 'Amber')
    system_message = _generate_system_message(regime_status, risk_level)

    return {
        "macro": {
            "usdCnh": macro.get('usd_cnh', 0),
            "usdCny": macro.get('usd_cny', 0),
            "copperPrice": macro.get('copper_price', 0),
            "dxyIndex": macro.get('dxy_index', 0),
            "interbankRate": macro.get('interbank_rate', 0),
            "interbankRate1w": macro.get('interbank_rate_1w'),
            "interbankRate2w": macro.get('interbank_rate_2w'),
            "interbankRate1m": macro.get('interbank_rate_1m'),
            "sbvAction": macro.get('sbv_action', 'Neutral'),
            "riskLevel": macro.get('risk_level', 'Amber'),
            "goldRegime": macro.get('gold_regime', 'NEUTRAL'),
            "goldVelocity": macro.get('gold_velocity', 0),
            "goldSpreadPressure": macro.get('gold_spread_pressure', 0),
            "goldMacroBias": macro.get('gold_macro_bias', 'NEUTRAL'),
            "goldPremiumRegime": macro.get('gold_premium_regime', 'PREMIUM_NORMAL'),
            "goldPremiumPct": macro.get('gold_premium_pct', 0),
            "goldPremiumVnd": macro.get('gold_premium_vnd', 0),
            "adx": macro.get('adx', 0.0),
            "atrRatio": macro.get('atr_ratio', 0.0),
            "vgb10y": macro.get('vgb10y', 2.84),
            "vgb10yBpsChange": macro.get('vgb10y_bps_change', '0 bps'),
            "vgb10yStatusLabel": macro.get('vgb10y_status_label', 'Thanh khoản nới lỏng'),
            "vgb10yRawBps": macro.get('vgb10y_raw_bps', 0),
            "silverPrice": macro.get('silver_price', 0),
            "goldSilverRatio": macro.get('gold_silver_ratio', 0),
            "tipPrice": macro.get('tip_price'),
            "usRealYield": macro.get('us_real_yield'),
            "breakevenInflation": macro.get('breakeven_inflation'),
            "us2yYield": macro.get('us2y_yield'),
            "us5yYield": macro.get('us5y_yield'),
            "us30yYield": macro.get('us30y_yield'),
            "spread10y2y": macro.get('spread_10y2y'),
            "spread30y10y": macro.get('spread_30y10y'),
            "yieldCurveInversion": macro.get('yield_curve_inversion'),
        },
        "breadth": {
            "healthScoreMa20": macro.get('breadth_pct', 0),
            "healthScoreMa50": 0,
            "trendStatus": regime_status,
            "updatedAt": macro.get('date', ''),
            **breadth,
        },
        "topLeaders": top_leaders,
        "shadowCashPercent": shadow_cash,
        "systemMessage": system_message,
        "regimeHistory": regime_history,
        "regimeScore": macro.get('regime_score', 0),
        "goldPrice": macro.get('gold_price', 0),
        "silverPrice": macro.get('silver_price', 0),
        "goldSilverRatio": macro.get('gold_silver_ratio', 0),
        "btcPrice": macro.get('btc_price', 0),
        "usdVnd": macro.get('usd_vnd', 0),
        "goldScenarios": macro.get('gold_scenarios', []),
    }


def _generate_system_message(regime_status: str, risk_level: str) -> dict:
    """Tạo thông báo hệ thống dựa trên trạng thái thị trường.
    Trả về object cấu trúc cho Mô hình C — Bối cảnh.
    """
    mapping = {
        'CRISIS': {
            'title': 'Mô hình C — Bối cảnh: TRẠNG THÁI KHỦNG HOẢNG',
            'advise': (
                'Đóng băng hoàn toàn vị thế mua đuổi của Mô hình A (Động lượng). '
                'Hạ tỷ trọng margin về 0. Chỉ trích xuất tối đa 30% vốn tiền mặt '
                'để gom rải đinh các ứng viên kiệt quệ thuộc Mô hình B (Dòng tiền) '
                'tại vùng hỗ trợ cứng.'
            ),
            'color': 'rose',
        },
        'TRENDING': {
            'title': 'Mô hình C — Bối cảnh: THỊ TRƯỜNG VÀO SÓNG TĂNG',
            'advise': (
                'Kích hoạt tổng lực Mô hình A — Động lượng. Tập trung 70% trọng số '
                'danh mục vào các mã bứt phá đỉnh có khối lượng xác nhận lớn (RVOL > 1.5). '
                'Ngưng các lệnh mua gom của Mô hình B để tối ưu vòng quay vốn.'
            ),
            'color': 'emerald',
        },
        'RANGING': {
            'title': 'Mô hình C — Bối cảnh: THỊ TRƯỜNG ĐI NGANG (SIDEWAYS)',
            'advise': (
                'Dòng tiền phân hóa cực đoan. Chiến lược tối ưu là giao dịch biên độ '
                '(Swing Trade). Chia đều tỷ trọng vốn 50% Mô hình A (đánh mã ngách bứt phá) '
                'và 50% Mô hình B (mua sát cạnh dưới biên tích lũy).'
            ),
            'color': 'amber',
        },
    }
    return mapping.get(regime_status, {
        'title': 'Mô hình C — Bối cảnh: CHƯA RÕ XU HƯỚNG',
        'advise': (
            'Hệ thống đang thu thập thêm dữ liệu độ rộng. '
            'Duy trì tỷ trọng danh mục ở mức cân bằng, chờ tín hiệu xác nhận.'
        ),
        'color': 'gray',
    })
