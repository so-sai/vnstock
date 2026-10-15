"""Regression test — Ca 2: thiếu dữ liệu breadth KHÔNG được coi là 0 % (thảm hoạ bịa ra).

[WHY - BỐI CẢNH & TÁC HẠI]
    `backtest_engine.py` dòng 382 (bản cũ):
        current_breadth = market_breadth_df.get(yesterday, 0)
        if current_breadth < 0.15:
            target_nav_ratio = 0.0
    `market_breadth_df` là pandas **Series** (dựng ở dòng 212 từ `stocks_above_ma50 /
    total_market_stocks`). Thiếu một bản ghi ngày hôm qua — chuyện xảy ra bình thường khi
    cron EOD lỗi hoặc vướng ngày lễ — thì `.get(..., 0)` trả **0**, và `0 < 0.15` đúng
    ⇒ `target_nav_ratio = 0.0`
    ⇒ **backtest thanh lý toàn bộ danh mục vì một thảm hoạ chưa từng xảy ra**.
    Sai lệch không hề im lặng: nó đổi hẳn quyết định giải ngân của phiên đó.

[ROOT CAUSE - NGUYÊN NHÂN GỐC]
    Chọn giá trị mặc định "lành" (0) cho một đại lượng mà, trong ngữ cảnh độ rộng thị
    trường, **0 lại là giá trị cực đoan tồi tệ nhất** (0 % số mã tăng). Cùng lớp lỗi đã
    thổi phồng OOS 2026 (`regime.get(T, "RANGING")`) và làm `inf` ở `breakout_continuation`.

[INVARIANT - BẤT BIẾN BẢO VỆ]
    Missing Data ≠ 0 % Breadth. Thiếu dữ liệu ⇒ BỎ QUA bộ lọc (giữ nguyên exposure),
    tuyệt đối không tự hạ exposure và không ghi 0 % vào hồ sơ.

GHI CHÚ VỀ PHƯƠNG PHÁP (tự phê bình để không viết test hình thức):
    Đây là **test hợp đồng nguồn** (source-contract), KHÔNG phải test hành vi runtime.
    Bộ lọc breadth nằm sâu trong vòng lặp mô phỏng của `run()` nên chưa tách ra hàm riêng
    để gọi trực tiếp. Test này sẽ ĐỎ nếu ai khôi phục lại `.get(yesterday, 0)` — nhưng nó
    KHÔNG chứng minh được hành vi khi breadth thiếu.
    ⇒ Follow-up bắt buộc (Ca 2c): tách quyết định breadth thành hàm thuần
      `_apply_breadth_filter(target_nav_ratio, breadth) -> float` và test trực tiếp.
      (Cùng loại nợ như test Ca 2 dự thảo từng mắc: kiểm tra `dict.get` của Python chứ
       không kiểm tra code của repo.)
"""

from __future__ import annotations

import inspect

import src.engine.backtest_engine as be  # type: ignore[import-not-found]


def _source() -> str:
    return inspect.getsource(be)


def test_missing_breadth_is_not_synthesized_as_zero():
    """ĐIỀU KIỆN CHÍNH: không còn PHÉP GÁN `.get(yesterday, 0)` trong module.

    Bám dạng `= market_breadth_df.get(yesterday, 0)` (có dấu gán) chứ không bám chuỗi trần:
    chính comment giải thích lỗi cũng chứa chuỗi đó ⇒ test sẽ tự khớp với lời chú thích của
    nó và báo đỏ sai. Đây là điểm yếu cố hữu của test hợp đồng nguồn.
    """
    src = _source()
    assert "= market_breadth_df.get(yesterday, 0)" not in src, (
        "HỒI QUY CA 2: breadth thiếu lại bị ép về 0 ⇒ `0 < 0.15` ⇒ thanh lý toàn bộ danh mục "
        "vì một thảm hoạ BỊA RA. Dùng `.get(yesterday, None)` + bỏ qua bộ lọc."
    )
    assert "= market_breadth_df.get(yesterday, None)" in src, (
        "Ca 2: thiếu breadth phải trả None (không biết), KHÔNG phải 0 (đã sập)."
    )


def test_shadow_log_does_not_record_fabricated_zero_breadth():
    """0 % chỉ được ghi khi breadth THẬT SỰ bằng 0, không phải khi dữ liệu vắng mặt."""
    src = _source()
    assert "float(current_breadth * 100) if current_breadth is not None else None" in src, (
        "Hồ sơ kiểm toán đang ghi 0 % cho phiên không có dữ liệu ⇒ bằng chứng giả."
    )
