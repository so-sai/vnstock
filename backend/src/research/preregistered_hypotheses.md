# SỔ GIẢ THUYẾT TIỀN ĐĂNG KÝ (Pre-registered Hypotheses)

Quy tắc chung của sổ này:
- Mỗi giả thuyết phải ghi **phát biểu định lượng**, **cách đo**, **cửa sổ kiểm chứng**, và
  **tiêu chí bác bỏ** TRƯỚC khi chạy kiểm chứng xuôi.
- **CẤM** hồi quy ngược / tinh chỉnh tham số dựa trên kết quả đã quan sát.
- **CẤM** dùng cửa sổ OOS 2025-2026 (đã nhiễm, xem `oos_2026_non_decisional_note.md`) làm nơi
  hợp thức hoá. Chỉ nhận bằng chứng **kiểm chứng xuôi (forward paper-trading)**.
- Giả thuyết ở trạng thái `PROPOSED` không được cấp quyền quyết định (không cổng, không entry).

---

## H-2026-10-15-A — Market-Wide Capitulation Washout

**Trạng thái:** PROPOSED (chưa kiểm chứng xuôi) — **KHÔNG dùng để ra quyết định**.

**Nguồn phát sinh (quan sát, không phải bằng chứng):** trong replay Frozen Spec
(`capitulation_replay.py`), nhóm ngày T0 có **số mã thoả ĐK1 > 10** cho win-rate T+10 = 80%
và lợi suất trung bình **+11,2%**, nhưng chỉ với **n = 5 ngày** thuộc IS 2021-2024.
Spearman(ĐK1 breadth, lợi suất T+10) toàn IS = **+0,314** (n=21 ngày).

**Phát biểu tiền đăng ký:**
> Khi độ rộng hoảng loạn toàn thị trường vượt ngưỡng — **số mã thoả ĐK1 trong một phiên > 10** —
> thì các sự kiện thoả trọn 3 điều kiện của Frozen Spec trong **cùng phiên T0** có
> kỳ vọng lợi suất T+10 **dương rõ rệt** (win-rate ≥ 80%, mean ≥ +11,2%) so với nhóm breadth thấp.

**Cách đo (giữ nguyên, không đổi tham số):**
- ĐK1/ĐK2/ĐK3 y hệt Frozen Spec 2026-10-14; breadth = số mã thoả ĐK1 trong phiên T0.
- Đo lợi suất ròng tại T+10 (entry `close_{T+2}`, chi phí 0,3%).
- So sánh nhóm `breadth > 10` với nhóm `breadth ≤ 10`; sai số chuẩn theo **cụm ngày T0**.

**Cửa sổ kiểm chứng:** CHỈ tiến về phía trước, kể từ 2026-10-16. Mọi phiên có ĐK1 breadth > 10
phải được ghi vào nhật ký giấy (`research/capitulation_paper_ledger.py`) kèm mã + ngày, để
đo T+10 khi đủ dữ liệu.

**Tiêu chí bác bỏ:** sau ≥ 20 ngày T0 độc lập có breadth > 10, nếu win-rate T+10 < 60%
**hoặc** mean ≤ 0 ⇒ BÁC BỎ, gỡ khỏi sổ.

**Ghi chú kỷ luật:** n=5 ngày là mẫu quá nhỏ; đây là **máy sinh giả thuyết**, không phải bằng chứng.
Việc cổng hoá theo breadth > 10 (nếu sau này có) phải là một quyết định RIÊNG, sau khi giả thuyết
này vượt tiêu chí trên — và phải được đăng ký lại như một thay đổi cổng mới.

---

## H-2026-10-22-REGIME-UNFREEZE — Điều kiện mở khoá sau nhịp nảy 21/10

**Trạng thái:** `PROPOSED` → **`CLOSED` (2026-10-23)** — xem mục **ĐÓNG SỔ** ở cuối file.
**Đóng băng:** 2026-10-21 sau ATC, trước giờ mở cửa 22/10.
Theo quy tắc dòng 9 của sổ này, giả thuyết `PROPOSED` **không tự cấp quyền quyết định**: kể cả khi
đạt 4/4, việc hạ `ENTRY_LOCK` vẫn phải do chủ hệ thống GO tường minh, không có tự động hoá nào được
đọc file này để mở khoá.

**Mục tiêu:** kiểm chứng tính bền của nhịp nảy phiên 21/10 (Breadth 46,7% → **65,5%** nhưng điểm
Regime lại **giảm** 0,31 → 0,28) — phân định "dòng tiền lớn nhập cuộc" với "nảy kỹ thuật thiếu cầu".

**Trạng thái xuất phát (đo từ `regime_history` ngày 2026-10-21, đã `DAT`):**

| Trường | Giá trị |
|---|---|
| `regime_score` | **0,28** (`status = CRISIS`) |
| `breadth_pct` (% mã trên MA20) | **65,5** (vận tốc +30,7) |
| `vol_score` | **0,20** (đang ở SÀN `max(0.2, …)`) |
| `trend_score` | **0,00** |
| Ngữ cảnh | VN-Index 1.691,97 < MA200 1.796,38 (−5,8%); ngoại 21/10 **+131,4 tỷ** (phiên mua ròng đầu tiên) |

### 4 điều kiện tiên quyết — đo bằng SQL trên `regime_history`

```sql
SELECT date, regime_score, breadth_pct, vol_score, trend_score
FROM regime_history WHERE date = :target;   -- :target = '2026-10-22', '2026-10-23'
```

1. `regime_score >= 0.40` — vượt hẳn vùng đệm 0,35–0,39 (RANGING kỹ thuật ≠ ngưỡng hành động).
2. `breadth_pct >= 60.0` — giữ được sắc xanh lan toả, không bị dội ngược T+2.
3. `vol_score > 0.20` — thoát sàn thanh khoản (sàn là hằng số trong `regime_engine.py:256`).
4. `trend_score > 0.00` — VN-Index bắt đầu có điểm cấu trúc xu hướng.

### Cây quyết định (đóng băng trước)

- **A — ĐẠT 4/4** (trong cửa sổ): đề xuất **hạ `ENTRY_LOCK`** lên chủ hệ thống để GO; nếu GO, giải ngân
  thăm dò **10–15% NAV** ở các mã đã qua T1–T4 và có breakout `Close > High20` + `Vol ≥ 1.3×ADV20`.
  **Không** được hạ ngưỡng nào trong 4 điều kiện trên để "cho vừa kết quả".
- **B — THIẾU dù chỉ 1 điều kiện** (hết cửa sổ): **GIỮ `ENTRY_LOCK = TRUE`**, 100% tiền mặt; ghi nhận
  nhịp nảy 21/10 là **hồi kỹ thuật chưa được xác nhận**. **Không** được nới ngưỡng 0,40 xuống 0,35 để
  hợp thức hoá một lệnh đã muốn mua.

### Cửa sổ kiểm chứng
**CHỈ tiến về phía trước:** các phiên **2026-10-22 và 2026-10-23** (EOD). Hết 23/10 là **hết hạn đo**;
muốn tiếp tục theo dõi phải **đăng ký giả thuyết mới** (không gia hạn ngầm).

### Tiêu chí bác bỏ / kết luận
- Hết 22/10 mà chưa đạt ⇒ chưa kết luận (còn 1 phiên).
- Hết **23/10** mà `regime_score < 0.40` **hoặc** `vol_score = 0.20` ⇒ **BÁC BỎ** giả thuyết "nhịp nảy
  có xác nhận dòng tiền"; ghi vào nhật ký là **B** và đóng H.
- Ghi chú mẫu: n = 2 phiên ⇒ **không** kết luận thống kê; đây là quan sát có đăng ký, không phải
  backtest và không được dùng để tinh chỉnh tham số Regime.

**Nguồn số liệu (provenance):** `backend/data/screener_cache.db` → `regime_history`
(ngày 19–21/10/2026, seed EMA đã nối liền tới 20/10 sau khi vá `is_degraded`).

**BẢN GHI HIỆU CHỈNH DỮ LIỆU KHỞI ĐIỂM (không đổi 4 điều kiện):**
- Lúc đóng băng (15:43 ngày 21/10), dòng `regime_history` của 21/10 đọc được `score 0,28 · breadth 65,5`.
- Đo lại lúc 16:0x: dòng đó đã được **tính lại** thành `score 0,27 · breadth 65,6` (`vol_score 0,20`,
  `trend_score 0,00`, `is_degraded 0`), và tổng ngoại ngày 21/10 đã **hoàn tất** từ **+131,4 → +170,1 tỷ**
  (snapshot 14:46 được ghi bổ sung bởi lượt sync sau ATC).
- Vì vậy **baseline chính thức để so sánh là bản đo lại**: `0,27 / 65,6 / V 0,20 / T 0,00`.
  4 điều kiện mở khoá và tiêu chí bác bỏ **giữ nguyên**, không nới và không siết.

**KẾT QUẢ ĐO PHIÊN 22/10** (EOD 867 mã; `regime_history` 2026-10-22):

| # | Điều kiện | Ngưỡng | Đo được | |
|---|---|---|---|---|
| 1 | `regime_score` | ≥ 0,40 | **0,35** | ❌ THIẾU |
| 2 | `breadth_pct` | ≥ 60,0 | **63,5** | ✅ ĐẠT |
| 3 | `vol_score` | > 0,20 | **0,9228** | ✅ ĐẠT |
| 4 | `trend_score` | > 0,00 | **0,4000** | ✅ ĐẠT |

⇒ **3/4 ⇒ KỊCH BẢN B: GIỮ `ENTRY_LOCK = TRUE`, 100% tiền mặt.** Cửa sổ còn **1 phiên (23/10)**;
nếu hết 23/10 vẫn `score < 0,40` ⇒ BÁC BỎ và đóng H theo tiêu chí đã đăng ký.

**Ghi chú nhất quán dữ liệu (không đổi phán quyết):** dòng 22/10 lưu `status = 'CRISIS'` trong khi
`regime_score = 0,35` — mà ngưỡng code là `RANGING if score >= 0,35`. Nguyên nhân: `regime_engine.py:489`
trả `round(regime_score, 2)` (0,35) còn `status` (:371) tính trên giá trị **chưa làm tròn** (≈0,349x).
Lệch chỉ ở biên, hướng fail-closed (CRISIS chặt hơn), nhưng cần sửa để `status` và `score` không mâu thuẫn.

---

## ĐÓNG SỔ H-2026-10-22-REGIME-UNFREEZE — 2026-10-23

**Trạng thái:** `CLOSED`. Cửa sổ đo đóng đúng hạn (hết phiên 23/10, **không gia hạn ngầm**).

### Kết quả đo phiên cuối (EOD 2026-10-23; `daily_ohlcv` 934 mã; `regime_history` `is_degraded = 0`)

| # | Điều kiện | Ngưỡng | Đo được | |
|---|---|---|---|---|
| 1 | `regime_score` | ≥ 0,40 | **0,4214** | ✅ ĐẠT |
| 2 | `breadth_pct` | ≥ 60,0 | **60,9** | ✅ ĐẠT |
| 3 | `vol_score` | > 0,20 | **0,9445** | ✅ ĐẠT |
| 4 | `trend_score` | > 0,00 | **0,7000** | ✅ ĐẠT |

⇒ **4/4 = KỊCH BẢN A.** Giả thuyết **không bị bác bỏ** (tiêu chí bác bỏ là `score < 0,40` hoặc
`vol_score = 0,20`; cả hai không xảy ra).

### Phán quyết điều hành: **VETO mở khoá** (không phải kịch bản B)

Đây là **A đạt nhưng Chủ hệ thống từ chối GO** — ghi thành mục riêng để sổ không bị đọc sai thành
"giả thuyết bị bác bỏ". Trạng thái vận hành: **`ENTRY_LOCK = TRUE`, 100% tiền mặt** qua cuối tuần
24–25/10 (`system_state.json`, lý do `DAILY_GOVERNOR_CRISIS_OVERLAY`).

Lý do định lượng, đo **cùng phiên 23/10**:

1. **Độ rộng ròng ÂM −4,073%**: 327 tăng / 365 giảm / 241 đứng, **58 mã giảm sàn** trên 933 mã
   (`market_wide_breadth_daily`, `degraded = 0`). Chỉ số tăng nhờ trụ, không phải lan toả.
2. **Chỉ 1 mã qua T1–T3 trong 28 mã `RS ≥ 90`**: `ORS` (RS 92,4; close 16.000 > đỉnh 20 phiên
   15.600; volume 11,30M = **3,91×** ADV20 2,89M). `PVT` trượt cả hai vế (close 23.700 < 26.350;
   volume 0,68× ADV20) — đúng như đã nêu, không nới ngưỡng.
3. **T4 veto — không mã nào đủ T1–T4**:
   - `ORS`: **`DISTRESSED`** (conf 0,4852; Eff 0,123 < 0,60; Moat 0,217 < 0,50).
   - `PET`: **`DISTRESSED`** (conf 0,5011; BS 0,142 < 0,30; Moat 0,426 < 0,50) — và PET vốn đã trượt
     T3 (close 56.700 ≤ đỉnh 20 phiên 57.400), nên chỉ ghi nhận T4, không phải ứng viên.
   - Luật đã niêm phong V5: `Health ≠ DISTRESSED`, `archetype is None` mới đi qua; `DISTRESSED` là
     **veto cứng**, không có ngoại lệ "chỉ 10–15% NAV".
4. **Cảnh báo provenance riêng của `ORS`**: `latest_period = 2025Q2` (BCTC cũ 5 quý). Verdict
   `DISTRESSED` dựa trên dữ liệu cũ ⇒ càng phải fail-closed; muốn đánh giá lại phải nạp BCTC mới,
   **không** được hạ cổng để hợp thức hoá.

### Amendment provenance Δ = 0,0030 (đã ghi sổ cái)

| Đường chạy | `regime_score` 23/10 | `B-Score` | `Raw` |
|---|---:|---:|---:|
| `regime_history` (live/stored) | **0,4214** | **60,91 %** | 0,7034 |
| `detect_regime("2026-10-23")` (replay) | **0,4244** | **63,80 %** | 0,7179 |

- **Nguyên nhân thật (đã điều tra, KHÔNG phải seed làm tròn):** hai đường dùng **cùng seed**
  22/10 (0,3459). Khác biệt nằm ở **B-Score**, do cửa sổ nạp dữ liệu khác nhau:
  - Live nạp từ mốc cố định `2025-10-01` ⇒ chỉ **5 mã** không đủ 20 phiên ⇒ universe thanh khoản
    **353 mã**, breadth **60,91 %**.
  - Replay nạp cửa sổ **60 ngày lịch** ⇒ **150 mã** không đủ 20 phiên (ACM chỉ 6 phiên, BEL 3…)
    ⇒ universe thanh khoản teo còn **326 mã**, breadth **63,80 %**.
  - Vì `avg_vol_20d` là `rolling(20)` trên chính cửa sổ đã nạp, cửa sổ ngắn **loại oan** các mã
    giao dịch thưa ⇒ mẫu số breadth đổi ⇒ B-Score lệch 2,9 điểm phần trăm.
- **Cả hai đều ≥ 0,40** ⇒ phán quyết 4/4 và quyết định VETO **bất biến** với sai số này.
- Nhưng đây là **vi phạm Bất biến #1** (cùng ngày, cùng score bất kể đường gọi): test
  `test_live_khop_hoan_toan_voi_replay` **ĐỎ** (Δ 0,0030 > ngưỡng 2e-3). Chưa vá engine trong
  bản ghi này; phải sửa cửa sổ nạp của nhánh replay (hoặc nguồn seed) trong một thay đổi riêng.
- Bản ghi: `eod_write_audit_ledger` **audit_id = 1703** (`status = AMENDMENT`) và bản **đính chính**
  `audit_id = 1704` (ghi lại nguyên nhân đúng; sổ cái append-only nên không sửa dòng cũ).

### Bất biến giữ nguyên

- Mọi ngưỡng của 4 điều kiện: **không nới, không siết**.
- Việc mở van thăm dò (nếu có) chỉ được xem xét **phiên 26/10**, tối đa **10–15% NAV**, và phải là
  một giả thuyết **đăng ký mới** — không dùng kết quả 2025–2026 để chọn tham số.

---

## H-2026-10-26-DUAL-COMPARTMENT — Hai khoang tàu độc lập

**Trạng thái:** `PROPOSED` — **đóng băng 2026-10-23, trước giờ mở cửa 26/10**. Không cấp quyền quyết
định, không tự mở `ENTRY_LOCK`, không ghi đè bất kỳ cổng nào đã niêm phong. Chỉ kiểm chứng **xuôi**
bằng sổ giấy; mọi kết quả 2025–2026 là NON-DECISIONAL.

**Vấn đề cần giải:** hiện chỉ có một bộ luật (Trend) và nó bị dùng sai chỗ — áp `RS ≥ 90` +
`Close > High20` lên cổ phiếu giá trị ở đáy sẽ **loại oan toàn bộ** nhóm tài sản rẻ (SHB, TCB, HPG).
Giả thuyết này tách thành **hai cỗ máy độc lập**, mỗi khoang có định nghĩa, cổng và hạn mức riêng.

### §1. Phân định cứng giữa hai khoang

| | **Khoang 1 — Asset-Play / Deep Value** | **Khoang 2 — Trend / Momentum** |
|---|---|---|
| Kích hoạt | `regime ∈ {CRISIS, RANGING}` | `regime_score ≥ 0,40` và regime sạch EOD |
| Định giá | `P/B ≤ 0,80` | không dùng |
| Sinh lời | `ROE ≥ 15,0 %` | không dùng |
| Rủi ro tín dụng | `NPL ≤ 2,2 %` — **chỉ BANK** | không dùng |
| Sức mạnh giá | **MIỄN TRỪ HOÀN TOÀN `RS`** | `RS ≥ 90` (giữ nguyên `trend_engine.RS_MIN`) |
| Cấu trúc | **MIỄN TRỪ `Close > High20`** | `Close > max(High[1:20])` **và** `Close > MA20` |
| Thanh khoản | `ADV20 ≥ 500.000` cp | `ADV20 ≥ 500.000` cp **và** `Volume ≥ 1,3 × ADV20` |
| Sức khoẻ | `Health ≠ DISTRESSED` | `Health ≠ DISTRESSED` |
| Loại trừ | giá < 10.000 đ; thang giá mơ hồ | giá < 10.000 đ; thang giá mơ hồ (`load_scale_ambiguous`) |
| Trần NAV | **tuyệt đối 30 %**; khởi điểm **20 %** | **tuyệt đối 70 %**; khởi điểm **60 %** |
| Thang giải ngân | chia phần, tối đa 3 phần | `0 / 30 / 70 / 100` (`unfreeze_gates_v2.TRANCHES`) |

**Quy tắc bất khả xâm phạm:** cấm dùng luật Khoang 2 để loại ứng viên Khoang 1 và ngược lại. Cấm
dùng "rẻ" của Khoang 1 để biện minh cho mã gãy nền không có cầu; cấm dùng "khoẻ" của Khoang 2 để
nâng hạn mức Khoang 1.

### §2. Cách đo (PIT, một nguồn cho mỗi trường)

- **P/B:** `financial_facts.db.valuation_scores`, `ratio_name = 'PB'`, kỳ `≤ get_pit_quarter(T)`,
  `is_clean_metric = 1`.
- **ROE:** `financial_facts.db.valuation_scores`, `ratio_name = 'ROE'`, `eps_flow_kind = 'TTM'`,
  `is_clean_metric = 1`, kỳ `≤ get_pit_quarter(T)`. `af = 1` khi flow_kind TTM ⇒ **cấm annualize
  lần hai**; `eps_flow_kind ∈ {QUARTER, YTD, UNKNOWN}` ⇒ `ROE_PERIODICITY_MISMATCH` ⇒ loại.
  [AMENDMENT 2026-10-23] Bản đầu ghi `health_ratios.ROE`; đo lại cho thấy hai nguồn cho cùng giá trị
  ở 2026Q2 nhưng chỉ `valuation_scores` có cột `eps_flow_kind` để chứng minh TTM. Không có bằng
  chứng chu kỳ ⇒ không dùng.
- **NPL:** `financial_facts.db.health_ratios`, `ratio_name ∈ {'NPL_RATIO','BANK_NPL_RATIO'}`, kỳ
  `≤ get_pit_quarter(T)`. Đơn vị là **phân số thập phân** (0,022 = 2,2 %).
- **Giá/thanh khoản:** `daily_ohlcv` chế độ live/PIT: `is_stale = 0 AND VALID_ROW_SQL`.
- **Health:** `CompanyHealthV2.analyze(symbol, T)`; `None` ⇒ loại (missing > fake).
- **Thiếu trường nào ⇒ `*_MISSING` và KHÔNG vào lệnh.** Cấm `fillna(0)`, cấm nội suy.

### §3. Cổng dữ liệu CHẶN Khoang 1 (khai báo trước, fail-closed)

Đo ngày 2026-10-23 trên kho thật: **toàn bộ 56.055 dòng `valuation_scores` có `price_basis =
'ADJUSTED_TODAY'`** — giá đã điều chỉnh theo hệ số của hôm nay, trong khi `shares_basis =
'CHARTER_PIT'`. Trộn hai basis này là **nhìn trước** (hệ số chia/thưởng tương lai), đúng loại lỗi
Bất biến #1 cấm.

⇒ **Khoang 1 bị khoá ở 0 % NAV cho tới khi có P/B theo basis PIT** (việc riêng, xem
`tech_debt_backlog.md` Mục 13). Giả thuyết vẫn được đăng ký để **đo tiềm năng** bằng bộ dữ liệu
PIT-safe sau này, nhưng **không được phát lệnh nào** trên basis hiện tại.

**Đo thử 2026Q2 (chưa phát lệnh, chỉ để biết tiềm năng):** 24 mã `entity_type='BANK'` có P/B sạch;
**0 mã** đạt đồng thời `P/B ≤ 0,80` và `ROE(TTM) ≥ 15 %`. Các mã hay được nhắc đều trượt:
`SHB` P/B 0,961 / ROE 4,78 %; `TCB` 1,256 / 3,89 %; `HPG` 1,399 / 4,50 %; `MBB` P/B có
`is_clean_metric = 0` ⇒ `PB_BASIS_MISMATCH`, loại. Hệ quả: **kể cả khi vá xong Mục 13, Khoang 1 có
thể vẫn rỗng** — không được hứa "thứ Hai mở van" trước khi đo lại.

### §4. Kiểm chứng xuôi và tiêu chí bác bỏ

- **Cửa sổ:** chỉ tiến về phía trước, từ **2026-10-26**. Không hồi quy, không dùng 2025–2026 để chọn
  tham số.
- **Cách đo mỗi khoang:** tín hiệu chốt tại close T, vào tại **open T+1**, đo lợi suất ròng T+20 với
  chi phí **0,50 %** (spec) và **0,60 %** (robustness). Ghi mã, ngày, khoang vào sổ giấy.
- **Cỡ mẫu:** tối thiểu **20 tín hiệu độc lập mỗi khoang**. Dưới 20 ⇒ chưa kết luận.
- **Bác bỏ từng khoang độc lập** nếu: win-rate T+20 **< 50 %** hoặc mean lợi suất ròng **≤ 0**.
- **Không được** đổi ngưỡng P/B, ROE, NPL, RS, breakout, volume, trần NAV sau khi đã thấy kết quả.
  Muốn đổi ⇒ đăng ký giả thuyết mới, cửa sổ mới.
- **Không có ngoại lệ "chỉ 10–15 % NAV"**: trần NAV trong bảng §1 là hạn mức của **sổ giấy**; tiền
  thật vẫn phụ thuộc `ENTRY_LOCK` và một GO riêng của chủ hệ thống.

### §5. Ý nghĩa của việc bác bỏ

Nếu Khoang 2 bị bác bỏ ⇒ xác nhận lại kết quả OOS 2026 (breakout yếu trong thị trường phân hóa).
Nếu Khoang 1 được bác bỏ ⇒ "rẻ + ROE cao" một mình không đủ tạo alpha; phải bổ sung biến dòng tiền
(margin, tự doanh, ngoại) trước khi đăng ký lại. Cả hai đều là **kết quả hợp lệ**, không phải thất
bại cần "sửa luật cho vừa".


