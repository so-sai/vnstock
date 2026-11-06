# Nghiệm thu Mục 27 — Regression Armor (Chốt #2, #3, #5) — **NON-DECISIONAL**

> **Tuyên bố phạm vi:** toàn bộ số liệu trong tài liệu này là **số đo vận hành** (đếm dòng,
> đếm phiên, thời điểm ghi) — **không phải** kết quả nghiên cứu, **không** dùng để tối ưu
> tham số, **không** tạo tín hiệu giao dịch. Cửa sổ OOS 2025–2026 vẫn **NON-DECISIONAL**
> (AGENTS §3). `ENTRY_LOCK = ON`, danh mục 100% tiền mặt — tài liệu này không thay đổi điều đó.

**Ngày nghiệm thu:** 06/11/2026 · **Nghị quyết:** NQ-17 §II.1, §III, §IV
**Trạng thái:** Mục 27 ✅ **DONE** · Mục 26 ✅ DONE (trước đó) · Mục 22 ⧗ OPEN (nợ đo được, nêu ở §5)

---

## 0. Phán quyết ngắn

| Hạng mục | Phán quyết | Commit |
|---|---|---|
| Chốt #2 — Gap-aware Sync, **phương án (B)** | ✅ DONE | `4104639` |
| Chốt #3 — Date-set invariant, **scoped** (epoch + rolling 60/20) | ✅ DONE | `6e013d8` |
| Chốt #5 — Restatement Audit Guard | ✅ DONE | `c6fa870` |
| *Phát sinh:* lịch Quốc khánh 2026 lệch Rust | ✅ DONE | `3bebe15` |
| Chốt #4 — lint call-site (Rust) | ⧗ **DEFERRED** | `src/main.rs` dirty (agent khác) |

**Regression: 185/185 PASS** (154 test cũ + 31 test mới), `EXIT_THAT=0` thật.

---

## 1. Phát hiện quan trọng nhất: lịch phiên Python lệch Rust (31/08/2026)

Khi chuẩn bị cắm Chốt #2, tôi đo trước và thấy bộ dò khuyết phiên sẽ **báo động giả mỗi
ngày** vào `2026-08-31`. Truy nguyên:

| Bằng chứng | Kết quả |
|---|---|
| `src/main.rs:839` | `VN_HOLIDAYS` **có** `2026-08-31` và `2026-09-01` |
| `src/main.rs:2925` (unit test) | `missing_trading_days_between("2026-08-31","2026-09-03") == []` |
| `backend/src/daily_updater.py` (trước sửa) | chỉ có `2026-09-02`; `2026-09-01` lọt qua nhờ `NON_TRADING_SESSIONS`; `2026-08-31` **không được xử lý ở đâu** |
| `daily_ohlcv` / `market_wide_breadth_daily` / `regime_history` | **0 bar / 0 dòng / 0 dòng** cho cả `2026-08-31` và `2026-09-01` |

⇒ Đây **đúng là "The Seam"** mà Hội đồng nêu: hai ngôn ngữ giữ hai bản lịch khác nhau. Kỳ nghỉ
Quốc khánh 2026 là **31/08–02/09** (thứ Hai → thứ Tư), không phải chỉ 02/09.

**Sửa:** thêm `2026-08-31`, `2026-09-01` vào `VN_HOLIDAYS` kèm `[WHY]` + bằng chứng; sửa lại
khẳng định **sai** trong test cũ (`previous_session("2026-09-03")` trả `2026-08-31`) thành
`2026-08-28`. RED 2 → GREEN 12/12; có ca chống-vá-quá-tay "thứ Hai thường vẫn là phiên".

> Nếu không sửa bước này, Chốt #2 sẽ in CRITICAL **mỗi ngày** và Chốt #3 sẽ đỏ vĩnh viễn ⇒
> cả hai bị tắt trong tuần đầu. Bài học: **đo trước khi viết cổng**, không viết cổng theo
> niềm tin.

---

## 2. Chốt #2 — Gap-aware Sync, **phương án (B)**: báo động, KHÔNG tự bù

**Quyết định của Hội đồng (NQ-17 §IV):** `daily-update` chỉ cảnh báo; `eod-run` gặp Chốt #1 ⇒
**exit 4**; người trực/tiến trình ngoài giờ chạy `daily-update --date YYYY-MM-DD`.

**Đã làm:** `session_continuity.detect_session_gaps(conn, as_of, lookback_sessions=60)` — hàm
**thuần, chỉ đọc, không gọi mạng**; cắm vào `daily_updater.alert_session_gaps()` **trước toàn
bộ đường cào** (`update_vnindex`), để cảnh báo hiện ra dù phần cào sau đó chết giữa đường.

Phân loại **rạch ròi** (AGENTS §2), không gộp thành một con số "mã thiếu" mờ nghĩa:

| Loại | Định nghĩa | Ý nghĩa vận hành |
|---|---|---|
| `missing` | 0 bar hợp lệ | dữ liệu chưa về → phải backfill |
| `partial` | > 0 nhưng < 100 bar | **ingest chết giữa đường** — nguy hiểm hơn `missing` |
| `stale_only` | có dòng nhưng toàn `is_stale = 1` | nguồn trả dữ liệu cũ → bù lại vẫn ra dữ liệu cũ |

Ngưỡng `MIN_VALID_BARS_PER_SESSION = 100` có `[WHY]` + số đo (1.666 phiên ≥ 100 mã; ~890 mã
giao dịch/phiên). Ngày nghỉ/phantom holiday **không nằm trong cửa sổ** (cửa sổ tính bằng
**phiên**, không phải ngày lịch) ⇒ không bao giờ bị báo khuyết.

**Bảo vệ chống trôi dạt:** test `test_alert_never_calls_backfill` patch
`src.services.market_backfill.backfill_market_history` thành recorder — nếu một "cải tiến" sau
này thêm tự bù, test **đỏ ngay**. Test `test_alert_failure_records_error_not_zero` khoá nguyên
tắc "không biết ≠ 0": quét lỗi ⇒ `session_gaps = None` + `session_gaps_error`, **không** `[]`.

**RED → GREEN:** 7 `ImportError` (chưa có `detect_session_gaps`/`alert_session_gaps`) → 9/9.

---

## 3. Chốt #3 — Bất biến tập ngày, **phạm vi hợp đồng**

**Thiết kế (NQ-17 §II.1):**

- Thị trường: **60 phiên gần nhất** cho `daily_ohlcv` (≥ 100 bar hợp lệ),
  `market_wide_breadth_daily`, `regime_history`.
- Kế toán: **20 phiên gần nhất** cho `paper_equity_curve` + `eod_run_ledger`;
  toàn bộ từ `CONTRACT_EPOCH = 2026-06-15` dùng cho **báo cáo nợ**, không fail-closed.
- Mẫu số là **LỊCH PHIÊN**, không phải một bảng dữ liệu.

**Vì sao mẫu số phải là lịch:** 04/11/2026 thiếu ở **cả** `paper_equity_curve` **và**
`eod_run_ledger`. Nếu chỉ so hai bảng kế toán với nhau thì "cùng khuyết" trông y hệt "khớp" —
phép kiểm tra tự mù đúng vào sự cố cần bắt.

**Số đo 06/11/2026 (sau khi sửa lịch 31/08):**

| Phạm vi | Kết quả |
|---|---|
| 60 phiên `2026-08-11 … 2026-11-05` | **0 phiên thiếu** ở cả 3 bảng |
| 20 phiên kế toán `2026-10-09 … 2026-11-05` | **0 phiên thiếu** ở equity và ledger |
| Nợ kế thừa từ `2026-06-15` | equity thiếu **22** phiên, ledger thiếu **54** phiên, lỗ hổng **muộn nhất `2026-10-02`** |

**Không có tham số "bỏ qua ngày X"** trong `assert_session_set_alignment` — nợ kế thừa phải
được nêu **tên và số** ở test, để không ai thêm ngày vào danh sách miễn trừ trong im lặng.
Test `test_live_legacy_accounting_gaps_match_measured_baseline` **ghim** 22/54 và chặn mọi
lỗ hổng mới sau `2026-10-02` ⇒ nếu cron lại miss một phiên, số đếm đổi ⇒ **đỏ ngay**.

**RED → GREEN:** 10 `ImportError` → 10/10 (gồm "04/11 khuyết ở CẢ HAI bảng kế toán vẫn đỏ",
"nợ cũ ngoài cửa sổ không chặn nhưng hỏi toàn bộ từ epoch thì phải thấy").

---

## 4. Chốt #5 — Restatement Audit Guard

**Sự kiện phải chặn (đo được):** 05/11/2026 17:31, `market_wide_breadth_daily` của phiên
**đã chốt** bị ghi lại: 02/11 `−47,73 → −48,578`, 03/11 `+4,648 → +5,276` (total_active
839 → 834), **không một dòng kiểm toán** nào nêu ai đổi / vì sao.

**Số đo buộc phải đổi thiết kế** (nếu làm đúng nguyên văn "cấm UPDATE phiên quá khứ"):

| Bảng | Số đo 06/11/2026 | Hệ quả |
|---|---|---|
| `market_wide_breadth_daily` | **1.707/1.708** dòng cùng `created_at = 2026-11-05T15:41:34` (một lần backfill toàn lịch sử) | fail-closed toàn bảng ⇒ **1.706 "vi phạm" vĩnh viễn** |
| `regime_history` | **KHÔNG có cột `created_at`** | không thể phát hiện ghi đè bằng dấu thời gian |
| `eod_write_audit_ledger` | `status='RESTATED'` **đã tồn tại**, đã dùng 2 lần cho `regime_history` (22/10, 23/10) | kế thừa quy ước, **không phát minh** định dạng mới |

⇒ Cổng chính phải nằm ở **thời điểm GHI**, không phải quét lịch sử. Quét ngược chỉ từ
`RESTATEMENT_GUARD_EPOCH = 2026-11-06` (mốc vận hành), và **khai báo tường minh**
`TABLES_WITHOUT_PROVENANCE = ("regime_history",)` thay vì im lặng coi là sạch.

**Cơ chế (fail-closed thật):** `_upsert_rows` đọc trạng thái **cũ trước khi** `INSERT OR
REPLACE`; nếu `date < processing_date` **và** chữ ký 9 trường thay đổi ⇒ ghi dòng `RESTATED`
(`caller_module`, `input_hash` 64 hex của old/new, `reason`) **trong cùng transaction** với
`commit=False`. Audit lỗi ⇒ `with con:` **rollback** ⇒ ghi đè trở thành **bất khả thi**, không
chỉ "bị phát hiện sau".

**Không dựng lại audit cho sự kiện 05/11:** giá trị cũ đã bị ghi đè; tạo dòng `RESTATED` hôm
nay sẽ là **dựng lại bằng chứng** chứ không phải ghi nhận — vi phạm "missing > fake". Sự kiện
được ghi ở đây kèm số đo, đó là mức trung thực tối đa còn có thể.

**RED → GREEN:** 9 `ModuleNotFoundError` → 9/9 (gồm then chốt **"audit lỗi ⇒ ROLLBACK, dữ liệu
cũ giữ nguyên"** và 2 ca chống-vá-quá-tay: tạo mới phiên cũ / refresh phiên hiện tại **không**
mang nhãn `RESTATED`).

---

## 5. Nợ còn lại — ĐO ĐƯỢC, CHƯA SỬA (không giấu vào "nợ cũ")

| # | Nợ | Số đo 06/11/2026 | Ghi chú |
|---|---|---|---|
| 1 | `paper_equity_curve` thiếu 22 phiên từ `2026-06-15` | lỗ hổng muộn nhất `2026-10-02` | phần lớn thuộc Mục 22 |
| 2 | `eod_run_ledger` thiếu 54 phiên từ epoch | lỗ hổng muộn nhất `2026-10-02` | ledger ra đời muộn hơn equity |
| 3 | `paper_equity_curve` có **1 dòng cho phiên phantom `2026-09-01`** | 1 dòng | ngày KHÔNG giao dịch — cần thanh lý riêng (chưa làm) |
| 4 | **Chốt #4** — lint call-site Rust | — | `src/main.rs` **dirty** bởi agent khác (`4eca9b4`); Hội đồng: không chạm |
| 5 | Mục 22 — 10 phiên `2026-09-18 … 2026-10-02` | nằm trong nợ #1/#2 | chờ đối soát `corporate_actions` |

**Không có hạng mục nào trong bảng này bị "vá mồm"**: chúng được đếm, ghim số trong test, và
một lỗ hổng **mới** sẽ làm test đỏ ngay.

---

## 6. Bằng chứng kiểm định

| Chốt | RED (trước) | GREEN (sau) |
|---|---|---|
| Lịch 31/08 | 2 failed | 12/12 |
| #2 Gap-aware sync | 7 `ImportError` | 9/9 |
| #3 Date-set scoped | 10 `ImportError` | 10/10 |
| #5 Restatement audit | 9 `ModuleNotFoundError` | 9/9 |
| **Regression đầy đủ** | — | **185/185 PASS** (`EXIT_THAT=0`) |

Lệnh kiểm chứng (PowerShell, không dùng pipe che exit code):

```powershell
$env:PYTHONPATH=""; $env:PYTHONIOENCODING="utf-8"
& "backend\.venv\Scripts\python.exe" -m pytest `
  backend/tests/test_trading_calendar_2026_11_06.py `
  backend/tests/test_gap_alert_chot2_2026_11_06.py `
  backend/tests/test_dateset_invariant_chot3_2026_11_06.py `
  backend/tests/test_restatement_guard_chot5_2026_11_06.py `
  backend/tests/test_session_continuity_2026_11_05.py -q
"EXIT_THAT=$LASTEXITCODE"
```

---

## 7. Điều tài liệu này **KHÔNG** chứng minh

- **Không** chứng minh chiến lược có lợi nhuận, không chứng minh mô hình đúng. Đây là hạ tầng
  **chống lỗi vận hành**, không phải bằng chứng alpha.
- **Không** thay đổi trạng thái `ENTRY_LOCK = ON` (100% tiền mặt).
- **Không** dùng số liệu OOS 2025–2026 để chọn/tinh chỉnh tham số — mọi con số ở đây là **đếm
  dòng và đếm phiên**, không phải hiệu suất.
- **Không** khẳng định `regime_history` sạch: bảng này **không có provenance thời gian**, nên
  "không phát hiện được" ≠ "không có ghi đè" (AGENTS §4A). Từ 06/11/2026 trở đi, mọi ghi đè
  lên nó **bắt buộc** đi qua `record_restatement`.
