import logging
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings('ignore')

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
if sys.platform == "win32":
    import io
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, 'encoding', '').lower() != 'utf-8':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
    elif hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
backend_dir = PROJECT_ROOT / "backend"
if backend_dir.is_dir() and str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import numpy as np

import src.config
from src.core.presentation.vi_localizer import SECTOR_LABELS
from src.database.db_core import get_connection, safe_json_dump

logger = logging.getLogger(__name__)

# Market-wide scan set (~150 symbols across all sectors)
BROAD_SCAN_SYMBOLS = [
    # Banks
    'ACB','TCB','VCB','BID','CTG','VPB','MBB','HDB','STB','TPB','VIB','EIB','SHB','LPB','OCB','MSB','NAB','KLB','NVB','SGB','PGB','BAB',
    # Securities
    'SSI','VND','HCM','MBS','BSI','VCI','BVS','TVB','AGR','CTS','EVF','TIG','VFS','ORS','APG','PSI','SHS',
    # Real Estate
    'VIC','VHM','VRE','NVL','KDH','PDR','DXG','DIG','NLG','KBC','SZC','BCM','SIP','GVR','IDC','HPX','HDG','PC1','TCH','CII','NDN','NTL','SCR','HQC','QCG',
    # Steel
    'HPG','NKG','HSG','POM','VGS','TLH','SMC','DVG',
    # Consumer / Retail
    'MWG','VNM','MSN','SAB','BHN','FRT','DGW','PET','COM','SGN','PNJ','DGC','HNG',
    # Tech
    'FPT','CMG','CTR','FOX','ELC','SAM','SVT','ICT','VGI',
    # Oil & Gas
    'GAS','PLX','POW','BSR','PVT','PVD','PVS','PVC','CNG','NT2',
    # Transportation
    'VJC','GMD','VSC','HAH','VOS','VIP','GSP','VTO','TCL','PVT','TOS',
    # Construction
    'CTD','HBC','VGC','VLB','PTB','HT1','VCG','LCG','FCN','C4G',
    # Food & Beverage
    'QNS','SBT','LSS','SEC','NHS','LHG','BFC','HHC','PAC','VNM','DCM','DPM','LAS','LAF',
    # Others
    'REE','DP3','VTP','IJC','POW','GEX','HAX','TNA','TCM','STK','PTI','BMI','BVH','MIG','PVI',
]
WATCHLIST_SYMBOLS = ['HPG','MBB','GMD','STB','VTO','REE','DP3','VTP','MWG','SSI','BSR','QNS','TLG','SBT','FPT','IJC','VGI','VIB','TCB','ACB']

SECTOR_MAP = {
    'ACB':'BANK','TCB':'BANK','VCB':'BANK','BID':'BANK','CTG':'BANK','VPB':'BANK','MBB':'BANK','HDB':'BANK','STB':'BANK','TPB':'BANK','VIB':'BANK','EIB':'BANK','SHB':'BANK','LPB':'BANK','OCB':'BANK','MSB':'BANK','NAB':'BANK','KLB':'BANK','NVB':'BANK','SGB':'BANK','PGB':'BANK','BAB':'BANK',
    'SSI':'SEC','VND':'SEC','HCM':'SEC','MBS':'SEC','BSI':'SEC','VCI':'SEC','BVS':'SEC','TVB':'SEC','AGR':'SEC','CTS':'SEC','EVF':'SEC','TIG':'SEC','VFS':'SEC','ORS':'SEC','APG':'SEC','PSI':'SEC','SHS':'SEC',
    'VIC':'RE','VHM':'RE','VRE':'RE','NVL':'RE','KDH':'RE','PDR':'RE','DXG':'RE','DIG':'RE','NLG':'RE','KBC':'RE','SZC':'RE','BCM':'RE','SIP':'RE','GVR':'RE','IDC':'RE','HPX':'RE','HDG':'RE','PC1':'RE','TCH':'RE','CII':'RE','NDN':'RE','NTL':'RE','SCR':'RE','HQC':'RE','QCG':'RE',
    'HPG':'STEEL','NKG':'STEEL','HSG':'STEEL','POM':'STEEL','VGS':'STEEL','TLH':'STEEL','SMC':'STEEL','DVG':'STEEL',
    'MWG':'CONSUMER','VNM':'CONSUMER','MSN':'CONSUMER','SAB':'CONSUMER','BHN':'CONSUMER','FRT':'CONSUMER','DGW':'CONSUMER','PET':'CONSUMER','COM':'CONSUMER','SGN':'CONSUMER','PNJ':'CONSUMER','DGC':'CONSUMER','HNG':'CONSUMER',
    'FPT':'TECH','CMG':'TECH','CTR':'TECH','FOX':'TECH','ELC':'TECH','SAM':'TECH','SVT':'TECH','ICT':'TECH','VGI':'TECH',
    'GAS':'OIL','PLX':'OIL','POW':'OIL','BSR':'OIL','PVT':'OIL','PVD':'OIL','PVS':'OIL','PVC':'OIL','CNG':'OIL','NT2':'OIL',
    'VJC':'TRANS','GMD':'TRANS','VSC':'TRANS','HAH':'TRANS','VOS':'TRANS','VIP':'TRANS','GSP':'TRANS','VTO':'TRANS','TCL':'TRANS','PVT':'TRANS','TOS':'TRANS',
    'CTD':'CONST','HBC':'CONST','VGC':'CONST','VLB':'CONST','PTB':'CONST','HT1':'CONST','VCG':'CONST','LCG':'CONST','FCN':'CONST','C4G':'CONST',
    'QNS':'FOOD','SBT':'FOOD','LSS':'FOOD','SEC':'FOOD','NHS':'FOOD','LHG':'FOOD','BFC':'FOOD','HHC':'FOOD','PAC':'FOOD','DCM':'FOOD','DPM':'FOOD','LAS':'FOOD','LAF':'FOOD',
    'REE':'UTILITY','DP3':'UTILITY','VTP':'UTILITY','IJC':'UTILITY','POW':'UTILITY','GEX':'UTILITY','HAX':'UTILITY',
}

def _fetch_batch(symbols, interval_days=5):
    import requests
    from vnstock import Quote
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=interval_days)).strftime('%Y-%m-%d')
    results = {}
    fallback_count = 0
    fail_count = 0
    for sym in symbols:
        sources = [('vci', 'VCI'), ('kbs', 'KBS')]
        for source, label in sources:
            try:
                q = Quote(symbol=sym, source=source)
                # Hard timeout: tránh treo vô hạn khi offline / mạng yếu.
                # (vnstock mặc định timeout=None → chờ mãi → EOD hang >120s).
                df = q.history(start=start, end=end, timeout=5)
                if df is not None and len(df) > 0:
                    results[sym] = df
                    if label == 'KBS':
                        fallback_count += 1
                        logger.info("FALLBACK: %s → kbs", sym)
                    break
            except (requests.Timeout, requests.ConnectionError) as e:
                if label == 'KBS':
                    fail_count += 1
                    logger.warning("SKIP: %s — cả VCI và KBS đều fail: %s", sym, e)
                else:
                    logger.debug("VCI fail for %s, trying KBS: %s", sym, e)
            except Exception as e:
                # Lỗi không phải mạng → bỏ qua symbol, không nuốt timeout mạng
                logger.debug("Unexpected error for %s (%s): %s", sym, label, e)
                if label == 'KBS':
                    fail_count += 1
    if fallback_count:
        logger.info("Fallback summary: %d/%d symbols dùng KBS, %d skip", fallback_count, len(symbols), fail_count)
    return results

def _calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = np.maximum(np.diff(closes[-period-1:]), 0)
    losses = np.maximum(-np.diff(closes[-period-1:]), 0)
    avg_g = np.mean(gains)
    avg_l = np.mean(losses)
    if avg_l == 0: return 100.0
    return round(100 - (100 / (1 + (avg_g / avg_l))), 1)

def scan_liquidity_concentration(target_date=None, offline: bool = False):
    today = target_date or datetime.now().strftime('%Y-%m-%d')
    # OFFLINE GUARD: EOD pipeline chạy offline mặc định (chống IP ban + SLA 60s).
    # Engine này bản chất là online-only (vnstock.Quote). Khi offline → bỏ qua
    # fetch mạng, trả kết quả 'OFFLINE_SKIPPED' sạch thay vì treo >120s.
    if offline:
        logger.warning("[CAPDISP] offline=True → bỏ qua fetch mạng "
                       "(trả OFFLINE_SKIPPED).")
        return {
            "date": today,
            "classification": "OFFLINE_SKIPPED",
            "conviction": "ZERO",
            "signals": [],
            "note": "Capital Displacement chưa có đường cache offline; "
                    "bỏ qua trong EOD offline.",
        }
    logger.info("=" * 70)
    logger.info("  CAPITAL DISPLACEMENT ENGINE v2")
    logger.info("  Scan date: %s | Symbols: %d market-wide", today, len(BROAD_SCAN_SYMBOLS))
    logger.info("=" * 70)

    data = _fetch_batch(BROAD_SCAN_SYMBOLS, interval_days=5)
    active = {sym: df for sym, df in data.items() if len(df) > 0}
    logger.info("  Active symbols with data: %d", len(active))

    # Build price + volume snapshot
    snapshot = {}
    for sym, df in active.items():
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        snapshot[sym] = {
            'vol': int(last['volume']),
            'close': float(last['close']),
            'chg': round((float(last['close']) - float(prev['close'])) / float(prev['close']) * 100, 2) if float(prev['close']) > 0 else 0,
            'sector': SECTOR_MAP.get(sym, 'OTHER'),
        }

    total_vol = sum(s['vol'] for s in snapshot.values()) or 1

    # 1. MARKET-WIDE CONCENTRATION
    sorted_vol = sorted(snapshot.items(), key=lambda x: -x[1]['vol'])
    top1 = sorted_vol[0]
    market_concentration = {
        'top1': {'symbol': top1[0], 'vol': top1[1]['vol'], 'pct': round(top1[1]['vol']/total_vol*100, 1)},
        'top3_pct': round(sum(v[1]['vol'] for v in sorted_vol[:3])/total_vol*100, 1),
        'top5_pct': round(sum(v[1]['vol'] for v in sorted_vol[:5])/total_vol*100, 1),
        'top10_pct': round(sum(v[1]['vol'] for v in sorted_vol[:10])/total_vol*100, 1),
        'total_symbols': len(snapshot),
        'total_volume': total_vol,
    }

    # 2. WATCHLIST-SPECIFIC ANALYSIS
    wl_data = {sym: snapshot[sym] for sym in WATCHLIST_SYMBOLS if sym in snapshot}
    wl_total = sum(s['vol'] for s in wl_data.values()) or 1
    wl_sorted = sorted(wl_data.items(), key=lambda x: -x[1]['vol'])
    wl_top1 = wl_sorted[0] if wl_sorted else ('N/A', {'vol': 0})
    wl_concentration = {
        'top1': {'symbol': wl_top1[0], 'pct': round(wl_top1[1]['vol']/wl_total*100, 1)},
        'top3_pct': round(sum(v[1]['vol'] for v in wl_sorted[:3])/wl_total*100, 1) if len(wl_sorted)>=3 else 0,
    }

    # 3. SECTOR CAPITAL ANALYSIS
    sector_vol = {}
    sector_chg = {}
    for sym, s in snapshot.items():
        sec = s['sector']
        sector_vol.setdefault(sec, 0)
        sector_vol[sec] += s['vol']
        sector_chg.setdefault(sec, [])
        sector_chg[sec].append((s['chg'], s['vol']))

    # Weighted average change per sector
    sector_perf = {}
    for sec, vals in sector_chg.items():
        total_w = sum(v[1] for v in vals) or 1
        w_avg = sum(v[0]*v[1] for v in vals) / total_w
        sector_perf[sec] = round(w_avg, 2)

    sector_share = {sec: round(vol/total_vol*100, 1) for sec, vol in sorted(sector_vol.items(), key=lambda x: -x[1])}

    # 4. VNINDEX CONTEXT
    try:
        from vnstock import Quote
        vni_q = Quote(symbol='VNINDEX', source='vci')
        vni_df = vni_q.history(start=(datetime.now()-timedelta(days=60)).strftime('%Y-%m-%d'), end=today)
        vni_info = {}
        if vni_df is not None and len(vni_df) > 0:
            last = vni_df.iloc[-1]
            vni_info = {
                'close': float(last['close']),
                'chg': round((float(last['close'])-float(vni_df.iloc[-2]['close']))/float(vni_df.iloc[-2]['close'])*100, 2) if len(vni_df)>1 else 0,
                'vol': int(last['volume']),
                'vol_ratio': round(int(last['volume'])/vni_df['volume'].tail(21).iloc[:-1].mean(), 2) if len(vni_df)>=21 else 0,
                'ma20': round(vni_df['close'].tail(20).mean(), 2) if len(vni_df)>=20 else 0,
            }
    except:
        vni_info = {'close': 0, 'chg': 0, 'vol_ratio': 0}

    # 5. CLASSIFICATION RULES (market-wide, not watchlist)
    signals = []
    classification = "NEUTRAL"
    conviction = "LOW"

    # Rule 0: INTERBANK LIQUIDITY SHOCK (absolute override — checked first)
    ibank = None
    ibank_signal = ""
    try:
        from src.services.macro.interbank_zscore import assess_interbank_risk
        ibank = assess_interbank_risk()
        ibank_signal = ibank.get("signal", "")
        ibank_reason = ibank.get("reason", "")
        if ibank_signal == "SYSTEMIC_LIQUIDITY_SHOCK":
            signals.append("SYSTEMIC_LIQUIDITY_SHOCK")
            signals.append(f"Z_fast={ibank['zscore']['z_fast']}")
            signals.append(f"Z_slow={ibank['zscore']['z_slow']}")
            signals.append(f"ON_rate={ibank['zscore']['current_value']}%")
            classification = "SYSTEMIC_LIQUIDITY_SHOCK"
            conviction = "HIGH"
            logger.warning("INTERBANK SHOCK: %s", ibank_reason)
        elif ibank_signal == "WARNING":
            signals.append("INTERBANK_WARNING")
            signals.append(f"Z_fast={ibank['zscore']['z_fast']}")
            logger.info("Interbank warning: %s", ibank_reason)
    except Exception as e:
        logger.warning("Interbank risk assessment failed: %s", e)

    # Rule 1: Market-wide top-1 concentration
    top1_pct = market_concentration['top1']['pct']
    if top1_pct > 15:
        signals.append(f"HIGH_TOP1_{top1_pct:.0f}%")
    elif top1_pct > 10:
        signals.append(f"MODERATE_TOP1_{top1_pct:.0f}%")

    # Rule 2: Top-10 concentration (higher = more concentrated)
    top10_pct = market_concentration['top10_pct']
    if top10_pct > 65:
        signals.append(f"HIGH_TOP10_{top10_pct:.0f}%")

    # Rule 3: Sector rotation detection
    top_sectors = sorted(sector_share.items(), key=lambda x: -x[1])[:5]
    sector_signals = [s[0] for s in top_sectors]
    signals.append(f"SECTORS:{','.join(sector_signals)}")

    # Rule 4: Banking vs non-banking flow
    bank_share = sector_share.get('BANK', 0)
    bank_perf = sector_perf.get('BANK', 0)
    non_bank_positive = sum(1 for sec, chg in sector_perf.items() if sec != 'BANK' and chg > 0)
    total_non_bank = len([s for s in sector_perf if s != 'BANK']) or 1

    if bank_share > 35:
        signals.append(f"BANK_DOMINANT_{bank_share:.0f}%")
    elif bank_share > 25:
        signals.append(f"BANK_ELEVATED_{bank_share:.0f}%")

    # Rule 5: Breadth of sector performance
    sector_breadth = non_bank_positive / total_non_bank * 100
    if sector_breadth > 50:
        signals.append(f"SECTOR_BREADTH_{sector_breadth:.0f}%_POSITIVE")
    else:
        signals.append(f"SECTOR_BREADTH_{sector_breadth:.0f}%_POSITIVE")

    # Rule 6: Watchlist-specific displacement (informational, not classification)
    wl_disp = wl_concentration['top1']['pct']

    # FINAL CLASSIFICATION
    # Market is "concentrated" only if top-10 > 60% AND top-1 > 12%
    if top10_pct > 60 and top1_pct > 12:
        classification = "SELECTIVE_CONCENTRATION"
        conviction = "MEDIUM" if top10_pct < 70 else "HIGH"
    elif bank_share > 35 and sector_breadth < 50:
        classification = "BANK_SHELTERING"
        conviction = "MEDIUM"
    elif vni_info.get('vol_ratio', 1) < 0.6:
        classification = "LOW_VOLUME_REGIME"
        conviction = "MEDIUM"
    elif sector_breadth > 60 and vni_info.get('vol_ratio', 0) > 0.7:
        classification = "BROAD_EXPANSION"
        conviction = "HIGH"
    else:
        classification = "MIXED_LIQUIDITY"
        conviction = "LOW"

    # Build capital flow momentum vector
    top_sectors_ranked = [s[0] for s in sorted(sector_perf.items(), key=lambda x: -x[1])]
    bottom_sectors_ranked = [s[0] for s in sorted(sector_perf.items(), key=lambda x: x[1])]

    verdict = {
        "date": today,
        "classification": classification,
        "conviction": conviction,
        "signals": signals,
        "market_wide": market_concentration,
        "watchlist": {
            "concentration": wl_concentration,
            "top1_chg": wl_data.get(wl_top1[0], {}).get('chg', 0) if wl_top1[0] != 'N/A' else 0,
            "symbols_active": len(wl_data),
        },
        "sector_flow": {
            "share": sector_share,
            "performance": sector_perf,
            "leading_sectors": top_sectors_ranked[:3],
            "lagging_sectors": bottom_sectors_ranked[:3],
            "bank_share": bank_share,
            "non_bank_breadth": round(sector_breadth, 1),
        },
        "vnindex": vni_info,
        "flow_momentum": {
            "top1_market": market_concentration['top1'],
            "sector_rotation_from": bottom_sectors_ranked[:2],
            "sector_rotation_to": top_sectors_ranked[:2],
        },
        "interbank_risk": ibank if ibank_signal else None,
    }

    # Display
    logger.info("Classification: %s (conviction: %s)", classification, conviction)
    logger.info("Signals: %s", '; '.join(signals))
    logger.info("--- MARKET-WIDE ---")
    logger.info("Top1: %s=%s%% | Top3: %s%% | Top10: %s%%",
                market_concentration['top1']['symbol'], market_concentration['top1']['pct'],
                market_concentration['top3_pct'], top10_pct)
    sector_flow_vi = {SECTOR_LABELS.get(s, s): p for s, p in sector_share.items()}
    logger.info("Sector flows: %s", ', '.join(f'{s}={p}%' for s,p in list(sector_flow_vi.items())[:5]))
    logger.info("Sector breadth: %.0f%% positive | Bank share: %s%%", sector_breadth, bank_share)
    logger.info("--- WATCHLIST ---")
    logger.info("Top1: %s=%s%%", wl_concentration['top1']['symbol'], wl_concentration['top1']['pct'])
    logger.info("--- FLOW MOMENTUM ---")
    bottom_vi = ', '.join(SECTOR_LABELS.get(s, s) for s in bottom_sectors_ranked[:2])
    top_vi = ', '.join(SECTOR_LABELS.get(s, s) for s in top_sectors_ranked[:2])
    logger.info("Rotation from: [%s] -> Rotation to: [%s]", bottom_vi, top_vi)
    logger.info("VNINDEX: %s (%s%%) | Vol ratio: %sx",
                vni_info.get('close',0), f"{vni_info.get('chg',0):+.2f}", vni_info.get('vol_ratio',0))
    logger.info("=" * 70)

    return verdict

def _store_reference_case(verdict):
    mw = verdict['market_wide']
    wl = verdict['watchlist']['concentration']
    sf = verdict['sector_flow']
    vni = verdict['vnindex']
    row = {
        'date': verdict['date'],
        'classification': verdict['classification'],
        'conviction': verdict['conviction'],
        'signals': ';'.join(verdict['signals']),
        'top1_symbol': mw['top1']['symbol'],
        'top1_concentration': mw['top1']['pct'],
        'wl_top1_symbol': wl['top1']['symbol'],
        'wl_top1_concentration': wl['top1']['pct'],
        'top3_concentration': mw['top3_pct'],
        'top10_concentration': mw['top10_pct'],
        'bank_share': sf['bank_share'],
        'sector_breadth': sf['non_bank_breadth'],
        'defensive_avg_chg': 0,
        'vnindex_close': vni.get('close', 0),
        'vnindex_chg': vni.get('chg', 0),
        'vnindex_vol_ratio': vni.get('vol_ratio', 0),
    }
    try:
        with get_connection() as conn:
            cols = ', '.join(row.keys())
            vals = ', '.join(['?'] * len(row))
            conn.execute(f"INSERT OR REPLACE INTO capital_displacement_history ({cols}) VALUES ({vals})", list(row.values()))
            conn.commit()
    except Exception as e:
        logger.warning("DB store error: %s", e)

def run_scan(target_date=None, offline: bool = False):
    try:
        result = scan_liquidity_concentration(target_date, offline=offline)
        if result.get("classification") == "OFFLINE_SKIPPED":
            # Không có dữ liệu mạng → bỏ qua lưu reference case, chỉ ghi file.
            pass
        else:
            _store_reference_case(result)
        out_path = src.config.DATA_DIR / "output" / "capital_displacement.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            safe_json_dump(result, f, indent=2)
        logger.info("Saved to: %s", out_path)
        return result
    except Exception as e:
        logger.exception("Capital Displacement Engine FAILED: %s", e)
        try:
            from src.telemetry.recorder import record_engine_fault
            record_engine_fault('capital_displacement', str(e), target_date)
        except Exception:
            pass
        return {"date": target_date or datetime.now().strftime('%Y-%m-%d'),
                "classification": "FAILED", "conviction": "ZERO",
                "signals": [], "error": str(e)}

if __name__ == "__main__":
    run_scan()
