"""
Universe Definition — single source of truth for SECTOR_MAP and market universe.
All engines import from here instead of duplicating symbol lists.

Architecture:
    universe.py (this file) → capital_flow_forecasting_engine
                           → portfolio_recommendation_engine
                           → rsi_regime_engine
                           → any engine needing sector classification

Only this file defines SECTOR_MAP. No more duplication.
"""
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
    backend_dir = root_path / "backend"
    if backend_dir.is_dir() and str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root_path

PROJECT_ROOT = _hydrate_path()

SECTOR_MAP = {
    'ACB':'BANK','TCB':'BANK','VCB':'BANK','BID':'BANK','CTG':'BANK','VPB':'BANK',
    'MBB':'BANK','HDB':'BANK','STB':'BANK','TPB':'BANK','VIB':'BANK','EIB':'BANK',
    'SHB':'BANK','LPB':'BANK','OCB':'BANK','MSB':'BANK','NAB':'BANK','KLB':'BANK',
    'NVB':'BANK','SGB':'BANK','PGB':'BANK','BAB':'BANK',
    'SSI':'SEC','VND':'SEC','HCM':'SEC','MBS':'SEC','BSI':'SEC','VCI':'SEC',
    'BVS':'SEC','TVB':'SEC','AGR':'SEC','CTS':'SEC','EVF':'SEC','TIG':'SEC',
    'VFS':'SEC','ORS':'SEC','APG':'SEC','PSI':'SEC','SHS':'SEC',
    'VIC':'RE','VHM':'RE','VRE':'RE','NVL':'RE','KDH':'RE','PDR':'RE','DXG':'RE',
    'DIG':'RE','NLG':'RE','KBC':'RE','SZC':'RE','BCM':'RE','SIP':'RE','GVR':'RE',
    'IDC':'RE','HPX':'RE','HDG':'RE','PC1':'RE','TCH':'RE','CII':'RE','NDN':'RE',
    'NTL':'RE','SCR':'RE','HQC':'RE','QCG':'RE',
    'HPG':'STEEL','NKG':'STEEL','HSG':'STEEL','POM':'STEEL','VGS':'STEEL',
    'TLH':'STEEL','SMC':'STEEL','DVG':'STEEL',
    'MWG':'CONSUMER','VNM':'CONSUMER','MSN':'CONSUMER','SAB':'CONSUMER',
    'BHN':'CONSUMER','FRT':'CONSUMER','DGW':'CONSUMER','PET':'CONSUMER',
    'COM':'CONSUMER','SGN':'CONSUMER','PNJ':'CONSUMER','DGC':'CONSUMER','HNG':'CONSUMER',
    'FPT':'TECH','CMG':'TECH','CTR':'TECH','FOX':'TECH','ELC':'TECH',
    'SAM':'TECH','SVT':'TECH','ICT':'TECH','VGI':'TECH',
    'GAS':'OIL','PLX':'OIL','POW':'OIL','BSR':'OIL','PVT':'OIL','PVD':'OIL',
    'PVS':'OIL','PVC':'OIL','CNG':'OIL','NT2':'OIL',
    'VJC':'TRANS','GMD':'TRANS','VSC':'TRANS','HAH':'TRANS','VOS':'TRANS',
    'VIP':'TRANS','GSP':'TRANS','VTO':'TRANS','TCL':'TRANS','TOS':'TRANS',
    'CTD':'CONST','HBC':'CONST','VGC':'CONST','VLB':'CONST','PTB':'CONST',
    'HT1':'CONST','VCG':'CONST','LCG':'CONST','FCN':'CONST','C4G':'CONST',
    'QNS':'FOOD','SBT':'FOOD','LSS':'FOOD','SEC':'FOOD','NHS':'FOOD',
    'LHG':'FOOD','BFC':'FOOD','HHC':'FOOD','PAC':'FOOD','DCM':'FOOD',
    'DPM':'FOOD','LAS':'FOOD','LAF':'FOOD',
    'REE':'UTILITY','DP3':'UTILITY','VTP':'UTILITY','IJC':'UTILITY',
    'GEX':'UTILITY','HAX':'UTILITY',
}

CORE_SECTORS = ['BANK', 'RE', 'SEC', 'STEEL', 'CONSUMER', 'TECH', 'OIL', 'TRANS', 'CONST', 'FOOD', 'UTILITY']

UNIVERSE_SYMBOLS = [s for s in SECTOR_MAP.keys() if s != 'VNINDEX']


def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol, 'OTHER')


def get_symbols_by_sector(sector: str) -> list:
    return [s for s, sec in SECTOR_MAP.items() if sec == sector]


def get_universe(exclude: list = None) -> list:
    symbols = list(UNIVERSE_SYMBOLS)
    if exclude:
        symbols = [s for s in symbols if s not in exclude]
    return symbols
