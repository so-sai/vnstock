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

## 4 LUẬT BẤT BIẾN BỔ SUNG (dữ liệu thị trường & quyết định)

**A. "Không biết" ≠ 0 và ≠ nhãn lành.** Dữ liệu thị trường thiếu/không đo được ⇒ `None` hoặc
raise (fail-closed), kèm khai báo suy giảm (`*_MISSING`). Cấm mọi default "lành": `.get(k, 0)`,
`.get(k, 1)`, `.get(k, "RANGING")`, và hằng số 0 cho trường chưa có nguồn tính.

**B. Đọc `daily_ohlcv` phải theo đúng CHẾ ĐỘ.** *Live/PIT* (`engine/`, `portfolio/`, `governor/`):
`is_stale = 0 AND VALID_ROW_SQL`. *Panel lịch sử / backtest*: chỉ `VALID_ROW_SQL`, KHÔNG dùng
`is_stale`. Cả hai chế độ: cấm truy vấn trần không vị từ.

**C. Sửa bug phải kèm test + dẫn chứng.** Docstring 3 tầng `[WHY / ROOT CAUSE / INVARIANT]` +
`[WHY]` từng test, có ca chống vá quá tay; chứng minh RED trước — GREEN sau; `EXIT_THAT=0` thật
(cấm để `| tail`/`| head` che mã lỗi); 1 commit = 1 nguyên nhân gốc.

Số đo, bằng chứng và phán quyết: `backend/src/research/*.md` + `tech_debt_backlog.md`.
Công cụ quét: `scripts/scan_timeseries_hazards.py`.

## 5 LUẬT VẬN HÀNH AGENT (CLI-FIRST · [WHY] · CHỐNG TRÔI DẠT)

**D. CLI-FIRST — cấm tự đẻ script thay CLI.** Trước khi viết bất kỳ script mới nào,
BẮT BUỘC tra `CLI_INDEX.md` + `python ptck.py <lệnh> --help`. Nếu nghiệp vụ đã có lệnh
⇒ BẮT BUỘC dùng CLI, cấm tạo `run_*.py`/`probe*.py` thay thế. Ngoại lệ duy nhất:
research runner trong `backend/src/research/` theo đúng điều kiện ở `CLI_INDEX.md`
(docstring 3 tầng + đăng ký + `NON-DECISIONAL`). Scratch probe chỉ ở `%TEMP%\opencode`,
kết quả có giá trị phải hợp nhất vào module + test chính thức, không commit scratch.

**E. NGUYÊN TẮC `[WHY]` — code không có lý do là code sẽ bị xóa oan.** Mọi hằng số,
công thức, ngưỡng lọc, phạt trượt giá, hệ số điều chỉnh, hoặc khối xử lý bug dữ liệu
vi cấu trúc BẮT BUỘC có comment `# [WHY]: <lý do thực tế> — xóa/sửa sẽ gây <rủi ro>`
đặt NGAY TRÊN dòng code đó. Cấm comment chỉ kể `WHAT`/`HOW`. Người/Agent sau đọc code
mà không thấy `[WHY]` ở một hằng số "lạ" thì phải dừng, không được tự ý "dọn dẹp".

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
