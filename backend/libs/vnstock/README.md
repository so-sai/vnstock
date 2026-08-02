# vnstock (PTCK-VN patched fork)

Vendored copy of the `vnstock` library, installed as an editable local package for
PTCK-VNSTOCK.

## Why a fork

The upstream `vnstock` requires an API key registration for several data sources.
This fork patches `vnstock/core/utils/auth.py` to bypass the registration gate
(returns an emulated "Sponsor" tier) so PTCK-VN keeps working without a key.

## Patches vs upstream (do not overwrite on upgrade)

- `vnstock/core/utils/auth.py` — `register_user()` / `change_api_key()` return
  `True`; `check_status()` returns an emulated Sponsor tier
  (`VNS-PATCHED-EMULATOR`). Upgrading to upstream will LOSE this bypass and the
  VCI/TCBS data sources will start returning empty responses (`KeyError: 'data'`).
- `vnstock/core/utils/upgrade.py` — `update_notice()` is a no-op. Suppresses the
  `pip install vnstock --upgrade` prompt that would replace this fork with
  upstream.

## Working data sources (verified 2026-08-02)

| Source | history | financials | notes |
|--------|---------|------------|-------|
| VCI    | ✅ | ❌ | GraphQL returns `{}` without API key; history works via Referer/Origin headers |
| kbs    | ✅ | — | history + price_board verified |
| tcbs   | ❌ | — | returns 0 rows |
| dnse   | ❌ | — | returns 0 rows |

## Install

```
pip install -e backend/libs/vnstock
```

After installing, remove the `sys.path` shim in
`backend/src/providers/vnstock_provider.py` (`_ensure_libs_importable()`).
