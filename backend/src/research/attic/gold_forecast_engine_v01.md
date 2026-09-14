# Gold Forecast Engine v0.1 — Research Track độc lập

> **Ngày mở:** 2026-08-15 | **Track:** GOLD_FORECAST_V01 (độc lập, không nằm trong equity pipeline)
> **Script:** `backend/src/research/gold_forecast_engine_v01.py` (READ-ONLY)
> **Output:** `backend/data/reports/gold_forecast_engine_v01_audit.csv`

## Mục đích

Trả lời câu hỏi **falsifiable**:

> Các "narrative" về USD (monetary / de-dollarization / fiscal / geopolitical) có
> **incremental predictive power** cho vàng sau khi kiểm soát monetary baseline
> hay không — kiểm định bằng **walk-forward time-series**, không random split,
> không bê nguyên cross-sectional RankIC từ Equity.

## Nguyên tắc kế thừa từ p_gain (Equity Gate 1)

`p_gain` nhìn rất thuyết phục in-sample nhưng FAIL khi walk-forward và không rank
được cổ phiếu nội ngày. Với Gold:

- Gold chỉ có **1 observation/ngày** → KHÔNG dùng RankIC cross-sectional.
- Dùng **time-series predictive framework**:
  \[
  X_t \rightarrow P(R_{Gold,t\rightarrow t+H} > 0), \quad H \in \{20, 60, 120\}
  \]
- Gate tương đương p_gain: **nếu một biến chỉ dự báo tốt trong một giai đoạn
  thuận lợi nhưng mất power khi walk-forward → FAIL.**

## Hypotheses & tính khả dụng dữ liệu (audit thật từ DB 2026-08-14)

| Hyp | Narrative | Biến cần | Trạng thái trong DB |
|---|---|---|---|
| H1 Monetary | RealYield, DXY, Liquidity | DXY, US10Y, TIP_PRICE, US2Y | ✅ DENSE 2021-08→2026-08 |
| H2 Reserve | CB buying, ETF flows, reserve div | CBGoldBuying, ETFFlows | ❌ KHÔNG CÓ → đứng ngoài |
| H3 Fiscal | Debt/GDP, TreasurySupply, TermPremium | TermPremium proxy = US10Y−US2Y | ⚠️ chỉ có proxy TermPremium |
| H4 Geopolitical | Shock, Oil, Hormuz | WTI_OIL | ⚠️ chỉ có Oil |
| H5 De-dollarization | USD ReserveShare, CNYSettlement, CIPS | USD_CNY | ⚠️ chỉ có USD_CNY (proxy FX) |

**Zero-Hallucination guard:** H2 không có data → không mô phỏng, không bịa; chỉ
test M1 monetary + các block có data thật (M1→M1+term premium→M1+oil→M1+cny),
mỗi bước là **incremental test sau khi kiểm soát M1**.

## Baselines

```
M0 = persistence / historical base rate   (base rate P(R>0) theo H)
M1 = monetary-only                        (DXY, US10Y, TIP real-yield proxy)
M2 = M1 + term premium                    (H3 proxy)
M3 = M1 + oil                             (H4 proxy)
M4 = M1 + USD_CNY                         (H5 proxy)
```

## Protocol (walk-forward, không random split)

1. **PIT load**: `macro_history` từ `screener_cache.db`, dedup (MAX rowid per
   date,variable — TIP_PRICE có 2 giá trị/ngày, giữ bản cuối).
2. **Target**: forward return gold `R(t→t+H)`, H ∈ {20,60,120}; biến nhị phân
   `R>0`.
3. **Features** (chỉ dùng giá trị đến ngày t — PIT, không lookahead):
   - DXY z-score + momentum 5d
   - US10Y thay đổi 5d
   - TIP_PRICE (real yield proxy) momentum 5d
   - Term premium = US10Y − US2Y (level)
   - WTI momentum 5d
   - USD_CNY momentum 5d
4. **Walk-forward expanding window**: train từ khởi điểm → dự báo block kế tiếp,
   refit mỗi block; metric đánh giá CHỈ trên OOS block.
5. **Metrics**: directional accuracy P(R>0) vs base rate M0, AUC, per-year
   accuracy, per-horizon. Incremental = Δ(accuracy | M1+X) − accuracy(M1).
6. **Verdict**: block nào có incremental accuracy OOS dương ổn định qua
   năm/horizon → giữ; không → đứng ngoài như p_gain.

## Guardrail

- **KHÔNG dùng kết quả Equity để giả định Gold có alpha.**
- KHÔNG tối ưu/threshold/refit theo OOS; walk-forward là expanding window, block
  đánh giá luôn sau khối train.
- KHÔNG bịa CB/ETF/reserve data — thiếu → đứng ngoài.
- `GOLD_XAU` = giá vàng thế giới (USD/oz, nguồn yfinance GC=F / investing);
  dùng cho nghiên cứu, không phải khuyến nghị đầu tư.

## Kết quả v0.1 (chạy 2026-08-15, walk-forward expanding window, sklearn LogisticRegression)

**Verdict: NONE của các narrative proxy (term premium / oil / CNY) có bằng chứng
incremental predictive power sau khi kiểm soát M1 monetary. Thậm chí M1 monetary
tự nó KHÔNG có skill (AUC ≈ 0.5 ở mọi horizon).**

| H | M1 acc | base rate | M1 AUC | Δ M2_term acc | Δ M3_oil acc | Δ M4_cny acc |
|---|---:|---:|---:|---:|---:|---:|
| 20 | 71.1% | 70.1% | 0.512 | −1.84pp | +0.00pp | +0.00pp |
| 60 | 80.9% | 85.9% | 0.504 | −2.17pp | +0.00pp | +0.00pp |
| 120 | 87.8% | 93.5% | 0.473 | −1.43pp | −0.13pp | +0.00pp |

**Đọc kết quả (zero-hallucination, không tô hồng):**

1. **Accuracy cao nhưng base rate cao ngang** — vàng 2021-2026 tăng gần như liên
   tục nên dự báo "up" trúng đa số (~70-93%). Chỉ số skill thật là **AUC**, và
   AUC ≈ 0.50 (M1) hoặc < 0.50 (M1+H120) → **không có phân biệt** giữa thắng/thua.
2. **Từng block incremental đều Δacc ≤ 0** — term premium làm acc giảm (trái dấu
   với dự đoán fiscal), oil/CNY không thêm gì. Không narrative nào vượt monetary
   baseline theo chiều dương ổn định.
3. **p_gain lesson được tái lập**: các biến nhìn "hợp lý" (TermPremium, USD_CNY)
   không có power khi kiểm định walk-forward → **FAIL cho cả M2/M3/M4 ở v0.1.**
4. **H2 (Reserve/CB/ETF) không có data** → không thể kết luận gì, không mô phỏng.

**Ý nghĩa:** "Mỹ → de-dollarization → Gold" KHÔNG có bằng chứng predictive ở
v0.1 với feature đơn giản (level/momentum 5d). Cần nâng cấp theo 1 trong 3
hướng trước khi gọi là track có triển vọng:

- **Feature nâng cấp**: thêm chế độ tần số (regime-conditioned), lags dài hơn
  (H20/H60/H120 cần feature tại nhiều lags), phi tuyến, hoặc chuẩn hóa theo
  regime (không đánh giá tuyệt đối — theo Luật bất biến).
- **Thêm data**: CB GoldBuying / ETF flows / USD reserve share (H2/H5 thật) —
  hiện KHÔNG CÓ trong DB.
- **Target khác**: thay vì directional P(R>0), thử magnitude / risk-adjusted —
  nhưng phải qua walk-forward tương tự.

## Hướng tiếp theo

1. V0.1 đóng negative result: không đưa narrative nào vào production từ kết quả này.
2. Trước khi mở v0.2: chọn 1 hướng feature/regime conditioning, giữ nguyên
   protocol walk-forward, KHÔNG so sánh với OOS 2025-2026 làm bằng chứng chọn tham số.
