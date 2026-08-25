# Capitulation Paper Ledger — Spec (READ-ONLY research, chưa triển khai)

**Trạng thái:** `SPEC LOCKED` — chưa code. Ledger chỉ được scaffold sau khi spec này được duyệt.
**Mục đích:** Instrumentation để đo cái giá của fail-closed (`P_cap` / `P_cap_final`) mà không đụng `Governor` / threshold `0.70`.
**Quyết định trước đó:** audit pilot ABORT vì `snapshot_index.json` replay pollution + `prediction_log.jsonl` scale corruption → không được rebuild hậu nghiệm thành historical evidence.

## 1. Contract

```
capitulation_paper_ledger.db
  T0 PIT snapshot
    P_cap, P_cap_final, regime, structure
    provenance + params_hash + source timestamp
      ↓
  mature_date = T0 + 30 trading sessions (không phải calendar days)
      ↓
  VNINDEX forward (từ daily_ohlcv)
    H+5 / H+20 / H+30 + effective trading dates
      ↓
  settlement — chỉ khi mature (không settle trước hạn)
```

## 2. Invariants (bắt buộc)

1. **Append-only:** không overwrite snapshot đã frozen. Duplicate `(date, horizon)` → `INSERT OR IGNORE`.
2. **PIT-clean:** snapshot tại `t` chỉ dùng dữ liệu `≤ t`. Nếu không chứng minh được PIT-clean → ghi `status = INVALID_SOURCE`, không cố phục hồi.
3. **No look-ahead episode selection:** candidate episode xác định bằng trạng thái tại `t`, không bằng ex-post minimum / toàn bộ trajectory.
4. **Settlement separation:** snapshot/forecast không được biết forward return trước `mature_date`. Forward lấy từ `daily_ohlcv` (`VNINDEX close(t) → close(t+5/20/30)`), không dùng `prediction_log.jsonl`.

## 3. Guardrails bổ sung

- Không import `src.governor` / `src.engine.orchestrator` / allocation. Module chỉ ĐỌC artifact hoặc OHLCV và ghi ledger riêng.
- Mọi row có `provenance`, `params_hash`, `recorded_at`.
- `mature_date` tính theo trading sessions (bỏ ngày nghỉ, dùng calendar `screener_cache.db` / `daily_ohlcv` distinct dates).
- Không backfill lịch sử từ `snapshot_index.json` nếu `params_hash` / engine đã đổi. Lịch sử cũ chỉ backfill khi chứng minh PIT integrity từng ngày.
- Không dùng kết quả để retune `P_cap > 0.70`.

## 4. Schema dự kiến

```sql
capitulation_snapshots(
  date TEXT PRIMARY KEY,          -- T0 trading date
  p_cap REAL NOT NULL,
  p_cap_final REAL NOT NULL,
  regime TEXT, structure TEXT,
  params_hash TEXT NOT NULL,
  provenance TEXT NOT NULL,       -- ví dụ "capitulation_detector:42616a09ff..."
  source_ts TEXT NOT NULL,
  mature_date TEXT NOT NULL,       -- T0 + 30 trading sessions
  status TEXT NOT NULL             -- VALID | INVALID_SOURCE
);
capitulation_forwards(
  date TEXT, horizon INTEGER,      -- 5/20/30
  return_pct REAL,                 -- (close(t+h)/close(t)-1)*100
  close_t REAL, close_th REAL,
  effective_date_th TEXT,
  provenance TEXT NOT NULL,
  settled_at TEXT,
  PRIMARY KEY(date, horizon)
);
```

## 5. Audit sau khi ledger mature

Chỉ khi đủ forward maturity mới chạy Entry-Lag Audit (read-only, tách khỏi FTSE Gate):

```
P_cap 0.30–0.50 | 0.50–0.70 | >0.70
  → H5/H20/H30: median / mean / CI / N / missingness
  → hit-rate, max adverse excursion, false-positive, entry lag, opportunity cost
```

Nếu `0.50–0.70` có risk/reward tốt → kết luận **"có evidence cho vùng nghiên cứu early-entry"**, sau đó mới thiết kế `Early Recovery Gate` độc lập và kiểm định forward. Không hạ `0.70` trực tiếp.

## 6. Track tách bạch

| Track | Trạng thái |
|---|---|
| FTSE Event Ledger | OBSERVATION — D+5/D+20 tiếp tục |
| Gold H20 | FROZEN — settlement ~16/09 |
| Capitulation Entry-Lag Audit | SPEC LOCKED — chưa code |
| Governor threshold | 🔒 không đụng |
| FTSE Security Transmission Gate | backlog riêng |

## 7. Điều kiện scaffold

Chỉ scaffold sau khi:
- [ ] Spec này được duyệt
- [ ] Xác định nguồn PIT-clean cho T0 (daily_updater EOD artifact hoặc daily_ohlcv + frozen params)
- [ ] Xác định trading calendar source cho mature_date
- [ ] Thống nhất không backfill từ `snapshot_index.json` trừ khi audit PIT từng ngày

ABORT vừa rồi là kết quả tốt — guardrail PIT-clean có hiệu lực.
