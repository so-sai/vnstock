# vnstock (PTCK-VN patched fork)

Vendored copy of the `vnstock` library, installed as an editable local package for
PTCK-VNSTOCK.

**Current base version: upstream vnstock 4.0.5** (upgraded from 3.4.0 on 2026-08-02).

## Why a fork

The upstream `vnstock` requires an API key registration for several data sources.
This fork patches `vnstock/core/utils/auth.py` to bypass the registration gate
(returns an emulated "Sponsor" tier) so PTCK-VN keeps working without a key.

## Patches vs upstream (re-apply after every upgrade)

These are the ONLY local changes over upstream 4.0.5. Verify them after upgrading.

1. **`vnstock/core/utils/auth.py`** — replaced the entire 224-line registration
   flow with a bypass: `register_user()` / `change_api_key()` return `True`;
   `check_status()` returns an emulated Sponsor tier (`VNS-PATCHED-EMULATOR`).
   Without this, VCI/TCBS return empty responses (`KeyError: 'data'`).
2. **`vnstock/core/utils/upgrade.py`** — `update_notice()` is a no-op. Suppresses
   the `pip install vnstock --upgrade` prompt that would replace this fork with
   upstream and lose the bypass.
3. **`vnai.py`** — mock module (`# vnai.py (Mock for vnstock bypass)`). Upstream
   4.0.5 depends on the separate `vnai` PyPI package everywhere
   (`from vnai import optimize_execution`, `from vnai import *`). The mock provides
   all required symbols (setup, optimize_execution, agg_execution,
   setup_api_key, check_api_key_status, accept_license_terms,
   setup_agent_environment, async_setup_agent_environment, load_skill_catalog,
   list_cached_skills, clear_skill_cache, load_skill) without the real package.
4. **`vnstock/explorer/misc/gold_price.py`** — added `btmc_silver_price()` back
   (removed in upstream 4.0.5; `backend/src/services/macro/silver_service.py`
   depends on it). `btmc_goldprice()` and `sjc_gold_price()` already exist in 4.0.5.

### BTMC API key

The gold/silver prices use the BTMC API with a hardcoded key in
`gold_price.py`:
`key=3kd8ub1llcg9t45hnoh8hmn7t5kc2v`
This key is identical across 3.4.0 and 4.0.5 — it has NOT changed between versions.
If a future upgrade breaks gold/silver, verify this key is still in the URL.

## Working data sources (verified 2026-08-02, upstream 4.0.5)

| Source | history | financials | company | notes |
|--------|---------|------------|---------|-------|
| VCI    | ✅ 24 | ✅ IS 25 / BS 122 / CF 41 | ✅ overview | financials WORK now (were `KeyError: 'data'` in 3.4.0) |
| kbs    | ✅ 22 | — | — | history verified |
| tcbs   | ❌ | — | — | returns 0 rows |
| dnse   | ❌ | — | — | returns 0 rows |

## Foreign flow note (MoneyFlowEngine)

`trading_stats()` in 4.0.5 returns company overview (incl. `foreigner_percentage`)
but NO `foreign_volume` column. `MoneyFlowEngine.get_foreign_snapshot()` looks up
`foreign_volume` and therefore still resolves to 0 — same as in 3.4.0 where the
call always failed. The foreign-flow feature has never delivered live volume data.

## Install

```
pip install -e backend/libs/vnstock
```

The editable install covers the `vnstock` + `vnai` imports; no `sys.path` shim is
needed (removed from `VnstockProvider`).
