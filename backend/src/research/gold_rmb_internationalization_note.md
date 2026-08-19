# Thesis note — De-dollarization, RMB internationalization và Gold (verdict note)

Trạng thái: **HYPOTHESIS framework — KHÔNG phải model input.**
Zero-Hallucination: không có số định lượng nào trong note này được đưa vào
Gold Forecast Engine. Mọi claim đều là cấu trúc khái niệm (framework), chưa
có PIT evidence kiểm định.

## Verdict thesis tổng thể

> **Phi đô la hóa không phải cuộc thay thế USD bằng một đồng tiền duy nhất.
> Nó là sự phân mảnh từng lớp của hệ thống USD.**

Các tầng cạnh tranh — mỗi tầng một "cuộc chơi" khác nhau:

| Tầng | USD | Gold | RMB |
|---|---|---|---|
| Reserve / neutral asset | chiếm ưu thế | **cạnh tranh ở đây** | chưa sẵn sàng (issuer liability) |
| Trade + settlement | chiếm ưu thế | không | **CIPS từ 2015 — infrastructure layer** |
| Funding / liquidity | chiếm ưu thế | không | **đang cố xây** (swap, loan, deposit) |
| Collateral / repo | chiếm ưu thế (UST plumbing) | không | **frontier — CGB repo chưa chứng minh được** |

USD vẫn mạnh nhất vì **kết nối tất cả các tầng cùng lúc** (network-liquidity
mechanism, không phải chỉ "chiếm X% dự trữ").

## Vàng và RMB không cạnh tranh trực tiếp

- **Gold** = hedge khỏi **monetary issuer risk** — tài sản không có liability
  của bất kỳ quốc gia nào; không credit risk; thanh khoản quốc tế.
- **RMB** = **alternative monetary network** — vẫn là liability của hệ thống
  tiền tệ Trung Quốc, chịu capital controls, PBOC policy, convertibility,
  market depth, geopolitical/legal risk.

Do đó có thể đồng thời xảy ra:

```
USD share ↓  =  Gold ↑ + RMB ↑ + EUR ↑ + Others
```

Và **vàng có thể hưởng lợi ngay cả khi RMB không bao giờ thay USD** —
nếu USD fragmentation tăng nhưng RMB chưa đủ deep, vàng là **neutral reserve
asset** hấp thụ phần demand không muốn chuyển sang một sovereign liability khác.

## Phản biện quan trọng

1. **CIPS ≠ USD replacement.** CIPS (2015) chỉ giải quyết payment/settlement
   infrastructure. Một hệ thống tiền tệ toàn cầu còn cần: funding, liquidity
   (stress provider), collateral, market-making, lender of last resort.
   → Swap lines + repo + RMB bond market + offshore RMB liquidity mới là thứ
   đáng theo dõi, không phải chỉ CIPS volume.

2. **"Doanh nghiệp vay RMB → demand RMB vĩnh viễn" là SAI một phần.**
   Doanh nghiệp có thể Borrow RMB → Swap RMB/EUR và hedge toàn bộ exposure.
   → **RMB internationalization ≠ RMB accumulation.** Khoản vay tạo demand
   cho RMB *funding*, không nhất thiết demand ròng dài hạn cho RMB *reserve*.

3. **Repo giúp tài sản thành collateral hữu dụng nhưng không loại bỏ risk.**
   Vẫn có haircut, liquidity/duration/credit/convertibility/jurisdiction/
   counterparty/collateral-eligibility risk. UST mạnh vì quy mô + thanh khoản +
   pháp lý + Fed + dealer network + dollar funding market; **network effect
   không thể copy bằng decree**.

## Con số "64% nợ toàn cầu bằng USD" — KHÔNG dùng

Không bê con số này vào framework nếu chưa xác định numerator/denominator.
Có ít nhất 3 khái niệm khác nhau (không tương đương):

- international debt denominated in USD;
- foreign-currency debt;
- global debt outstanding.

Điểm kinh tế đúng hơn: **USD có network effect vì các chủ thể ngoài Mỹ có
nghĩa vụ USD** ("dollar wins because the world owes dollars").

## 4 chỉ báo theo dõi (nếu muốn thành monitor)

Chỉ CIPS volume tăng = internationalization of *payment*, chưa phải của *tiền tệ*.
Cần bằng chứng ở lớp funding-currency:

1. **RMB cross-border credit** — doanh nghiệp nước ngoài thực sự vay CNY.
2. **RMB swap / liquidity facilities** — ai tiếp cận RMB khi stress.
3. **RMB collateral / repo depth** — RMB bonds có thành collateral thật không.
4. **Foreign RMB liabilities + foreign RMB assets** — bảng cân đối người
   nước ngoài có "dính" RMB hay chưa.

Chỉ khi cả 4 cùng tăng → bằng chứng funding-currency internationalization.

## Con đường thành Gold v1.x — nếu muốn kiểm định

Xây **RMB Internationalization Index** PIT-clean:

```
RMB_intl = f(RMB settlement, RMB funding, CIPS, offshore deposits,
             RMB bonds, swap liquidity, repo/collateral)
```

Rồi kiểm định incremental power:

```
ΔGold_t ~ RMB_intl,t + CB_Gold_t + M1_t
```

Câu hỏi quyết định: **RMB index có incremental information ngoài CB gold
accumulation (M2) và monetary variables (M1) hay không?**

- Không → narrative.
- Có → bằng chứng RMB internationalization tạo cơ chế cầu vàng gián tiếp.

## Why test RMB — not because the structural thesis predicts gold

RMB is tested because internationalization can alter the monetary/funding
architecture underlying reserve allocation and cross-border liquidity. This
makes RMB a theoretically distinct candidate from M1/M2, but **theoretical
relevance does not imply predictive power**. Therefore each RMB component must
first pass an independent incremental OOS test conditional on M1+M2. Only
components with stable incremental evidence may proceed to a composite; if the
composite adds no incremental information, it is rejected.

### Protocol khóa trước (component-wise, không gộp sớm)

```
M1
 ↓
M1 + M2
 ↓
M1 + M2 + RMB_1
M1 + M2 + RMB_2
...
M1 + M2 + RMB_n
 ↓
incremental OOS?        (ΔAUC_OOS = AUC(M1+M2+RMB_i) - AUC(M1+M2) > 0)
 ↓
stable across years/regimes?
 ↓
redundancy / residual test
 ↓
Composite only if justified
```

Điều kiện tối thiểu: `ΔAUC_OOS > 0`, nhưng **ΔAUC > 0 đơn thuần chưa đủ** —
cần stability theo năm/regime và không collapse khi kiểm tra redundancy.

### Ba khả năng hoàn toàn khác nhau

1. **RMB có latent factor riêng** → đáng đưa vào model.
2. **RMB chỉ là proxy của M1/M2/China trade** → correlation cao nhưng
   incremental ≈ 0 → loại.
3. **Structural thesis đúng nhưng RMB không có predictive timing** → thesis
   giữ nguyên, feature bị loại.

**Trường hợp (3) quan trọng: loại feature không đồng nghĩa bác bỏ thesis
kinh tế.** "Why test" ≠ "why believe" — RMB được kiểm định vì là candidate
mechanism có thể đo lường, không phải vì đã tin nó sẽ dự báo vàng.

Boundary tổng thể:

```
RMB internationalization
  ⇏  RMB reserve dominance
  ⇏  Gold price increase
```

Muốn nối ba vế thành evidence định lượng → phải qua PIT data + incremental
OOS test. Note này không phải pseudo-model specification; protocol chỉ chuyển
thành spec thực thi khi có dữ liệu mở Gold v1.x.

## Japan Repatriation & Global Liquidity — Structural Watchlist

RMB/Gold nói về cấu trúc reserve–funding; Japan repatriation nói về
**transmission qua capital flows / global liquidity**. Đây là mắt xích bổ sung,
không phải driver độc lập.

Transmission hypothesis:

```
JGB30Y ↑
   ↓
FX hedge cost ↑
   ↓
Japanese insurer foreign-bond attractiveness ↓
   ↓
Foreign holdings ↓ / domestic allocation ↑
   ↓
UST marginal demand ↓
   ↓
Global term premium / liquidity conditions
   ↓
Gold transmission
```

**4 biến quan sát** (structural watchlist — chưa phải feature):

1. JGB 30Y yield
2. USD/JPY hedging cost
3. Japanese insurer foreign-bond holdings/flows
4. Japanese UST holdings/flows

**Ba điều kiện khóa:**

> **Headline yield ≠ repatriation.**
> **Repatriation ≠ UST crisis.**
> **UST selling ≠ automatically bullish gold.**

Chỉ khi có **flow evidence + PIT vintage + incremental OOS H20** mới được phép
nâng từ *structural hypothesis* → *candidate feature*. Cần đo **flow**, không
phải headline yield; không dùng câu "Nhật là bên rút tiền gây khủng hoảng
1997" (Asian Crisis có nhiều cơ chế đồng thời — USD peg, short-term FC debt,
maturity mismatch, inflow reversal, banking fragility, reserve depletion).

## Kiến trúc tổng thể (giữ nguyên)

```
EMPIRICAL
M1 + M2 ──────────────→ Gold H20 signal
GVZ ──────────────────→ uncertainty / PI

STRUCTURAL
Gold ── Reserve
RMB ─── Funding
CGB ─── Collateral
Japan ─ Capital-flow transmission
CIPS ── Infrastructure

                 ↓
          hypothesis layer
                 ↓
        NEVER explain forecast
        retrospectively
```

Nguyên tắc vận hành: **Detect → Estimate → Stress → Update** — không tiên tri
tương lai, xây hệ thống có lợi thế thống kê nhỏ, đo được, cập nhật khi state mới.
Economic narrative ≠ forecasting system.

## Tương quan với research Gold hiện tại (evidence đã có)

- M1 (DXY / real yields / monetary): baseline, **direction skill yếu**.
- M2 (IFS central-bank gold reserve): **H20 ΔAUC +0.060**, ổn định IS — evidence
  tốt nhất hiện tại.
- ETF (private capital): **NOT TESTABLE** strict historical PIT + diagnostic
  không ổn định → chưa được phép đưa vào model (xem `gold_etf_gate_e1_note.md`).

→ Narrative này **đi xa hơn evidence** nếu coi là driver. Giữ ở tầng framework.

## Boundary

- **KHÔNG đưa hypothesis này vào Gold Forecast Engine.**
- Model vẫn frozen (v0.2 direction + v0.3C GVZ PI, commit b2001a2) —
  chỉ thay đổi nếu có incremental PIT evidence và mở version mới qua gate.
- Note này chỉ là provenance của framework, không phải research gate.