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

## 10. CÂU HỎI MỞ — **ĐÃ CHỐT** (2026-10-08)

| # | Quyết định | Lý do đã chốt |
|---|---|---|
| 1 | **P1 chạy PAPER 1 tháng trước (bắt buộc)** | Track 2 chưa có OOS trên tài khoản thực; không dùng tiền thật để test kiến trúc chưa có ≥20–30 phiên paper kiểm soát drawdown |
| 2 | **Trần tính trên NAV TỔNG** | Nếu tính trên tiền mặt khả dụng, cùng một rủi ro lại ra 2 số khác nhau tuỳ mức giải ngân của Track 1 → phá tính nhất quán của risk budgeting |
| 3 | **Chuyển pha P1→P2 = DUYỆT TAY** | Sharpe chỉ là điều kiện *cần*; nâng trần phải qua audit slippage + chi phí thanh khoản + độ ổn định API |

---

## 11. ADDENDUM A — `exposure_sign`: định nghĩa & cách tính

### 11.1 Định nghĩa

```
exposure_sign[symbol, factor] ∈ {-1, 0, +1}
  +1 : factor tăng ⇒ kỳ vọng phía target (biên/doanh thu) TĂNG
  -1 : factor tăng ⇒ kỳ vọng phía target GIẢM  (PVT: dầu = chi phí)
   0 : không có phơi nhiễm rõ ràng ⇒ KHÔNG vào universe (fail-closed)
```

### 11.2 `exposure_sign` là **GIẢ THUYẾT KHAI BÁO TRƯỚC**, không phải kết quả đo

Đây là điểm tôi **phản biện lại đề xuất trong review**: đề xuất viết test
`assert exposure_sign["PLX","OIL_PRICE"] == +1`. **Không nên**, vì PLX là **nhà phân phối xăng dầu** (ICB ghi "Sản xuất Dầu khí" nhưng kinh tế học là mua–bán lại): giá dầu tăng có thể **nén** biên do độ trễ chuyển giá. Dấu của PLX **không hiển nhiên** — hard-code `+1` vào test là khoá cứng một giả thuyết chưa chứng minh.

Thiết kế đúng:
1. Mỗi mã trong U1/U2 khai báo `exposure_sign` **kèm nguồn** (ICB + lập luận), lưu trong file cấu hình có version — **khai trước khi đo**.
2. C2 (mục 4) **kiểm chứng** giả thuyết đó trên dữ liệu thật.
3. Nếu dấu đo được **ngược** dấu khai báo ⇒ **LOẠI mã khỏi Track 2 + mở ticket**, **KHÔNG tự động lật dấu** (lật theo dữ liệu = fit vào nhiễu).

Nguồn compute:
- **P1:** thủ công cho U1/U2, mỗi dòng kèm `source` (ICB + cạnh nhân quả tương ứng).
- **P2+:** suy từ dấu đường `symbol → GROSS_MARGIN` trong `causal_edge`, **đối chiếu** với bản thủ công.

### 11.3 Test bắt buộc

```python
def test_pvt_oil_la_chi_phi():
    """Duy nhất ca không thể tranh cãi: ICB Vận tải Thủy, dầu là nhiên liệu."""
    assert exposure_sign["PVT", "OIL_PRICE"] == -1

def test_moi_ma_trong_universe_deu_co_dau_va_nguon():
    for s in UNIVERSE_U1 + UNIVERSE_U2:
        assert sign[s] in (-1, +1) and sign_source[s], "thiếu dấu/nguồn ⇒ loại khỏi universe"

def test_dau_do_nguoc_khai_bao_thi_loai_ma():
    """Giả thuyết bị phản bác ⇒ loại, KHÔNG lật dấu."""
    assert decide(declared=+1, measured_sign=-1).excluded is True
```

---

## 12. ADDENDUM B — định nghĩa chính xác `hit_rate`

### 12.1 Công thức

Cho cạnh `x → y` với `lag_min..lag_max` (lấy từ `causal_edge`), `k = (lag_min+lag_max)//2`, cửa sổ cuộn `W = 120` phiên:

```
Δx_t     = x_t − x_{t−k}
Δy_{t+k} = y_{t+k} − y_t
agree_t  = 1 nếu sign(Δy_{t+k}) == exposure_sign[x,y] * sign(Δx_t), ngược lại 0
hit_rate = mean(agree_t)   trên các t hợp lệ
```

Kích hoạt khi **`hit_rate ≥ 0.60`** (0,65 với tiên nghiệm LOW) **và số mẫu độc lập ≥ 30**.

> ⚠️ **SỬA LUẬT (2026-10-08, khi triển khai):** không dùng điểm ước lượng để kết luận.
> Bản đầu ghi "hit_rate < 0.50 ⇒ REJECT_TICKER" — **sai về thống kê**: với n=50, SE ≈ 0,07,
> nên hr = 0,46 **không phân biệt được** với tung đồng xu; tuyên bố "ngược dấu" là nói quá
> bằng chứng. Luật đúng dùng **khoảng tin cậy Wilson 95%**:
> - **CONFIRMED** ⟺ `CI_low > 0.50` **và** `hit_rate ≥ ngưỡng`
> - **REJECT_TICKER** (dấu ngược) ⟺ `CI_high < 0.50`
> - còn lại → **NOT_CONFIRMED** (không kết luận được — vẫn fail-closed: cổng không mở)

### 12.1.1 Kết quả đo thật (BRENT_OIL, 2021-04 → 2026-10, toàn bộ lịch sử)

| mã | sign | k (phiên) | N độc lập | hit-rate | CI 95% | verdict |
|---|---|---|---|---|---|---|
| BSR | +1 | 20 | 61 | 0,492 | [0,371 ; 0,614] | NOT_CONFIRMED |
| GAS | +1 | 30 | 41 | 0,463 | [0,321 ; 0,613] | NOT_CONFIRMED |
| PVD | +1 | 25 | 50 | 0,460 | [0,330 ; 0,596] | NOT_CONFIRMED |
| PVS | +1 | 25 | 51 | 0,471 | [0,341 ; 0,605] | NOT_CONFIRMED |
| PLX | +1 | 5 | 225 | 0,529 | [0,464 ; 0,593] | NOT_CONFIRMED |
| PVT | −1 | 10 | 120 | 0,575 | [0,486 ; 0,660] | NOT_CONFIRMED |

**KHÔNG một mã nào qua cổng.** Mọi CI đều vắt qua 0,50.

### 12.1.2 Hệ quả — Track 2 KHÔNG được bật

Tiền đề của Track 2 ("giá dầu dẫn dắt rổ dầu khí theo một dấu khai báo trước, trong 20–30 phiên")
**không được dữ liệu ủng hộ**. Do đó, theo mục 8 (tiên quyết), **Track 2 giữ nguyên OFF**;
`ENTRY_LOCK` vẫn là chốt chặn duy nhất và 100% tiền mặt vẫn đúng.

Điều này **không** nói rằng "không tồn tại quan hệ" — chỉ nói rằng **đặc tả này không chứng minh được nó**.
Đặc tả khác (hồi quy theo lợi suất, dùng WTI, horizon khác, bỏ lọc lag) có thể cho kết quả khác,
nhưng thử nhiều đặc tả rồi chọn cái đẹp nhất **chính là data-snooping** — muốn thử phải
**đăng ký trước** (pre-register) đặc tả và giữ nguyên tiêu chí.

### 12.2 ⚠️ Phản biện quan trọng: "30 mẫu" với cửa sổ trượt là **sai thống kê**

Cửa sổ trượt làm các mẫu **tự tương quan**: với `k = 12`, 120 mẫu trượt chỉ chứa **~10 quan sát độc lập**, không phải 120. Đếm `n ≥ 30` theo kiểu trượt là **đếm ảo** → hit-rate trông chắc chắn hơn thực tế.

**Sửa:** lấy mẫu **không chồng lấn**, bước `step = k+1` ⇒ số mẫu độc lập thật. Nếu số mẫu độc lập `< 30` ⇒ **không kích hoạt** (fail-closed), dù hit_rate cao.

### 12.3 Test bắt buộc

```python
def test_hit_rate_random_xap_xi_50pct():
    x, y = np.random.randn(2000), np.random.randn(2000)
    assert 0.45 < compute_hit_rate(x, y, lag=5) < 0.55

def test_hit_rate_y_bang_x_thi_100pct():
    x = np.random.randn(2000)
    assert compute_hit_rate(x, x, lag=5) > 0.99

def test_dem_mau_doc_lap_khong_chong_lan():
    """120 mẫu trượt với k=12 KHÔNG được coi là 120 mẫu độc lập."""
    assert independent_n(n_sliding=120, k=12) < 30
```

---

## 13. ADDENDUM C — thứ tự ưu tiên kill switch (chống "half-kill")

Nhiều trigger cùng lúc mà không có thứ tự ⇒ hệ thống có thể đóng 50% trong khi ngoại đang xả — **tệ hơn không kill** (giữ lại đúng phần đang xấu nhất).

**Luật: xét từ trên xuống, trigger đầu tiên khớp thì THI HÀNH, dừng.**

| Ưu tiên | Trigger | Hành động |
|---|---|---|
| 1 | K3 — drawdown > 8% | **Đóng 100% ngay** |
| 2 | K5 — `ENTRY_LOCK` bật lại | **Đóng 100% ngay** |
| 3 | K1 — ngoại đảo chiều ≥ 3 phiên | **Đóng 100% ngay** |
| 4 | K2 — hit_rate < 0.40 | Đóng 50% |
| 5 | K4 — regime xấu đi (CRISIS + breadth giảm 3 phiên) | **Không mở mới** (giữ vị thế hiện có) |

Bổ sung bắt buộc (auditability):
- Log ghi **TẤT CẢ** trigger đang bật, không chỉ trigger được thi hành.
- Log ghi `trigger_id` đã quyết định hành động ⇒ truy vết được vì sao đóng/mở.
- Thứ tự ưu tiên là **hằng số trong code, có test** — không hard-code rải rác.

```python
def test_uu_tien_k3_thang_k2():
    """K3 (drawdown) và K2 (hit-rate) cùng bật ⇒ đóng 100%, không phải 50%."""
    assert decide_exit([K3, K2]).action == "CLOSE_FULL"

def test_khong_bao_gio_chi_dong_50pct_khi_co_trigger_uu_tien_cao():
    for trig in ([K3, K1], [K5, K2], [K1, K2]):
        assert decide_exit(trig).action == "CLOSE_FULL"
```

---

## 14. NGOÀI PHẠM VI (cập nhật)

Giữ nguyên mục 9, bổ sung: 3 addendum trên **phải hoàn tất trước khi viết dòng code Track 2 nào** (ưu tiên B → F → D trong mục 8).
