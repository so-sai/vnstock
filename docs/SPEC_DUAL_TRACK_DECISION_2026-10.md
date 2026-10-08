# SPEC — Dual-Track Decision Engine (Track 1 Core + Track 2 Commodity/Energy)

Ngày: 2026-10-08 · Trạng thái: **DRAFT — chờ chốt scope** · Người đề xuất: chủ hệ thống
Nguồn cảm hứng: 08/10 ngoại mua ròng **PLX +91,6 tỷ** và **PVT +61,7 tỷ** trong khi `ENTRY_LOCK=TRUE`
khoá toàn bộ Track 1 ở 0% allocation.

---

## 0. KẾT QUẢ TIỀN KIỂM (facts — không phải giả định)

Bốn lệnh verify trước khi viết spec đã bác bỏ 2 giả định trong đề xuất ban đầu:

| # | Câu hỏi | Kết quả | Hệ quả |
|---|---|---|---|
| **F1** | `COMMODITY_UNIVERSE` đã có chưa? | **KHÔNG tồn tại.** Không có hằng số nào tên `COMMODITY_UNIVERSE`/`ENERGY_UNIVERSE` trong `backend/src/` | Phải tự định nghĩa — và định nghĩa đó là **quyết định dữ liệu**, không phải code |
| **F2** | Archetype của rổ năng lượng? | `BASELINE_MAP` (35 mã human-validated) **chỉ có BSR→CYCLICAL_HEAVY và GAS→REGULATED_UTILITY**. **PLX, PVT, PVD, PVS, MSR KHÔNG có archetype** | Không được tự gán. Theo `AGENTS.md` ("params từ ICB"), phải qua ICB validation trước |
| **F3** | `confidence ≥ 0.80` tính động được không? | **KHÔNG.** `confidence` là **hằng số tác giả** (0.60/0.70/0.80/0.90) trong `causal_edge.py:63,168,182,196,211`. `grep confidence causal_dag_engine.py` → **rỗng**, không engine nào compute | ⚠️ **Cổng "confidence ≥0.80" là vòng tròn** — nó luôn đúng với các cạnh tác giả gán cao, bất kể thị trường. Phải thay bằng thước đo đo được |
| **F4** | Backtest được không? | **ĐƯỢC.** PLX/PVT/PVD/PVS/MSR: **1.391–1.435 phiên sạch** từ 2020-12/2021-01. BSR 1.391, GAS 1.390, HPG 2.810 (từ 2015) | Backtest khả thi, nhưng cửa sổ chỉ phủ ~1 chu kỳ hàng hoá → **không đủ để tự tin về tỷ lệ Sharpe ngoài mẫu** |
| **F5** | `rs_ranker` fail ảnh hưởng gì? | `watchlist_state_history` max date = **2026-05-26**; `rsi_regime_history` max = **2026-06-01**. Không có `watchlist_state.json` | Subsystem watchlist/RS **đã chết ~4 tháng**. Brief hiện **không** phụ thuộc nó (tự tính từ `market_foreign_history`) ⇒ brief 08/10 vẫn đúng |
| **F6** | Trạng thái hiện tại | `ENTRY_LOCK = TRUE`, 100% tiền mặt, Regime CRISIS 0,28 (5 phiên), breadth 35,8% | Track 2 nếu triển khai hôm nay sẽ là **rổ duy nhất còn hoạt động** ⇒ blast radius phải cực nhỏ |
| **F7** | Bảng ICB có sẵn trong nhà? | **CÓ.** `symbol_industry(symbol, icb_name2/3/4)` — 1.964 mã | ✅ Tiên quyết A **đã thoả**, không cần chờ dữ liệu ngoài |
| **F8** | Chuỗi giá dầu lịch sử? | **CÓ.** `macro_history.variable = BRENT_OIL` (1.395 điểm) và `WTI_OIL` (1.384), **2021-04-05 → 2026-10-06** | ✅ Tiên quyết C **đã thoả**. ⚠️ Max date **lệch 2 phiên** so với EOD 08/10 ⇒ C2 luôn tính trên giá dầu hơi cũ; phải kiểm `is_stale` của chuỗi macro trong chính hàm C2 |

**Kết luận tiền kiểm:** đề xuất đúng về *cơ hội* (dòng tiền ngược chu kỳ có thật) nhưng **sai về 2 điểm kỹ thuật** — universe chưa tồn tại và cổng confidence không đo được. Cả hai đều **sửa được bằng dữ liệu đã có trong nhà** (F7, F8), nên Track 2 khả thi; nhưng phần **phân cực dấu** (mục 3.2) là điều kiện sống còn mà đề xuất ban đầu bỏ sót.

---

## 1. MOTIVATION

Track 1 (VN20 Gate) khoá cứng khi Regime = CRISIS. Điều đó bảo vệ đúng đắn, nhưng **không phân biệt được** hai tình huống khác hẳn nhau:

- (a) Toàn thị trường xấu, không có chỗ trú → đứng ngoài là tối ưu.
- (b) Toàn thị trường xấu **nhưng** có một nhóm hưởng lợi từ biến số vĩ mô *bên ngoài* thị trường VN (giá dầu, crack spread, giá kim loại) → đứng ngoài là **bỏ lỡ**, không phải phòng thủ.

Ngày 08/10 là bằng chứng cho (b): ngoại xả ròng toàn thị trường −368,8 tỷ, nhưng **PLX +91,6** và **PVT +61,7** được mua ròng mạnh nhất nhóm tích cực.

**Mục tiêu:** cho phép capture (b) **mà không** phá kỷ luật fail-closed.

---

## 2. KIẾN TRÚC

```
                    ┌──────────────────────────────────────┐
                    │  ENTRY_LOCK (portfolio_critical_lock)│  ← ABSOLUTE OVERRIDE
                    │  TRUE  ⇒  cả 2 track = 0%            │
                    └───────────────┬──────────────────────┘
                                    │ FALSE
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
   ┌──────────────────────┐                   ┌──────────────────────────┐
   │ TRACK 1 — CORE       │                   │ TRACK 2 — SATELLITE      │
   │ VN20 Gate (KHÔNG đổi)│                   │ COMMODITY_UNIVERSE       │
   │ Regime ⇔ allocation  │                   │ (điều kiện riêng, mục 4) │
   └──────────┬───────────┘                   └────────────┬─────────────┘
              └───────────────┬────────────────────────────┘
                              ▼
                  ┌────────────────────────┐
                  │ final-decision         │
                  │ alloc = T1 + T2 ≤ 100% │
                  └────────────────────────┘
```

**Bất biến kiến trúc:**
1. **Track 2 KHÔNG phải ngoại lệ của ENTRY_LOCK.** Khi `ENTRY_LOCK=TRUE` → Track 2 = 0%. Không có đường vòng.
   *(Chấp nhận hoàn toàn Safeguard 1. Lý do đúng như đã nêu: một ngoại lệ được duyệt hôm nay trở thành tiền lệ cho ngoại lệ thứ hai.)*
2. Track 2 là **phân bổ có điều kiện**, chỉ tồn tại khi Track 1 đã mở.
3. **Không có write-path mới vào DB.** Track 2 chỉ đọc + ra quyết định.
4. Mọi số trong output Track 2 phải trace được về DB (thừa hưởng invariant của Narrative Layer).

---

## 3. COMMODITY_UNIVERSE — định nghĩa bằng ICB (đã có trong nhà), và **PHẢI tách theo dấu**

### 3.1 Sự thật ICB (nguồn: `symbol_industry` — F7)

| mã | icb_name2 | icb_name3 | icb_name4 |
|---|---|---|---|
| BSR | Dầu khí | Sản xuất Dầu khí | Sản xuất và Khai thác dầu khí |
| PLX | Dầu khí | Sản xuất Dầu khí | Sản xuất và Khai thác dầu khí |
| PVD | Dầu khí | Thiết bị, Dịch vụ & Phân phối Dầu khí | Thiết bị và Dịch vụ Dầu khí |
| PVS | Dầu khí | Thiết bị, Dịch vụ & Phân phối Dầu khí | Thiết bị và Dịch vụ Dầu khí |
| GAS | Điện, nước & xăng dầu khí đốt | Nước & Khí đốt | Phân phối xăng dầu & khí đốt |
| **PVT** | **Hàng & Dịch vụ Công nghiệp** | **Vận tải** | **Vận tải Thủy** |
| **MSR** | **Tài nguyên Cơ bản** | **Khai khoáng** | **Khai khoáng** |
| HPG | Tài nguyên Cơ bản | Kim loại | Thép và sản phẩm thép |

### 3.2 ⚠️ Phát hiện làm thay đổi thiết kế: dấu phơi nhiễm dầu KHÁC NHAU

Đề xuất ban đầu xếp PVT chung rổ với dầu khí và dùng **một** cổng "giá dầu tăng". **Sai về kinh tế học:**

- **Phơi nhiễm DOANH THU (giá dầu ↑ ⇒ tốt):** `PVD`, `PVS` (dịch vụ khoan — giá dịch vụ bám giá dầu), `BSR` (crack spread), `PLX` (biên phân phối).
- **Phơi nhiễm CHI PHÍ (giá dầu ↑ ⇒ xấu):** `PVT` — ICB là **Vận tải Thủy**; dầu là **nhiên liệu**, và đồ thị đã có sẵn cạnh chi phí: `LOGI:OIL_PRICE→OPERATING_EXPENSE` (`causal_edge.py:932`). Giá dầu ↑ làm bào mòn biên, không phải mở rộng.

⇒ **Một cổng `OIL ↑` sẽ kích hoạt PVT đúng lúc nó bị bất lợi nhất.** Vì vậy:

**Luật cứng: mỗi mã có một `exposure_sign` riêng, lấy từ signed causal graph (C3), KHÔNG suy từ tên ngành.** C2 phải kiểm chứng **chính cạnh của mã đó** hợp lệ trên dữ liệu thật, không phải "giá dầu đã tăng".

### 3.3 Tầng universe

| Tầng | Nội dung | Trạng thái |
|---|---|---|
| **U1 — dùng được** | `BSR`, `GAS` (đã có archetype human-validated: CYCLICAL_HEAVY / REGULATED_UTILITY) | ✅ |
| **U2 — ICB-validated, chờ `exposure_sign`** | `PLX`, `PVD`, `PVS` (doanh thu) + `PVT` (chi phí — **dấu âm**) | ✅ ICB có; ⏳ chờ khai báo `exposure_sign` từ C3 |
| **U3 — loại khỏi Track 2** | `MSR` — ICB là **Khai khoáng**, driver là giá khoáng sản **khác** giá dầu; không có chuỗi giá tương ứng trong `macro_history` ⇒ C2 không tính được. `HPG` cũng loại khỏi rổ **dầu**, nhưng thuộc nhánh kim loại/`CYCLICAL_HEAVY` nếu sau này có chuỗi giá thép | ❌ |

**Quy tắc cứng:** không mã nào vào U1/U2 mà thiếu **cả hai** (nguồn ICB + `exposure_sign` có kiểm chứng). Thêm mã bằng trực giác sẽ phá tính truy vết của cả track.

---

## 4. ĐIỀU KIỆN KÍCH HOẠT (thay thế cổng confidence không đo được)

Bốn điều kiện, **tất cả phải đúng**, cho từng mã:

| # | Điều kiện | Thước đo | Ngưỡng |
|---|---|---|---|
| C1 | Regime không tệ hơn ngưỡng | `regime_history.status` | ∈ {TRENDING, RANGING} **hoặc** CRISIS nhưng `breadth_pct` tăng ≥2 phiên liên tiếp |
| C2 | **Sức mạnh nhân quả ĐO ĐƯỢC** (thay cho `confidence` tĩnh) | **hit-rate của đường nhân quả trên dữ liệu thật**: sign-agreement giữa Δ`OIL_PRICE`(t) và forward return của mã trong `lag_min..lag_max` (từ `causal_edge`), cửa sổ cuộn 120 phiên | ≥ 0,60 **và** n ≥ 30 mẫu |
| C3 | Dòng ngoại của **chính mã đó** | `market_foreign_history` (flow_kind='daily') | mua ròng ≥ **2 phiên liên tiếp** |
| C4 | Thanh khoản | ADV20 của mã | ≥ 50k cp/phiên (cổng chuẩn của hệ thống) |

**Vì sao C2 thay đổi:** cổng cũ (`confidence ≥ 0.80`) chỉ lọc các cạnh mà *tác giả* đã gán cao — nó không phản ánh thị trường đang diễn ra thế nào. Hit-rate trên dữ liệu thật là thứ **đo được, kiểm chứng được, và có thể sai** — đúng tinh thần zero-hallucination.

Nếu `polarity_reconcile.py` (C3, đã có) xuất ra được sign-agreement theo thời gian → **tái dùng nó**, không viết engine mới.

---

## 5. POSITION SIZING & KILL SWITCH

### 5.1 Sizing theo pha (chấp nhận đề xuất thắt chặt của chủ hệ thống)

| Pha | Điều kiện mở pha | Trần NAV cho **toàn bộ** Track 2 | Số mã tối đa |
|---|---|---|---|
| **P1 — validation** | mặc định khi mới bật | **≤ 5%** | 2 |
| **P2 — track record** | ≥ 3 tháng P1 **và** Sharpe > 0,5 | ≤ 10% | 4 |
| **P3 — mở rộng** | ≥ 3 tháng P2 **và** Sharpe > 1,0 | ≤ 15% | 6 |

**Ghi chú:** đề xuất ban đầu (≤15–20% ngay từ đầu) bị bác — Track 2 chưa có backtest ngoài mẫu, và cửa sổ dữ liệu chỉ phủ ~1 chu kỳ hàng hoá (F4). Mức 15–20% chỉ hợp lý **sau** P2.

### 5.2 Kill switch (bắt buộc — thoát tự động, không cần người quyết)

Đóng vị thế Track 2 khi **bất kỳ** điều kiện nào đúng:

| # | Điều kiện | Ngưỡng |
|---|---|---|
| K1 | Dòng ngoại của chính mã đảo chiều | bán ròng ≥ 3 phiên liên tiếp |
| K2 | Đường nhân quả đảo dấu | sign-agreement của C2 < 0,40 |
| K3 | Drawdown từ đỉnh của vị thế | > **8%** |
| K4 | Regime xấu đi | chuyển sang CRISIS **và** breadth giảm 3 phiên liên tiếp |
| K5 | Kill switch toàn cục | `ENTRY_LOCK` bật lại → đóng **tất cả** Track 2 ngay lập tức |

### 5.3 Correlation với Track 1 (Safeguard 4 — chấp nhận)

`alloc_T1 + alloc_T2 ≤ 100%`. Nếu corr(T2, T1) > 0,7 (tương quan lợi suất 60 phiên) → **giảm** size T2 theo hệ số `(1 − corr)`. Không mã nào trong Track 2 được làm tổng vượt trần danh mục.

---

## 6. TÍCH HỢP VÀO `final-decision`

Output mở rộng (giữ nguyên các trường cũ để không vỡ consumer):

```json
{
  "entry_lock": true,
  "track1": {"alloc_pct": 0, "reason": "regime=CRISIS"},
  "track2": {"enabled": false, "reason": "entry_lock=true ⇒ Track 2 = 0 (bất biến)",
             "universe": ["BSR","GAS"], "candidates": []},
  "total_alloc_pct": 0
}
```

Quy tắc:
- `track2.enabled = false` **bắt buộc** khi `entry_lock = true`.
- `total_alloc_pct = track1 + track2`, assert ≤ 100.
- Mọi `reason` phải trích từ mã điều kiện (C1..C4, K1..K5), không văn xuôi tự do — tái dùng nguyên tắc Narrative Layer.

---

## 7. TEST PLAN

**Unit**
1. `test_entry_lock_override_track2` — `entry_lock=true` ⇒ Track 2 = 0 **dù mọi điều kiện C đều đúng** (bất biến #1).
2. `test_c2_hitrate_thay_confidence_tinh` — mã có `confidence` tác giả 0,90 nhưng hit-rate thật 0,35 ⇒ **KHÔNG** kích hoạt.
3. `test_c3_can_2_phien_mua_rong` — 1 phiên mua ròng ⇒ không kích hoạt; 2 phiên liên tiếp ⇒ kích hoạt.
4. `test_nan_khong_kich_hoat` — thiếu dữ liệu ngoại ⇒ fail-closed, không mặc định ON.
5. `test_kill_switch_k3_drawdown` — drawdown 8,1% ⇒ đóng vị thế.
6. `test_sizing_tran_theo_pha` — P1 không được vượt 5% kể cả khi có 6 ứng viên.
7. `test_corr_cap_tong_alloc` — corr > 0,7 ⇒ T2 giảm, tổng ≤ 100%.

**Integration**
8. Kịch bản 08/10 replay: CRISIS + PLX mua ròng 2 phiên + ENTRY_LOCK=TRUE ⇒ **total = 0%** (chứng minh không có đường vòng).
9. Kịch bản giả định ENTRY_LOCK=FALSE + PLX đủ C1–C4 ⇒ T2 = 5% P1, không hơn.

**Backtest (bắt buộc trước khi bật P1 thật)**
10. Chạy trên 1.391–1.435 phiên của U1 (BSR, GAS), **walk-forward**, báo cáo cả **in-sample và out-of-sample**. Không đạt OOS ⇒ không bật.

---

## 8. ĐIỀU KIỆN TIÊN QUYẾT (chưa có thì spec chưa thi hành được)

| # | Việc | Trạng thái | Vì sao chặn |
|---|---|---|---|
| A | Bảng ICB cho PLX/PVT/PVD/PVS | ✅ **ĐÃ CÓ** (`symbol_industry`, F7) | — |
| B | Thước đo hit-rate nhân quả (hoặc tái dùng `polarity_reconcile`) | ⏳ **CẦN LÀM** | F3: `confidence` tĩnh không dùng làm cổng được |
| C | Chuỗi giá dầu lịch sử có provenance | ✅ **ĐÃ CÓ** (`BRENT_OIL`/`WTI_OIL`, 2021-04→2026-10-06, F8) | ⚠️ còn phải xử lý độ trễ 2 phiên của chuỗi macro |
| **F** | **`exposure_sign` cho từng mã U2, lấy từ signed causal graph** | ⏳ **CẦN LÀM — MỚI, BẮT BUỘC** | Mục 3.2: PVT đi ngược chiều PLX/PVD/PVS; dùng chung một dấu sẽ vào lệnh sai |
| D | Backtest harness walk-forward cho rổ nhỏ | ⏳ **CẦN LÀM** | mục 7.10 |
| E | Vá `rs_ranker` | ⏳ P2 đang treo | nếu Track 2 dùng RS ranking thì không được phụ thuộc subsystem đã chết 4 tháng (F5) |

---

## 9. NGOÀI PHẠM VI

- KHÔNG nới `ENTRY_LOCK` trong bất kỳ hình thức nào.
- KHÔNG thêm mã vào U2 bằng trực giác.
- KHÔNG dùng LLM trong đường ra quyết định.
- KHÔNG sửa Track 1 — mọi thay đổi Track 1 là spec riêng.
- KHÔNG dùng `MSR` (U3).

---

## 10. CÂU HỎI MỞ (cần chủ hệ thống chốt trước khi code)

1. **Nguồn giá dầu:** hệ thống đã có chuỗi Brent/WTI lịch sử + provenance chưa? Nếu chưa, C2 bất khả thi và Track 2 phải hoãn tới khi có.
2. **Pha P1 dùng tiền thật hay paper?** Đề xuất: **paper 1 tháng** trước, vì Track 2 chưa có track record nào.
3. **Trần 5% NAV của P1** — tính trên NAV tổng hay trên phần tiền mặt khả dụng? Đề xuất: NAV tổng, để nhất quán với Track 1.
4. Ai duyệt việc chuyển pha P1→P2: tự động theo Sharpe hay cần duyệt tay? Đề xuất: **duyệt tay** (đúng tiền lệ GO/NO-GO của hệ thống).
