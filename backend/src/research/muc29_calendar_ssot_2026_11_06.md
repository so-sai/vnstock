# NGHIỆM THU MỤC 28 & 29 — Lịch giao dịch, Chốt #3 hai chiều, SSOT (NQ-18 §II)

**Ngày:** 06/11/2026 · **Phạm vi:** commits `f30d8c5` → `60eab8d` (5 commit, tiếp sau `2ef6e9f`)
**Nhãn:** `NON-DECISIONAL` — không dùng số liệu ở đây để chọn tham số hay ra tín hiệu.

---

## 0. Vì sao phải làm trước khi đóng Mục 27

NQ-18 §II phê chuẩn mở Mục 28/29. Nhưng khi **đo** để dựng SSOT, phát hiện lịch hiện tại
**sai 16 ngày** (2 ngày thừa + 14 ngày thiếu, 2023–2026). Đóng băng một lịch sai vào "nguồn
sự thật duy nhất" sẽ biến sai lầm thành hợp đồng. Vì vậy thứ tự thực thi là: **sửa lịch bằng
số đo → hợp nhất nguồn → thêm cổng chiều ngược**. Đó là nội dung 5 commit dưới đây.

Nguyên tắc phân loại dùng xuyên suốt (AGENTS §2 — ba thứ khác nhau, không gộp):

| Hiện tượng | Kết luận | Bằng chứng |
|---|---|---|
| Phiên theo lịch mà **cả 3 bảng độc lập** trống | Sàn đóng cửa ⇒ **lịch thiếu ngày nghỉ** | 3 writer độc lập cùng trống |
| Ngày lịch coi là nghỉ mà có **≥100 bar hợp lệ** | **lịch thừa ngày nghỉ** (đè mất phiên thật) | bar + volume ngang phiên thường |
| Ngày không giao dịch, **0 bar** nhưng có dòng dẫn xuất | **dòng ma** — nợ dữ liệu, *không* phải lỗi lịch | thanh lý riêng, có audit |

---

## 1. `f30d8c5` — Lịch THỪA: `2024-02-15` / `2024-02-16` là PHIÊN

Cả Python và Rust đều xếp hai ngày này vào nghỉ Tết Giáp Thìn. Đo trên `screener_cache.db`:

| Ngày | Bar | Volume |
|---|---:|---:|
| 2024-02-07 (phiên thường) | 1.093 | 1.555.833.512 |
| 2024-02-08 → 2024-02-14 | **0** | **0** |
| **2024-02-15** | **1.092** | **1.995.796.205** |
| **2024-02-16** | **1.139** | **1.819.204.976** |
| 2024-02-19 (phiên thường) | 1.114 | 2.325.529.617 |

**Kết luận:** kỳ nghỉ là 08/02–14/02; HOSE giao dịch lại **thứ Năm 15/02**. Không phải dữ liệu
sai — **lịch sai**. Hệ quả trước khi sửa: `previous_session("2024-02-19") == "2024-02-07"` ⇒ mọi
phép "biến động 1 phiên / 5 phiên / 20 phiên" quanh Tết 2024 **gộp 3 phiên thành một**.

Test: `test_calendar_tet2024_2026_11_06.py` (3 test) — gồm ca **chống vá quá tay** (08–14/02
phải **vẫn** là nghỉ; nếu ai xoá cả kỳ nghỉ thì `previous_session("2024-02-15")` sai) và ca
**ghim bằng chứng vào CSDL thật** (mất bar 15–16/02 ⇒ đỏ).

## 2. `8fd7dd7` — Lịch THIẾU: 14 ngày nghỉ 2023–2026

Quét toàn dải `2023-01-01..2026-11-05`, mọi ngày được lịch coi là phiên mà **cả 3 bảng cùng
trống** trong khi hai bên kỳ nghỉ đều ~1.000 bar:

```
2023-01-02                     (nghỉ bù Tết Dương lịch; 30/12/2022 = 1.014 bar, 03/01 = 998)
2023-01-20, 23, 24, 25, 26     (Tết Quý Mão; 19/01 = 985, 27/01 = 1.013)
2023-05-01, 02, 03             (30/04–01/05; 28/04 = 1.021, 04/05 = 1.021)
2023-09-01, 04                 (Quốc khánh; 31/08 = 1.087, 05/09 = 1.098)
2024-04-29                     (Giỗ Tổ + 30/04; 26/04 = 1.120, 02/05 = 1.109)
2025-09-01                     (Quốc khánh; 29/08 = 1.149, 03/09 = 1.045)
2026-01-02                     (Tết Dương lịch; 31/12/2025 = 981, 05/01/2026 = 963)
```

Test: `test_calendar_missing_holidays_2023_2026.py` (4 test) — mỗi kỳ nghỉ phải **liền mạch**
(`previous_session` nhảy về phiên cuối trước kỳ nghỉ, không dừng giữa kỳ nghỉ), và ngày giao dịch
lại **vẫn** là phiên (chống biến cả tuần thành nghỉ để "cho khớp").

## 3. `20295b5` — Chốt #3 CHIỀU NGƯỢC: dòng trên ngày không giao dịch cũng là lỗi

`assert_session_set_alignment` (Chốt #3, `6e013d8`) chỉ kiểm `expected ⊄ present` — "phiên cần
có mà thiếu". Nó **không** kiểm `present ⊄ expected` — "có dòng trên ngày sàn đóng cửa".

Đo được lỗ hổng **đang sống thật** (06/11/2026):

| Bảng | Dòng trên ngày không giao dịch | Trong 60 phiên | Từ epoch |
|---|---:|---:|---:|
| `daily_ohlcv` | 2 (`2024-02-15/16` — phiên thật, đã sửa) | 0 | 0 |
| `market_wide_breadth_daily` | 3 | **1** (`2026-09-01`) | **1** |
| `regime_history` | 3 | **1** (`2026-09-01`) | **1** |
| `paper_equity_curve` | **1** (`2026-09-01`) | **1** | **1** |
| `eod_run_ledger` | 0 | 0 | 0 |

**Hệ quả đo được:** `paper_equity_curve` có **một điểm vốn không ứng với phiên nào** — mọi
CAGR/drawdown/max-DD tính qua nó lệch một phiên; và mọi consumer đọc "phiên liền trước" quanh
01/09/2026 coi ngày lễ là phiên có dữ liệu.

**Sửa:** thêm `market_spurious` / `accounting_spurious` vào `collect_session_alignment`; `ok` gồm
**cả hai chiều**; `assert_session_set_alignment` ném lỗi với nhãn `[NON-SESSION]` nêu **tên ngày
+ tên bảng**. Phạm vi vẫn **thật**: dòng ngoài cửa sổ là nợ kế thừa (không chặn), nhưng mở rộng
cửa sổ thì **phải thấy** (chống "cửa sổ che mắt", NQ-17 §II.1).

Test: `test_dateset_reverse_direction_2026_11_06.py` (6 test) — gồm ca **chống báo động lan**
(chỉ bảng thật sự có dòng mới bị nêu), ca **DB sạch ⇒ `ok` True** (chống vá quá tay), và 2 test
trên **CSDL thật**: ghim chính xác tập 3 dòng `2026-09-01` + chứng minh cổng **thật sự ném lỗi**
(nếu test này xanh thì cổng đã mù).

> **Điều chỉnh test cũ, không nới lỏng:** `test_dateset_invariant_chot3_2026_11_06.py::
> test_live_market_alignment_is_exact` trước khẳng định `ok is True`. Khi `ok` mang thêm nghĩa
> "không có dòng thừa", khẳng định đó **không còn đúng trên CSDL thật**. Nay test khẳng định
> **phần chiều thiếu** (mục đích gốc của sentinel) và **ghim** 3 dòng thừa; thiếu một phiên vẫn đỏ.

## 4. `60eab8d` — Mục 29: MỘT nguồn sự thật

- **SSOT:** `backend/src/config/weekend_holidays.json` (file đã tồn tại, `time_series_aligner`
  đã đọc) — không tạo file thứ ba. 59 ngày lễ: 2023–2026 **kiểm chứng bằng dữ liệu**, 2027
  **kế thừa từ Rust** với `provenance_2027` khai báo rõ là chưa kiểm chứng được.
- **Python:** `daily_updater._load_vn_holidays()` **fail-closed** (`RuntimeError` khi thiếu/hỏng/rỗng
  /sai định dạng); `VN_HOLIDAYS = _load_vn_holidays()`; xoá ~80 dòng hard-code.
- **Parity Rust (ghi nợ, không giả vờ đã xong):** 15 ngày lệch — Rust **thừa** `2024-02-15/16`,
  **thiếu** 12 ngày 2023 + `2024-04-29`. Ghim tên trong test ⇒ lệch **mới** là đỏ ngay. Không sửa
  `src/main.rs` (agent Rust đang giữ file, NQ-17 §II.2).

Test: `test_calendar_ssot_2026_11_06.py` (5 test) — audit **3 chiều** với dữ liệu thật:
A (phiên mà 3 bảng trống = lịch thiếu) · B (ngày nghỉ mà ≥100 bar = lịch thừa) · C (dòng dẫn xuất
trên ngày nghỉ = dòng ma, ghim `2026-09-01`).

---

## 5. Kiểm định

| Bước | Kết quả |
|---|---|
| RED trước mỗi bản vá | `f30d8c5` 1 failed · `8fd7dd7` 2 failed · `20295b5` 6 KeyError · `60eab8d` 4 failed |
| GREEN cục bộ | 34/34 → 38/38 → 44/44 → 5/5 |
| **Regression toàn bộ** | **203/203 PASS** (`EXIT_THAT=0` thật, không qua pipe) |
| Trạng thái an toàn vốn | `Critical Lock: True`, `Hard Shutdown: False` |
| Cửa sổ sống (đo lại **sau** khi sửa lịch) | 60 phiên thị trường `2026-08-11..11-05` **0 thiếu**; 20 phiên kế toán `2026-10-09..11-05` **0 thiếu** |

## 6. Nợ còn lại (chuyển sang Mục 28, **không** giấu)

1. **3 dòng trên ngày không giao dịch `2026-09-01`** (breadth/regime/equity) — Chốt #3 nay **đỏ**
   khi quét từ epoch ⇒ buộc phải thanh lý thật. Thanh lý là **ghi/xoá dữ liệu** ⇒ cần GO riêng và
   phải để lại audit, không xoá lén.
2. **Rust vẫn là bản sao thứ hai** (15 ngày lệch) — chờ `src/main.rs` sạch.
3. `time_series_aligner._load_vn_holidays()` vẫn **fail-open** (trả `set()` khi file hỏng) — cùng
   file SSOT nhưng khác chính sách lỗi; không chặn EOD nên hạ ưu tiên.
4. `paper_equity_curve` 22 phiên · `eod_run_ledger` 54 phiên (từ epoch) — Mục 22/28.
