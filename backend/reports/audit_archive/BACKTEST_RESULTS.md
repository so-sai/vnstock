# Audit archive — kết quả backtest (2026-10-14)

## Vì sao có hai thư mục

`backend/reports/*` bị gitignore; chỉ `audit_archive/` được commit. Nên hai bộ kết quả dưới
đây được sao vào đây để **có vết vĩnh viễn trên git**, không phải để "lưu cho đẹp".

| Thư mục | Trạng thái | Vì sao giữ |
|---|---|---|
| `backtest_pre_pit_fix/` | ❌ **KHÔNG HỢP LỆ** | Chạy trước khi vá look-ahead. Giữ làm **bằng chứng kiểm toán** cho lỗi đã xảy ra và cho quy mô sai lệch của nó. Xoá đi là xoá bằng chứng. |
| `backtest_pit_clean/` | ✅ **HỢP LỆ** | Chạy sau khi vá PIT (commit `89319c5`). Đây là bộ số dùng để ra quyết định. |
| `backtest_pit_clean_regime_complete/` | ✅ **HỢP LỆ — bản CHÍNH THỨC** | Chạy sau khi nạp bù **4 phiên regime khuyết** (2025-04-03/08/09/10) dưới guard fail-closed. Guard không raise ⇒ dữ liệu đầy đủ. **Thay thế thế hệ trước.** |

## Ba thế hệ kết quả — vì sao giữ cả ba

| Thế hệ | Lỗi | Baseline V4 | V2 | V5 |
|---|---|---|---|---|
| (0) `pre_pit_fix` | ❌ look-ahead 100 % số ngày | −0,2504 · 28 lệnh | +0,4446 | +0,0634 |
| (1) `backtest_pit_clean` | 🟠 PIT đúng, còn **4 phiên regime khuyết** | −0,2542 · 39 lệnh | +0,4378 | +0,4058 |
| (2) `backtest_pit_clean_regime_complete` | ✅ **đầy đủ** | **−0,2580 · 38 lệnh** | **+0,4365** · 172 | **+0,4044** · 157 |

Bước nhảy của phép sửa **4 phiên regime** chỉ **0,001–0,004 Sharpe** (0,3 % số phiên) ⇒ dữ liệu thiếu là nhỏ nhưng **vẫn phải khai báo**, và R1 **không** bị nới để hợp thức hoá bất cứ thế hệ nào.

**Phán quyết không đổi qua cả ba thế hệ:** baseline **FAIL** (−0,258 so với ngưỡng 0,50) · tác nhân **organ âm** (−0,506) · **regime dương** (+0,349) · tương tác **âm** · R7 = **A**.

## Kết quả OOS 2026 (cửa sổ riêng, chạm một lần + một lần sửa — xem spec OOS §8)

| | C1 (momentum+regime+Health≠DISTRESSED) | C2 (bỏ health) |
|---|---|---|
| Sharpe | **+0,8956** / +0,8188 | −0,6169 / −0,7012 |
| MaxDD | 3,44 % / 3,52 % | 3,49 % / 3,58 % |
| Lệnh | 35 | 39 |
| Kết luận | **ĐẠT** cả hai kịch bản | trượt |

⇒ **Nhánh 2** ⇒ paper-trade C1 (giấy, **không tiền thật**).

## Lỗi đã sửa và quy mô

`company_health_v2._quarter_at(date)` trả về **quý CHỨA ngày đó**; `analyze()` chọn
`period <= quý đó`. Ở VN BCTC quý chỉ công bố sau khi quý kết thúc (hạn 30 ngày), nên một
phiên ngày 15/02 dùng báo cáo Q1 (công bố ~tháng 4) = **nhìn trước ~2 tháng**.

Đo được: **1826/1826 ngày** (100%) trong 2021–2025 dùng quý KHÁC cách tính đúng ⇒ lỗi phủ
**toàn bộ cửa sổ**, không phải vài chỗ.

Hệ quả cụ thể (trước → sau khi sửa):

| Ô | Sharpe nhiễm | Sharpe PIT-sạch | Ghi chú |
|---|---|---|---|
| V4 baseline | −0,2504 | **−0,2542** | FAIL giữ nguyên; số lệnh 28 → **39** (hết "không đủ lực") |
| V2 momentum+regime | +0,4446 | **+0,4378** | Ổn định: dịch chỉ 0,0068 khi 100% số ngày đổi dữ liệu |
| V5 distressed-only | +0,0634 | **+0,4058** | **LẬT**: R7 đảo từ B (hại) sang **A (gần như miễn phí)** |
| organ (tác nhân chính) | −0,5283 | **−0,5052** | Kết luận vững |
| regime (tác nhân chính) | +0,3797 | **+0,3511** | Kết luận vững |

## Bài học đã ghi vào quy trình

1. **Nhãn chất lượng tài chính phải PIT theo hạn công bố**, không theo quý chứa ngày — nếu
   `health_ratios` không có cột ngày công bố thì phải dùng luật lag bảo thủ (Q1–Q3 ≥ 60 ngày,
   Q4 ≥ 120 ngày), và ghi rõ rằng đó là **xấp xỉ** vì thiếu dữ liệu gốc.
2. **Một tham số chưa có số đo riêng thì đừng kết luận về nó.** "Chặn DISTRESSED bóp nghẹt cơ
   hội" từng được đọc ra từ dữ liệu nhiễm với Δ = −0,38; sau khi sạch, Δ = −0,032.
3. **Giữ kết quả cũ lại khi tuyên bố nó sai** — để lần sau còn đo được quy mô sai lệch.
