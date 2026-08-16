# Gate E1 — WGC ETF flows: Incremental-Power Audit (verdict note)

## Hai lane — KHÔNG trộn evidence

### Lane A — STRICT PIT (official verdict): **NOT TESTABLE**
- Panel walk-forward v0.1: `2021-08-09 → 2026-02-23` (1142 ngày).
- ETF vintage rows: 2256. Rows có `publication_date <= panel_end`: **0**.
- Lý do: mọi obs ETF có `publication_date = 2026-08-16` (fetch date, operational
  vintage). Không obs nào usable trong bất kỳ backtest lịch sử nào.
- **Không impute, không backfill pub date, không đưa vào Gold v0.2.**
- Đây là kết quả nghiên cứu hợp lệ (không phải failure): historical predictive
  power của ETF **chưa kiểm định được** cho tới khi có vintage ladder thật.

### Lane B — Latent-signal diagnostic (ASSUMPTION-BASED / NON-PIT / RESEARCH ONLY)
Giả định pub = obs + L ngày, L ∈ {3,7,14,30}. Không calibration, không production.
Trả lời duy nhất: "nếu availability gần đúng giả định, ETF có latent signal không?".

## Kết quả Lane B

### AUC standalone (walk-forward logistic, OOS, GLOBAL tonnes/usd mean)
| Lag | H20 | H60 | H120 |
|---|---|---|---|
| M+3 | 0.505 | 0.615 | 0.733 |
| M+7 | 0.507 | 0.611 | 0.698 |
| M+14 | 0.525 | 0.638 | 0.658 |
| M+30 | 0.527 | 0.679 | 0.507 |

- **H20 (primary): AUC ≈ 0.50–0.53 → KHÔNG có signal.**
- H60/H120 cao hơn nhưng **không ổn định theo lag**: H120 sụt 0.733→0.507 khi lag
  tăng → đây là sensitivity artifact, KHÔNG phải signal ổn định.

### Các diagnostic khác (GLOBAL, mean theo lag)
| Metric | H20 | H60 | H120 |
|---|---|---|---|
| Spearman vs R_H | 0.081 | 0.026 | −0.031 |
| Sign agreement | 0.494 | 0.408 | 0.392 |
| Decile spread | +0.004 | −0.010 | −0.042 |

- Spearman gần 0, **âm ở H120**.
- Sign agreement < 0.5 ở H60/H120 → dấu hiệu ngược (nghịch biến).
- Decile spread H120 ÂM → top-decile flow đi kèm return thấp hơn.

### Redundancy — phát hiện quan trọng
- `spearman(ETF_FLOWS_USD, ETF_DEMAND_TONNES)` = **0.967–0.992** theo region.
  → **Hai series gần như cùng 1 latent variable** (khớp cảnh báo `ETF_usd ≈
  ETF_tonnes × GoldPrice`). KHÔNG được tính là 2 evidence độc lập.
- Cross-region tonnes mean = 0.28–0.30 → regional có bớt redundancy nhưng yếu.
- ETF GLOBAL tonnes vs DXY_z = −0.41…−0.45 → **trùng một phần với monetary**
  (không phải information độc lập hoàn toàn với M1).

## Verdict Lane B
```
ETF latent signal?
   ├── H20: AUC≈0.5 (no signal)
   ├── H60/H120: AUC>0.5 nhưng unstable theo lag + spearman≤0 +
   │     sign agreement <0.5 + decile spread âm
   └── ETF_usd ≡ ETF_tonnes (redundancy 0.97–0.99)
        → NO STABLE LATENT SIGNAL
```
**Không đủ cơ sở để đầu tư mạnh vào vintage acquisition dựa trên evidence hiện tại.**
Đây KHÔNG là phán quyết cuối — là diagnostic theo lag giả định trên snapshot final-
revised (nhiễm revision), không có vintage thật.

## Boundary đóng tại E1
| Lane | Verdict | Production? |
|---|---|---|
| Strict PIT | **NOT TESTABLE** | ❌ |
| Lag sensitivity | **NO STABLE SIGNAL** | ❌ |
| Forward vintage ladder | **OPEN** | ⏳ |

**Diễn giải chính xác (không thổi phồng):** Lane B chỉ cho phép kết luận yếu —
"với các lag giả định đã thử, không tìm thấy tín hiệu ETF ổn định; đồng thời strict
PIT chưa đủ vintage để kiểm định lịch sử". KHÔNG được diễn giải thành "ETF không có
alpha". ETF không bị loại vĩnh viễn; chỉ là chưa đủ evidence để vào Gold v0.2.

## Quyết định
1. ETF giữ ngoài Gold v0.2 cho tới khi có bằng chứng strict PIT.
2. Forward crawl vẫn chạy (chi phí thấp, vài phút/tháng) → nếu sau ~6-12 tháng
   ladder đủ dài, chạy lại E1 strict PIT chính thức.
3. Không tìm thêm nguồn trả phí cho ETF ngay (latent signal yếu → không đáng tiền).
4. Gold v0.2 chỉ tiếp tục với các series có strict PIT: WGC_CB / IFS_GOLD_RESERVE
   (cùng đường M1 monetary, không có ETF).

## Data/Artifacts
- Module: `gold_etf_feature_audit.py` (Lane A + Lane B).
- CSV: `data/reports/gold_etf_e1_latent_signal.csv` (gitignored qua `data/`).
- Snapshot cache: `data/cache/wgc_etf/snapshot_2026-08-07.json` (gitignored).