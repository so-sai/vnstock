# SPEC — Narrative & Audit Trail Layer (A + B)

Ngày: 2026-10-08 · Trạng thái: **đã chốt thiết kế, chờ nghiệm thu bằng dữ liệu phiên 08/10**
Phạm vi: tầng **diễn giải hạ nguồn** cho Quant Engine. Không đụng logic ra quyết định.

---

## 1. Vấn đề

Quant Engine đã có: ingestion guard → làm sạch → regime → causal DAG → decision → risk guard.
Thiếu: **tầng chuyển output máy-đọc được thành người-đọc được**, và **vết kiểm toán cho từng quyết định**.

Hệ quả hiện tại: muốn giải thích "vì sao ENTRY_LOCK=TRUE hôm nay" phải gõ 5–7 lệnh CLI rồi tự ghép bằng tay.

## 2. Phân loại & phạm vi

| Loại | Mục đích | Làm bây giờ? |
|---|---|---|
| **A. Audit trail** | Truy vết quyết định, deterministic, rẻ | ✅ **P1** |
| **B. Daily brief** | Báo cáo người đọc, sau EOD | ✅ **P1** |
| **C. Deep dive** | Phân tích sâu khi gate mở | ❌ P2 — chỉ làm khi có gate pass |

Cả A và B đều **deterministic**: template + data binding. **Không LLM free-form.**

## 3. Nguyên tắc bất biến (không được vi phạm)

1. **Narrative là HẠ NGUỒN của decision.** Không bao giờ chạy ngược: narrative không được sinh số mới, không được suy luận thêm, không được "diễn giải" nguyên nhân.
2. **Mọi con số phải truy được về DB.** Mỗi số trong brief kèm `(nguồn: bảng, ngày)`; có checker tự động (mục 7).
3. **Nếu narrative mâu thuẫn decision → DECISION THẮNG**, và brief phải gắn cờ `⚠️ NARRATIVE_CONTRADICTION` thay vì làm mượt câu chữ.
4. **LLM chỉ được paraphrase**, tuyệt đối không reasoning. Ở bản này: **không dùng LLM**.
5. **Thiếu dữ liệu → ghi `N/A` + lý do.** Không nội suy, không bịa, không "ước tính".
6. **Đọc là read-only**: lớp này không INSERT/UPDATE/DELETE vào `screener_cache.db`.

## 4. Luồng dữ liệu

```
screener_cache.db (read-only)          system_state.json
   ├─ regime_history                     └─ portfolio_critical_lock
   ├─ market_foreign_history                 (ENTRY_LOCK)
   ├─ daily_ohlcv (is_stale=0)
   └─ data_freshness
              │
              ▼  fetch_report_inputs()
      ReportInputs (dataclass, đã kiểm chứng)
              │
              ├──► build_audit_record()  ──►  backend/reports/audit_trail.jsonl   (append)
              └──► render_daily_brief()  ──►  backend/reports/daily_brief_YYYY-MM-DD.md
```

## 5. Output A — `audit_trail.jsonl`

Append-only, 1 dòng JSON / 1 lần chạy. Đường dẫn: `backend/reports/audit_trail.jsonl`.

```json
{
  "ts": "2026-10-08T14:50:03+07:00",
  "as_of": "2026-10-07",
  "command": "daily_brief_builder",
  "decision": {"entry_lock": true, "target_allocation_pct": 0},
  "inputs": {
    "regime": {"date": "2026-10-07", "status": "CRISIS", "score": 0.28, "breadth_pct": 31.1},
    "foreign": {"d1": -942.7, "d3_sum": -3042.0, "days_available": 3},
    "freshness": {"latest_clean": "2026-10-07", "is_stale": false}
  },
  "gates": {"regime_ok": false, "breadth_ok": false, "foreign_ok": false},
  "narrative_contradiction": false,
  "sources": ["regime_history:2026-10-07", "market_foreign_history:2026-10-05..07", "daily_ohlcv"]
}
```

Quy tắc:
- `as_of` = ngày dữ liệu sạch mới nhất (KHÔNG phải ngày chạy) — tránh nhầm lẫn lệch phiên.
- `entry_lock` đọc từ `system_state` (fail-closed: file mất/hỏng → `true`).
- `foreign` **chỉ tính `flow_kind='daily'`** — nếu tính cả `legacy_level` thì tổng sẽ vô nghĩa.
- `narrative_contradiction`: `true` nếu câu chữ sinh ra nói ngược cờ quyết định (xem mục 6.3).

## 6. Output B — `daily_brief_YYYY-MM-DD.md`

### 6.1 Cấu trúc (cố định, không thêm/bớt mục)

```markdown
# Daily Brief — {as_of}  (sinh lúc {ts})

## 1. Trạng thái thị trường
- Regime: {status} (score {score}, {n_phiên} phiên liên tiếp)
- Breadth: {breadth}% (Δ so phiên trước: {delta})
- Dòng ngoại: {d1} tỷ ({streak} phiên liên tiếp {chiều}, lũy kế {n} phiên: {sum})

## 2. Quyết định
- ENTRY_LOCK: {entry_lock}   |   Allocation mục tiêu: {alloc}%
- Lý do: {danh sách điều kiện KHÔNG đạt, trích từ cờ}

## 3. Watchlist
{chỉ các mã có tín hiệu trong ngày: ngoại ròng |Δ| lớn, hoặc KL > 2× TB20}

## 4. Việc tiếp theo
- Chờ: {điều kiện cổng, lấy từ hằng số, KHÔNG phải văn xuôi tự do}

## 5. Nguồn dữ liệu & độ tươi
{mọi nguồn + ngày + is_stale}
```

### 6.2 Quy tắc render
- Mọi số format `dấu phân cách nghìn, 1 chữ số thập phân`; tiền tệ = **tỷ đồng**.
- Mọi dòng số kèm nguồn dạng ngoặc nhỏ: `(regime_history:2026-10-07)`.
- Nếu một input thiếu → `N/A (lý do)` và **vẫn render** phần còn lại (không được crash ra file rỗng).
- Không dùng tính từ định tính không kiểm chứng được ("lành mạnh", "tích cực") trừ khi nó là **tên trạng thái do engine trả về**.

### 6.3 Phát hiện mâu thuẫn (invariant #3)
Ví dụ luật cứng: nếu `entry_lock=true` mà phần "Lý do" không nêu được ít nhất 1 điều kiện không đạt → gắn cờ
`⚠️ NARRATIVE_CONTRADICTION` + set `narrative_contradiction=true` trong audit record. **Không sửa decision.**

## 7. Kiểm chứng (bắt buộc, không phải tuỳ chọn)

`--verify` chạy 3 kiểm tra, in PASS/FAIL:
1. **Traceability**: mỗi số in ra trong brief được re-query từ DB và so khớp (dung sai 0.05).
2. **Invariant #2**: brief không chứa số nào không có trong danh sách đã trace.
3. **Invariant #3**: nếu `entry_lock=true` → phải có ≥1 điều kiện không đạt được nêu.

## 8. Nguồn dữ liệu (chính xác bảng/cột)

| Dữ liệu | Nguồn | Lọc |
|---|---|---|
| Regime | `regime_history` | `ORDER BY date DESC LIMIT 1` (chuỗi liên tiếp đếm ngược theo `status`) |
| Foreign ngày | `market_foreign_history` | `date=? AND flow_kind='daily'` → `SUM(net_value)` |
| Foreign lũy kế | `market_foreign_history` | `date IN (n phiên gần nhất) AND flow_kind='daily'` |
| Giá/KL mã | `daily_ohlcv` | `COALESCE(is_stale,0)=0` |
| Độ tươi | `daily_ohlcv` | ngày có `COUNT(*) >= 300` dòng sạch (khớp ngưỡng Sentinel) |
| ENTRY_LOCK | `backend/data/system_state.json` | fail-closed default `true` |

## 9. CLI

```bash
python backend/scripts/daily_brief_builder.py              # brief cho as_of mới nhất + ghi audit
python backend/scripts/daily_brief_builder.py --date 2026-10-08
python backend/scripts/daily_brief_builder.py --verify      # chạy kiểm chứng
python backend/scripts/daily_brief_builder.py --no-audit    # chỉ render
python backend/scripts/daily_brief_builder.py --json        # in ReportInputs
```

## 10. Tiêu chí nghiệm thu (bài test đầu tiên = phiên 08/10)

1. `backend/reports/daily_brief_2026-10-08.md` tồn tại, đủ 5 mục, không có `N/A` ở mục 1–2.
2. `audit_trail.jsonl` có ≥1 dòng hợp lệ schema, `as_of=2026-10-08`.
3. `--verify` → **3/3 PASS**.
4. Số dòng ngoại trong brief khớp **cùng phép tính** với `ptck.py`/SQL độc lập.
5. Unit test: ≥6 case, gồm ca thiếu dữ liệu (phải ra `N/A`, không crash) và ca mâu thuẫn.

## 11. Ngoài phạm vi (ghi rõ để không trôi việc)

- KHÔNG dùng LLM (kể cả paraphrase) ở bản này.
- KHÔNG gửi email/telegram, KHÔNG xuất PDF.
- KHÔNG đọc/ghi `brain.db`, không đụng `final_decision` logic.
- KHÔNG tự động hoá sửa dữ liệu — lớp này read-only.
