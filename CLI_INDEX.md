# 🧭 CLI_INDEX — DANH MỤC LỆNH NỘI BỘ (PTCK_VNSTOCK)

> **Đọc file này TRƯỚC KHI viết bất kỳ script mới nào.**
> Entrypoint duy nhất: `ptck.py` (root). Python: `backend/.venv/Scripts/python.exe`.
> Danh sách dưới đây sinh từ `ptck.py --help` (2026-10-28) — nếu lệch, chạy
> `python ptck.py <lệnh> --help` làm chuẩn, rồi cập nhật lại file này.

```powershell
# Mẫu gọi chuẩn (PowerShell)
$env:PYTHONPATH=""
& "D:\04. Software\opensource_contrib\PTCK_VNSTOCK\backend\.venv\Scripts\python.exe" ptck.py <lệnh> [cờ]
```

Cờ toàn cục: `--verbose-lang {compact,annotated,full,auto}` (mọi lệnh),
`--force-stale` (chỉ debug/backtest — **ghi ledger**
`backend/data/stale_override_ledger.jsonl`).

---

## 1. Dữ liệu OHLCV & Vĩ mô

| Lệnh | Chức năng | Cờ chính |
|---|---|---|
| `daily-update` | Cập nhật EOD theo ngày (KBS batch) | `--date YYYY-MM-DD`, `--manifest`, `--batch-size`, `--throttle` |
| `eod-run` | EOD Runner: retry + idempotent + SQLite lock | `--date`, `--force`, `--no-catchup`, `--retry-sleep` |
| `fetch-macro` | Fed/FRED/US10Y/USD/Brent + VGB 10Y | `--force`, `--skip-vgb` |
| `sbv-update` | 5-step recovery lãi suất SBV (backup→fixtures→pytest→scrape) | — |
| `backfill-history` | Khôi phục lịch sử mã thiếu | — |
| `backfill-macro` | Backfill 1 năm PTD macro tickers | — |
| `backfill-regime` | Backfill `regime_history` ngày thiếu | — |
| `check-calendar` | Đối chiếu lịch lễ với nguồn online | — |
| `cafef-crawl` | Crawl BCTC 20 quý | `--symbols`, `--source {vci,tcbs,cafef,api,vietstock,synthetic}`, `--full` |
| `import-financials` | Import PE/PB/... từ CSV/XLSX → z-scores | `--file`, `--sheet` |
| `bctc-20q` | Import 20 quý BCTC → z-scores định giá nội tại | — |
| `db` | Vacuum/stats/optimize/backup SQLite | `db {vacuum,stats,optimize,backup}` |

## 2. Kiểm toán dữ liệu, cổ tức, PIT

| Lệnh | Chức năng | Cờ chính |
|---|---|---|
| `dividend-audit` | Kiểm toán DRIP vs tiền gửi 12 tháng Big 4 | `--symbol FOC\|VEA\|SMB\|PAT\|DSN\|ALL`, `--capital`, `--json` |
| `symbol-audit` | Giải phẫu 4 cổng 1 mã (Hàng hóa/Ngành→Định giá→Dòng tiền→Kỹ thuật) | `--symbol` (bắt buộc) |
| `pit-audit` | Đối soát Period End + Ingested (chống look-ahead) | `--symbols`, `--as-of`, `--json-out` |
| `data-quality` | Độ tin cậy dữ liệu (TẦNG 0) | — |
| `gap-analyzer` | Missing symbols/dates | — |
| `forensic-fpr` | Tỷ lệ báo động giả trên rổ VN30 | — |
| `evidence-ledger` | Decision candidates + budget + opportunity cost | `{list,resolve,opportunity-cost,stats}`, `--year`, `--hold-days` |
| `why-audit` | Linter `[WHY]` **report-only**: liệt kê hằng số/ngưỡng thiếu lý do (AGENTS luật E) | `--root` (mặc định `backend/src`), `--top`, `--category {constant,literal-threshold,parse-error}`, `--json`, `--strict` |

**`why-audit` — report-only, KHÔNG chặn CI.** Mặc định luôn exit 0 kể cả khi có vi
phạm; chỉ `--strict` mới trả 1 (dùng khi muốn tự kiểm một thư mục nhỏ). Lệnh này
không đọc DB, không sửa gì, nằm trong `BYPASS_COMMANDS`. Phạm vi nhận diện **cố ý
hẹp** và ghi ngay trong báo cáo: (a) hằng số số học cấp module/lớp (kể cả biểu thức
số, bỏ 0/±1), (b) literal số \|x\|≥2 trong điều kiện `if`/`while`/`assert`, bỏ cặp
so sánh có `len(...)` (kiểm tra số phần tử, không phải ngưỡng). Đo 29/10/2026 trên
`backend/src`: **1.047 vi phạm** (constant 581, literal-threshold 466), 0 parse-error
— đây là số đo nền để theo dõi mức tuân thủ tăng/giảm, không phải danh sách việc
phải vá hết. Chạy: `python ptck.py why-audit --top 10`.

## 3. Tín hiệu, sức khỏe doanh nghiệp, dòng vốn

| Lệnh | Chức năng |
|---|---|
| `erl-scan` | Entity Resilience Layer — quét 1.514 mã + whitelist top 30 (`--top`, `--vn30`, `--whitelist`) |
| `flow-map` | Bản đồ dòng vốn liên thị trường 4 tầng |
| `scan` / `analyze` | Elite scanner / phân tích dòng tiền danh mục |
| `watch` | Giám sát volume spike (phiên xác nhận scale-in) |
| `sector` | Sector State Engine — 19 ngành ICB trên 4 trụ |
| `health-v2` / `health-engine` | 5-organ latent state / health_ratios từ `financial_facts` |
| `csi-scan` / `csi-explain` | Contextual Security Index toàn thị trường / causal DAG trace |
| `archetype` / `economic-engine` / `competitive` | Giai đoạn 1: DNA / propagation / moat |
| `factor-exposure` / `contextual-health` / `capital-allocation` | Giai đoạn 2–4 |

## 4. Governor, quyết định, hiệu chuẩn

| Lệnh | Chức năng |
|---|---|
| `governor` | Bayesian Expected Utility + Kelly sizing |
| `final-decision` | Bộ não quyết định cuối cùng |
| `snapshot` / `snapshot-index` | Ảnh chụp thị trường / lịch sử snapshot |
| `calibrate` | Log-Loss, ECE, Beta-posterior LR |
| `evidence` | LAW-004 Evidence Engine — Beta posterior reliability |
| `causal` / `counterfactual` / `model-registry` | CausalEdge / What-if 6 nút / 3 hypotheses BMA |
| `drift` / `system-audit` | Drift layer / hợp nhất drift+calibration+model health |
| `regime` / `structural` / `ddi` | Regime chi tiết / lệch cấu trúc / Delta Divergence Index |
| `governor-report` | Phân rã đóng góp 3 mô hình (Vĩ mô 20% / Định lượng 30% / Regime 50%) |
| `macro` / `macro-state` / `transmission` | Macro Governor Gatekeeper / MacroState / Liquidity→Credit→Confidence |

## 5. An toàn vốn & vận hành

| Lệnh | Chức năng |
|---|---|
| `stale {status,writeoff,reclaim,escrow}` | StalePositionManager — **`stale status` là cổng kiểm `ENTRY_LOCK`** |
| `twap` | TWAP Executor thanh lý stale positions |
| `break-glass` | Override Hard Shutdown (có kiểm soát) |
| `status` | Sức khỏe hệ thống |
| `clear-crisis` | Reset `crisis_cooldown` + stress gate (thủ công) |
| `cleanup` / `restore-backup` / `hooks-install` | Dọn dẹp / khôi phục alert / cài pre-commit hook |

## 6. Backtest, kiểm định, mô phỏng

| Lệnh | Chức năng |
|---|---|
| `walk-forward` | IS 2021–2024 → khóa tham số → blind replay OOS 2025–2026 |
| `grid-search` | Tối ưu trọng số multi-factor + DecisionGuard |
| `capitulation` | Paper ledger forward H+5/H+20/H+30 theo vùng P_cap |
| `paper` / `sandbox` / `phase5` / `quantstats` | Paper trading / dashboard / UAT / live calibration |
| `phase4` | CAS-DSM: Capitulation Detector + Scale-In + Abortion |

## 7. Báo cáo & pipeline định kỳ

| Lệnh | Chức năng |
|---|---|
| `market` / `report {weekly,monthly,daily}` | Snapshot thị trường / báo cáo (`--month`, `--lang`) |
| `morning` | SBV → Sensors → MacroState → Governor → System Audit |
| `close` | EOD → Breadth → Sector → Governor → System Audit |
| `earnings` | Crawl → Health v2 → Valuation → Governor → System Audit |
| `daily-close` | Daily closer pipeline |
| `serve` | Khởi động server |

---

## ⛔ LỆNH CẤM / LỆNH HỎNG

- **`fetch-financials` — BROKEN** (help ghi rõ: VCI/KBS API không hoạt động).
  Dùng `import-financials` (CSV/XLSX) hoặc `bctc-20q` thay thế.
- **`cafef-crawl --source cafef`** — requests+Playwright, thường 404.
  Ưu tiên `--source vci` hoặc `--source api`.
- **`--force-stale`** — chỉ debug/backtest, mỗi lần dùng ghi ledger. Không dùng
  để "chạy cho xong" trong vận hành thật.

## 🔬 NGOẠI LỆ CÓ KIỂM SOÁT: research runners

`backend/src/research/run_*.py` **không phải** CLI thay thế — là runner
nghiên cứu (calibration/audit), chỉ được tạo khi:

1. Nghiệp vụ **chưa có** lệnh trong `ptck.py` (đối chiếu mục 1–7 ở trên).
2. Có docstring 3 tầng `[WHY / ROOT CAUSE / INVARIANT]`.
3. Được đăng ký vào CLI_INDEX này (thêm dòng vào mục "Research runners").
4. Đầu ra ghi rõ `NON-DECISIONAL` nếu đụng OOS 2025–2026.

Hiện có:
- `run_foc_empirical_audit.py`, `run_vea_empirical_audit.py`, `run_smb_empirical_audit.py` (Pha 1/2/3 audits, NON-DECISIONAL)
- `run_pat_empirical_audit.py`, `run_dsn_empirical_audit.py` (Pha 4/5 audits, NON-DECISIONAL —
  PAT UPCoM lưới 100, DSN HOSE lưới 50). Hai runner này dùng pipeline chung
  `symbol_audit_pipeline.py` (`load_certified_series` + `print_audit_report`) thay vì tự
  nạp/phục nguyên, vì chúng không có runner cũ cần bảo toàn số đã nghiệm thu.
  — **bắt buộc**: cổ tức lấy từ `audit_provenance.build_vsdc_dividends(symbol, AUDIT_WINDOW_START)`
  (SSOT = `cash_event_registry` + metadata pay_date/action_id), phục nguyên band mode OHLC,
  và fail-closed nếu `is_certified = False`. Cấm khai báo lại event inline trong runner
  hoặc trong CLI `dividend-audit` (test khoá literal `CashDividendEvent(`).
- `run_raw_price_reconstruction.py` (Pha 2B calibration — FOC/VEA/SMB, band mode OHLC)
- `price_provenance_gate.py --reconstructed --symbols <SYM>` — **cổng chứng nhận
  chuỗi giá phục nguyên** (100% bars bội lưới tick + gap qua biên phiên GDKHQ +
  **tầng 3 kiểm chéo vendor** KBS/VCI).
  Exit 0 = được phép đưa chuỗi vào audit. Chạy từ `backend/`:
  `python -m src.research.price_provenance_gate --symbols FOC,VEA,SMB,PAT,DSN --reconstructed`
  Event không đạt band OHLC (phiên gap-up/gap-down) **chỉ** được nghiệm thu qua
  `LIMIT_BAND_EVENTS` (registry, khai báo từng ngày) và in nhãn `limit-band` +
  dòng "LƯU Ý BẰNG CHỨNG" — tầng này yếu hơn, cấm nới theo symbol.
  **Tầng 3** (`cross_vendor_agreement.py`) đo bước nhảy tỷ số close KBS/DB VCI qua
  ex_date: `conflict` ⇒ chặn tuyệt đối; `insufficient-data` chỉ chặn event đang dựa
  vào `limit-band`; `vendor-lag` là ghi chú (DB chưa pull lại kỳ mới nhất). Cần DB
  tồn tại ở chế độ `--reconstructed`; đọc DB bằng `VALID_ROW_SQL`, KHÔNG `is_stale`.
  Còn treo sau E19: PAT 2026-09-15, FOC 2026-08-10 — DSN đã về 10/10 `agree`.
  **`vendor-lag` KHÔNG đồng nghĩa "vendor chậm"**: bước nhảy ~4,3% của DSN
  2026-05-18 hoá ra là **chuyển thang giá trong DB** (bar thô xen bar adjusted,
  `1/0,958395 − 1`), vá bằng `backfill-history` là hết. Trước khi kết luận vendor
  chậm, phải kiểm phân bố `source` trong cửa sổ (E19).
- `symbol_audit_pipeline.py` — **thư viện dùng chung**, không phải CLI: 3 tầng
  fail-closed (band mode → certify → cross-vendor) + in báo cáo; runner Pha 4/5 và
  CLI `dividend-audit` (nhánh PAT/DSN) cùng dùng, nên không có đường nạp thứ hai.
- `backend/scripts/run_ablation.py`, `run_oos_2026.py` (ablation/OOS)
- `backend/src/backtest/run_batch_validation.py`, `run_hsr.py` (backtest harness)

**Cấm:** để scratch probe (`probe*.py`, `vea_probe*.py`) trong repo — scratch
chỉ tồn tại ở `%TEMP%\opencode`, và nếu kết quả có giá trị thì **hợp nhất vào
module + test chính thức**, không commit scratch.
