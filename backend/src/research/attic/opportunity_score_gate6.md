# Gate 6: Governor Integration — Oracle Top-K vs Budget/Execution Machinery

> **Ngày đóng:** 2026-08-15 | **Trạng thái:** GATE 6 = **PASS** — alpha sống qua machinery
> **Script:** `backend/src/research/opportunity_score_gate6.py` (READ-ONLY)
> **Output:** `backend/data/reports/opportunity_score_gate6_audit_all.csv`

## Verdict (1 câu)

**Selection alpha của M_value sống sót qua Execution/Budget machinery của
Governor: A (oracle) và B (machinery thật, cap=1) CÙNG dương net r20 sau cost
0.45% round-trip → Feature→Selection→Governor→Realized Alpha có bằng chứng;
chênh lệch B−A nằm trong chi phí budget/execution, không phải feature.**

```
Gate 1 (p_gain FAIL) → Gate 2 (valuation PASS) → Gate 3A (M_value PASS)
→ Gate 4 (cluster KHÔNG incremental) → Gate 5 (selection alpha PASS)
→ Gate 6 = PASS
        └── A oracle k=5  : net r20 +1.77%, MaxDD −35.8%
        └── B machinery cap=1 (config thật): net r20 +1.31%, MaxDD −14.2%
        └── Δ B−A = −0.46pp → chi phí budget/execution, alpha không chết
        └── LƯU Ý: budget=20/năm là CEILING — B tiêu hết 20 slot ~ngày 38
```

## Thiết kế (vì sao lại thế này)

**Vấn đề dữ liệu:** replay_58_ts chỉ có OPEN/SCALE_IN deployment candidates ở
**2022** (p_gain < floor 0.55 → 2023-25 không tạo được candidate). Nếu Branch B
dùng ledger deployment rows thì 2023-25 "không có gì để chọn" → so sánh A-vs-B
vô nghĩa. Vì vậy:

- **Branch A (Oracle)**: mỗi ngày rank toàn bộ cross-section theo M_value, mua
  top-K (K = 1/3/5/10), giữ H=20 phiên. Không budget/cap/dedup. Đây là *ceiling*
  của tín hiệu khi giao dịch thật (có turnover, cost, MaxDD, concentration thật).
- **Branch B (Governor machinery)**: cùng tín hiệu M_value nhưng candidate pool =
  toàn bộ cross-section có M_value (synthesize, vì p_gain-gated candidate không
  đủ), rồi chạy **đúng semantics** `selection_layer.rank_day`/`select_year`:
  budget = 20 slot/năm (ceiling), daily_cap ∈ {1,3,5,10}, dedup 1 symbol/năm.
  cap=1 là **config thật** (DEFAULT_DAILY_CAP); cap 3/5/10 để test daily_cap
  có giết alpha không. p_gain hoàn toàn loại khỏi selection.

## 1. Kết quả chính (H20, cost 0.45% round-trip)

| branch | config | n_trades | net r20% | WinRate% | PF | NAV% | MaxDD% | IS(22-24) | OOS25* |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A oracle | K=1 | 51 | **+2.93** | 58.8 | 2.31 | +214.6 | −39.7 | +2.53 | +4.08 |
| A oracle | K=3 | 151 | +2.47 | 55.6 | 1.96 | +144.0 | −35.2 | +1.64 | +5.04 |
| A oracle | K=5 | 252 | +1.77 | 51.6 | 1.67 | +84.7 | −35.8 | +1.27 | +3.26 |
| A oracle | K=10 | 500 | +1.62 | 52.0 | 1.52 | +47.4 | −34.0 | +1.15 | +3.00 |
| **B governor** | **cap=1** (config thật) | 80 | **+1.31** | **61.3** | 1.60 | +5.6 | **−14.2** | +0.50 | +3.75 |
| B governor | cap=3 | 80 | +0.93 | 56.2 | 1.47 | −6.6 | −11.4 | +0.97 | +0.79 |
| B governor | cap=5 | 80 | +0.88 | 57.5 | 1.41 | −7.4 | −10.1 | +1.14 | +0.09 |
| B governor | cap=10 | 80 | +1.30 | 53.8 | 1.62 | −11.5 | −13.3 | +1.91 | −0.54 |

*OOS 2025 = descriptive (ĐÃ NHIỄM).

- **A oracle**: top-K nhỏ → per-trade alpha cao nhất (K=1 +2.93%) nhưng MaxDD lớn
  (−35~−40%) và concentration cao (HHI 0.97). K tăng → pha loãng alpha
  (+2.93→+1.62) nhưng NAV vẫn dương. Đây là *ceiling*, không phải strategy.
- **B machinery cap=1** (config thật): net r20 **+1.31%** — dương, WinRate cao
  nhất (61.3%), MaxDD tốt nhất (−14.2%). **Alpha KHÔNG chết qua budget/execution.**
- **Daily_cap tăng KHÔNG giúp**: cap=3/5/10 có per-trade alpha thấp hơn cap=1
  (+0.88~+1.30) và NAV ÂM. cap=1 (config thật) là tốt nhất → không có lý do
  đổi daily_cap, đây không phải biến tối ưu alpha.

## 2. Phát hiện cấu trúc: budget=20 là CEILING, không phải quota

Branch B tiêu hết **20 slot trong ~ngày 38** của mỗi năm (first/last entry doy:
4/38, 3/37, 2/29, 2/36). Hệ quả:

- Governor v1 chỉ cho tối đa 20 lần triển khai vốn mới/năm, và M_value luôn có
  candidate hàng ngày → budget cạn sớm, phần còn lại của năm không có EXECUTE.
- Điều này khiến NAV của B (+5.6%) thấp hơn nhiều so với A (+84.7%) dù per-trade
  alpha chỉ thấp hơn ~0.46pp — vì B chỉ "được" 20 vị thế/năm thay vì liên tục.
- **Đặc tính này là cấu trúc của policy v1 (budget ceiling), KHÔNG phải bug của
  M_value.** Khi deploy thật cần xem lại: budget 20/năm có phù hợp kỳ vọng
  deployment (số vị thế/năm) không — nhưng KHÔNG dùng kết quả này để tăng budget
  nhằm "làm đẹp" alpha (contamination guardrail).

## 3. IS vs OOS, regime, overlap

- **IS(2022-24) → OOS(2025)**: A dương cả hai (IS +1.27~+2.53, OOS +3.00~+5.04);
  B cap=1 IS +0.50 / OOS +3.75 — OOS cao hơn IS, nhất quán với M_value mạnh dần
  theo năm (Gate 3A/4/5 đều thấy 2025 mạnh nhất).
- **Regime (B cap=1)**: CRISIS +2.41%, RANGING +1.39%, TRENDING +0.21% — mạnh nhất
  khi thị trường ép (CRISIS/RANGING), yếu nhất TRENDING (nhất quán Gate 4).
- **Overlap H20→H60/H120 (B cap=1)**: ρ(H20,H60)=0.217, ρ(H20,H120)=0.251,
  same-sign 70% → alpha H20 có lan sang H60/H120 yếu-trung bình, không chỉ là
  noise 20 phiên. Gate 5 cảnh báo overlap H60/H120 — đã kiểm tra, đây là độ bền
  alpha chứ không phải artifact trùng lặp hoàn toàn (ρ ~0.2-0.25, không ~1).

## Đọc kết quả

1. **A-vs-B cho bằng chứng tích cực**: A tốt VÀ B tốt → Feature→Selection→
   Governor→Realized Alpha có bằng chứng; chênh lệch B−A = chi phí
   budget/execution, không phải feature thất bại.
2. **cap=1 (config thật) là tốt nhất**: không đổi daily_cap; budget vẫn là
   constraint vật lý, không phải biến tối ưu.
3. **p_gain xác nhận fail cuối cùng**: không xuất hiện ở bất kỳ nhánh nào — đã
   loại hẳn khỏi selection từ Gate 5.
4. **Budget ceiling là giới hạn thật của policy v1**: 20 slot/năm → deployment
   thực tế sẽ thưa. Đây là quyết định policy, cần tách khỏi câu hỏi "alpha có
   thật không" (đã trả lời: có).

## Guardrail

- **B là replication machinery (synthesized candidates)**, CHƯA phải replay thật
  qua `company_state.py`/`decision_budget.py`/Evidence Ledger. Muốn bằng chứng
  end-to-end cần replay Governor thật với M_value làm rank function.
- **Budget = 20/năm luôn là constraint**; K/cap sensitivity để đo độ nhạy, KHÔNG
  chọn K hoặc cap vì số đẹp (K=3 đẹp nhất ở Gate 5 nhưng ở đây cap=1 thật).
- **OOS 2025 descriptive** — không chọn K/cost/threshold từ OOS.
- **Cost 0.45% round-trip** (multi_factor_fusion.TRANSACTION_COST); kết quả chưa
  mô hình slippage phụ thuộc ADV/kích thước lệnh.
- KHÔNG dùng +147% Selection v0 (checkpoint 147afdd) làm benchmark.
- MaxDD của A (−35~−40%) là của oracle không giới hạn — KHÔNG phải khuyến nghị
  giữ K nhỏ; đây là ceiling để so sánh, không phải strategy.

## Hướng tiếp theo

1. **Replay Governor thật (production integration)**: thay p_gain bằng M_value
   trong Selection Layer → chạy replay qua Governor DB để có Evidence Ledger thật
   (decision_ledger có sẵn return_pct/opportunity_cost để kiểm chứng).
2. Kiểm tra **capacity/ADV**: position size ≤10-15% ADV_20D với 20 slot/năm —
   liệu budget 20 có đủ nhỏ để không chạm capacity không.
3. Đánh giá **policy budget**: 20/năm có phù hợp kỳ vọng product không (tách khỏi
   việc tối ưu alpha).
