# AGENTS

Boot: `sentinel_check.py; kit recall`. CLI: `ptck.py`. Verify: `pytest`.
Python: `backend/.venv/Scripts/python.exe` (system `python3` không tồn tại).

## 3 BẤT BIẾN CỐT LÕI — vi phạm là DỪNG, không thương lượng

**1. Missing > Fake (fail-closed).**
Thà thiếu dòng dữ liệu còn hơn nạp dữ liệu sai lệch. Mọi đường nạp/đọc dữ liệu phải
CHẶN hoặc CÁCH LY khi bất thường (biên độ dị tật, lệch thang giá, phiên trùng/bóng ma,
thiếu bar). Cấm `fillna(0)` ngầm, cấm ép khớp, cấm suy diễn từ một frame không chứa dữ
liệu cần. Không có provenance ⇒ đứng ngoài. Bộ lọc panel lịch sử dùng `VALID_ROW_SQL`,
KHÔNG dùng `is_stale`.

**2. Phân tầng dữ liệu.**
Chỉ số (`INDEX_SYMBOLS`) KHÔNG BAO GIỜ được gộp chung với cổ phiếu trong bất kỳ phép
toán toàn thị trường nào (độ rộng, mẫu số thanh khoản, `SUM(volume)`). Phân biệt rạch
ròi ba thứ khác nhau: *dòng bị lỗi/đã cách ly* · *mã ngừng giao dịch (DORMANT)* · *nguồn
không có bar phiên đó*. Mất độ phủ theo nhiều cơ chế ⇒ phải phân loại và khai báo mẫu
số, cấm gộp thành một con số "mã thiếu" mờ nghĩa.

**3. Kỷ luật kiểm định (Zero-Hallucination · OOS Quarantine).**
Không gọt chân cho vừa giày từ một vài ca cá biệt. Mọi con số trên cửa sổ **OOS 2025–2026
là NON-DECISIONAL**: cấm dùng để tối ưu/tune/chọn tham số. Ý tưởng mới chỉ được kiểm
chứng XUÔI (forward paper-trading) trên giả thuyết tiền đăng ký; evidence theo Regime,
cấm tuyệt đối hoá.

## 4 LUẬT BẤT BIẾN BỔ SUNG — dữ liệu chuỗi thời gian & quyết định (MANDATORY)

**A. "Không biết" ≠ 0 và ≠ nhãn lành.** Trên mọi dữ liệu thị trường (giá, chỉ số, độ rộng,
thanh khoản, regime, hướng giá), giá trị thiếu/không đo được BẮT BUỘC là `None` (SQL `NULL`)
hoặc raise fail-closed — cấm `.get(k, 0.0)` / `.get(k, 1.0)` / `.get(k, "RANGING")` /
`"defensive_avg_chg": 0`. *Vì sao:* `0` là một GIÁ TRỊ ĐO ĐƯỢC. `vol_ratio = 0` tự kích nhánh
`< 0,6` ⇒ nhãn `LOW_VOLUME_REGIME`; `ratio = 0` ⇒ "không có volume spike"; `regime = "RANGING"`
⇒ mở đường cho breakout. Ca 1 / Ca 2 / Ca 3 / Ca 6 đều là biến thể của luật này. Suy giảm phải
được KHAI BÁO (signal + log + nhãn `*_MISSING`/`*_XAC_DINH` + conviction `ZERO`), không được im
lặng rơi vào một default — **đặc biệt ở module bảo vệ rủi ro: thiếu dữ liệu KHÔNG phải là "an toàn"**.

**B. Lưới chỉ mạnh bằng đường đọc yếu nhất — phân biệt HAI chế độ đọc, cấm dùng lẫn:**
- *Chế độ 1 — Live/PIT* (`engine/`, `portfolio/`, `governor/`): đọc giá trị HIỆN HÀNH ⇒ BẮT BUỘC
  `WHERE is_stale = 0 AND VALID_ROW_SQL`. Bar cũ/kém tươi là bar SAI cho quyết định hôm nay.
- *Chế độ 2 — Panel lịch sử / backtest research*: CHỈ `VALID_ROW_SQL`, CẤM `is_stale = 0`
  (rụng ~40% mẫu — xem bất biến 1 ở trên).
- Hai vị từ bắt HAI lớp khác nhau (đo thật: 3.337 dòng `is_stale=1` vẫn hợp lệ về hình dạng;
  1 dòng vi phạm vị từ lại có `is_stale=0`) ⇒ ở chế độ 1, thiếu MỘT trong hai là hở lưới.
- Cả hai chế độ CẤM truy vấn `daily_ohlcv` trần không vị từ: `AVG`/`SUM` dính dòng rác ⇒
  lệch 19–28% và LẬT HƯỚNG giá (đo thật 2026-06-15/16/18 trên 3 trụ cột) ⇒ ra lệnh thoát vị thế oan.

**C. Kỷ luật kiểm thử & commit.** Mỗi bug sửa xong BẮT BUỘC có:
1. Regression test riêng, docstring 3 tầng `[WHY / ROOT CAUSE / INVARIANT]` ở đầu file + `[WHY]`
   ở từng test case, trong đó có ≥ 1 ca "chống vá quá tay" (dữ liệu HỢP LỆ phải đi qua nguyên vẹn).
2. Dẫn chứng TRƯỚC bản vá (số đo thật hoặc fixture chạy trên code cũ) — RED trước, GREEN sau.
3. Exit code THẬT `EXIT_THAT=0`; lệnh có `| tail`/`| head` che mã lỗi thì phải chạy lại không ống
   dẫn trước khi kết luận. Một commit cho một NGUYÊN NHÂN GỐC; `EXIT_THAT≠0` ⇒ KHÔNG commit.

## Quy ước vận hành

- Dữ liệu: OHLCV `YYYY-MM-DD`; ADV_20D ≥ 50k; pos ≤ 10–15% ADV. CAGR = calendar-year;
  PIT period-based only. Entity BANK/SEC/INS/STD; params từ ICB. Mã mới sau 16:00:
  cafef-crawl → backfill → analyze.
- Kỷ luật mã: TDD fail-fast (smoke root, PYTHONPATH sạch, import relative); cấm
  `except: pass`. FusionV2 M2.45/M1.15/A.20/M3.20; stop7/take25/hold15; entry.55/exit.30.
- Cron: ticker là **in-process của Hermes Desktop** — job **one-shot bị retire** khi app
  tắt qua giờ (không chạy, không log). Mọi job bắt buộc chạy phải đặt **recurring** +
  **chốt chặn idempotent** trong script.
- An toàn vốn: `ENTRY_LOCK` = `backend/data/system_state.json:portfolio_critical_lock`,
  **fail-closed = True** khi thiếu/hỏng state file. Kiểm bằng `ptck.py stale status`.

## Chi tiết kỹ thuật nằm ở đây (không chép lại vào Hiến pháp này)

- Vị từ & phân tầng dữ liệu: `src/database/data_validity.py` · `series_quality.py` ·
  `dormant_symbols.py` · `ingestion_counters.py` · `foreign_flow_guard.py`.
- Cổng chất lượng: `src/database/panel_quality.py` (`assert_series_coverage`,
  `assert_market_breadth`, `balanced_panel`, `NON_TRADING_SESSIONS`,
  `detect_duplicate_sessions`).
- Phán quyết & giả thuyết: `backend/src/research/*.md`
  (`capitulation_replay_verdict`, `preregistered_hypotheses`, `breadth_consumers_audit`,
  `oos_2026_non_decisional_note`).
