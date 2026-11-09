// [WHY] Toàn bộ crate là số học trên slice + Vec; không có FFI thủ công. Cấm `unsafe` để lỗi
// chỉ số/borrow bị chặn ở compile-time thay vì thành trap trong WASM của trình duyệt.
#![forbid(unsafe_code)]

use wasm_bindgen::prelude::*;

/// Guard dùng chung: buffer phải đủ `stride` để đọc offset 0 (x) và 4 (close).
///
/// [WHY] Trước 09/11/2026 `lttb_indices` đọc thẳng `buf[0]`/`buf[4]` và nhánh `else` đọc
/// `buf[off]`/`buf[off + 4]` mà không kiểm tra độ dài ⇒ panic index-out-of-bounds khi mảng
/// rỗng/ngắn (ví dụ mã mới niêm yết có 1 nến). Trả `false` để caller trả "giữ nguyên dữ liệu".
#[inline]
fn slice_has_min_bar(buf: &[f64], stride: usize) -> bool {
    stride >= 5 && buf.len() >= 5 && buf.len() >= stride
}

/// LTTB — return selected indices (Uint32Array) for Float64Array buf with stride.
/// Mirrors `frontend/src/utils/lttb.ts:lttbTyped` exactly (area via close, X via timestamp).
#[wasm_bindgen]
pub fn lttb_indices(buf: &[f64], count: usize, target: usize, stride: usize) -> Vec<u32> {
    let stride = if stride == 0 { 6 } else { stride };
    // [WHY] Buffer rỗng/ngắn hơn 1 bar (hoặc stride < 5) ⇒ trả danh sách RỖNG: "không có điểm
    // nào để vẽ" là sự thật. Trước 09/11/2026 hàm này panic index-OOB ở đúng nhánh này.
    if !slice_has_min_bar(buf, stride) {
        return vec![];
    }
    // [WHY] `count` đến từ metadata và CÓ THỂ lớn hơn dữ liệu thật (controller gửi lệch).
    // Cắt theo sức chứa thật để index trả về luôn hợp lệ với buffer; nếu không, consumer JS
    // nhận index ngoài mảng (undefined) và vẽ sai mà không ai biết.
    let count = count.min(buf.len() / stride);
    if target >= count || target < 2 {
        return (0..count as u32).collect();
    }
    if count == 0 {
        return vec![];
    }
    let mut out: Vec<u32> = Vec::with_capacity(target);
    out.push(0);
    let bucket_size = (count - 2) as f64 / (target - 2) as f64;
    let mut a_time = buf[0];
    let mut a_close = buf[4];
    for i in 0..(target - 2) {
        let bucket_start = (i as f64 * bucket_size).floor() as usize + 1;
        let bucket_end = (((i + 1) as f64 * bucket_size).floor() as usize + 1).min(count);
        let next_start = bucket_end;
        let next_end = (((i + 2) as f64 * bucket_size).floor() as usize + 1).min(count);
        // avg of next bucket (X=time, Y=close)
        let (mut avg_x, mut avg_y, mut cnt) = (0.0, 0.0, 0usize);
        for j in next_start..next_end {
            let off = j * stride;
            if off + 4 < buf.len() {
                avg_x += buf[off];
                avg_y += buf[off + 4];
                cnt += 1;
            }
        }
        if cnt > 0 {
            avg_x /= cnt as f64;
            avg_y /= cnt as f64;
        } else {
            let j = next_start.min(count.saturating_sub(1));
            let off = j * stride;
            // [WHY] Nhánh dự phòng này từng đọc `buf[off]`/`buf[off + 4]` KHÔNG kiểm tra độ
            // dài ⇒ panic khi `stride * count > buf.len()` (mảng lệch metadata). Thiếu dữ
            // liệu thì để avg = 0 và bỏ qua bucket, không được panic trong WASM.
            if off + 4 < buf.len() {
                avg_x = buf[off];
                avg_y = buf[off + 4];
            }
        }
        let mut max_area = -1.0;
        let mut best_idx = bucket_start;
        for j in bucket_start..bucket_end.min(count) {
            let off = j * stride;
            if off + 4 >= buf.len() {
                continue;
            }
            let x = buf[off];
            let y = buf[off + 4];
            let area = ((a_time - avg_x) * (y - a_close) - (a_time - x) * (avg_y - a_close)).abs() * 0.5;
            if area > max_area {
                max_area = area;
                best_idx = j;
            }
        }
        out.push(best_idx as u32);
        let best_off = best_idx * stride;
        // [WHY] `best_idx` có thể vẫn là `bucket_start` khi cả bucket bị bỏ qua vì buffer ngắn
        // hơn metadata (`stride*count > buf.len()`) ⇒ đọc thẳng `buf[best_off+4]` sẽ panic.
        // Không có bar thì giữ mốc neo cũ, không được crash toàn bộ đồ thị.
        if best_off + 4 < buf.len() {
            a_time = buf[best_off];
            a_close = buf[best_off + 4];
        }
    }
    out.push((count - 1) as u32);
    out
}

/// 4-Point: High+Low+LTTB per bucket, deduped and time-ordered, always keep first/last.
/// Returns indices (Uint32Array) mirroring `downsample4Point`.
#[wasm_bindgen]
pub fn downsample4point_indices(buf: &[f64], count: usize, target: usize, stride: usize) -> Vec<u32> {
    let stride = if stride == 0 { 6 } else { stride };
    // [WHY] Cùng lý do như `lttb_indices`: thiếu bar đầu thì không có "điểm neo" (a_time/a_close)
    // để tính diện tích ⇒ trả rỗng thay vì panic index-OOB.
    if !slice_has_min_bar(buf, stride) {
        return vec![];
    }
    // [WHY] Cùng lý do như `lttb_indices`: metadata `count` lệch phải được cắt theo buffer thật.
    let count = count.min(buf.len() / stride);
    if target >= count || target < 3 {
        return lttb_indices(buf, count, count, stride);
    }
    if count == 0 {
        return vec![];
    }
    let mut seen = std::collections::HashSet::with_capacity(target * 3);
    let mut out: Vec<u32> = Vec::with_capacity(2 + (target - 2) * 3);
    let push = |idx: usize, seen: &mut std::collections::HashSet<u32>, out: &mut Vec<u32>| {
        let u = idx as u32;
        if seen.insert(u) {
            out.push(u);
        }
    };
    push(0, &mut seen, &mut out);
    let bucket_size = (count - 2) as f64 / (target - 2) as f64;
    let mut a_time = buf[0];
    let mut a_close = buf[4];
    for i in 0..(target - 2) {
        let bucket_start = (i as f64 * bucket_size).floor() as usize + 1;
        let bucket_end = (((i + 1) as f64 * bucket_size).floor() as usize + 1).min(count);
        let next_start = bucket_end;
        let next_end = (((i + 2) as f64 * bucket_size).floor() as usize + 1).min(count);
        if bucket_start >= bucket_end {
            continue;
        }
        let mut max_high = f64::NEG_INFINITY;
        let mut max_high_idx = bucket_start;
        let mut min_low = f64::INFINITY;
        let mut min_low_idx = bucket_start;
        let mut best_area = -1.0;
        let mut best_idx = bucket_start;
        let (mut avg_x, mut avg_y, mut cnt) = (0.0, 0.0, 0usize);
        for j in next_start..next_end {
            let off = j * stride;
            if off + 4 < buf.len() {
                avg_x += buf[off];
                avg_y += buf[off + 4];
                cnt += 1;
            }
        }
        if cnt > 0 {
            avg_x /= cnt as f64;
            avg_y /= cnt as f64;
        } else {
            let j = next_start.min(count.saturating_sub(1));
            let off = j * stride;
            // [WHY] Nhánh dự phòng này từng đọc `buf[off]`/`buf[off + 4]` KHÔNG kiểm tra độ
            // dài ⇒ panic khi `stride * count > buf.len()` (mảng lệch metadata). Thiếu dữ
            // liệu thì để avg = 0 và bỏ qua bucket, không được panic trong WASM.
            if off + 4 < buf.len() {
                avg_x = buf[off];
                avg_y = buf[off + 4];
            }
        }
        for j in bucket_start..bucket_end {
            let off = j * stride;
            if off + 4 >= buf.len() {
                continue;
            }
            let h = buf[off + 2];
            let l = buf[off + 3];
            let y = buf[off + 4];
            let x = buf[off];
            if h > max_high {
                max_high = h;
                max_high_idx = j;
            }
            if l < min_low {
                min_low = l;
                min_low_idx = j;
            }
            let area = ((a_time - avg_x) * (y - a_close) - (a_time - x) * (avg_y - a_close)).abs() * 0.5;
            if area > best_area {
                best_area = area;
                best_idx = j;
            }
        }
        let mut pts = [max_high_idx, min_low_idx, best_idx];
        pts.sort_unstable();
        for p in pts {
            push(p, &mut seen, &mut out);
        }
        let best_off = best_idx * stride;
        // [WHY] `best_idx` có thể vẫn là `bucket_start` khi cả bucket bị bỏ qua vì buffer ngắn
        // hơn metadata (`stride*count > buf.len()`) ⇒ đọc thẳng `buf[best_off+4]` sẽ panic.
        // Không có bar thì giữ mốc neo cũ, không được crash toàn bộ đồ thị.
        if best_off + 4 < buf.len() {
            a_time = buf[best_off];
            a_close = buf[best_off + 4];
        }
    }
    push(count - 1, &mut seen, &mut out);
    // Ensure time-ordered (pts per bucket already sorted, but cross-bucket insertion keeps order;
    // seen dedup preserves first-seen order which is already monotonic because buckets increase)
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // Bar layout mirror `frontend/src/utils/lttb.ts`: [x(time), o, h, l, c, v] × count.
    fn make_buf(count: usize, stride: usize) -> Vec<f64> {
        (0..count * stride).map(|v| v as f64).collect()
    }

    /// [WHY] Ca thật "mã mới niêm yết / controller chưa có dữ liệu": buffer rỗng từng làm panic
    /// index-OOB trong WASM (trap cả trang đồ thị). Hợp đồng: không có bar ⇒ trả rỗng.
    #[test]
    fn empty_buffer_returns_empty_instead_of_panicking() {
        assert!(lttb_indices(&[], 0, 10, 6).is_empty());
        assert!(downsample4point_indices(&[], 0, 10, 6).is_empty());
    }

    /// [WHY] Buffer ngắn hơn 1 bar (< stride) ⇒ không đọc được close ở offset 4.
    #[test]
    fn buffer_shorter_than_one_bar_returns_empty() {
        assert!(lttb_indices(&[1.0, 2.0, 3.0], 1, 10, 6).is_empty());
        assert!(downsample4point_indices(&[1.0, 2.0, 3.0], 1, 10, 6).is_empty());
    }

    /// [WHY] `stride < 5` khiến offset 4 (close) nằm ngoài một bar ⇒ dữ liệu vô nghĩa,
    /// phải bị từ chối thay vì đọc lấn sang bar kế tiếp.
    #[test]
    fn stride_below_five_is_rejected() {
        let buf = make_buf(10, 6);
        assert!(lttb_indices(&buf, 10, 5, 4).is_empty());
    }

    /// [WHY] Chống vá quá tay: guard mới KHÔNG được đổi hành vi của dữ liệu hợp lệ —
    /// vẫn giữ điểm đầu/cuối và mọi index phải nằm trong buffer.
    #[test]
    fn valid_buffer_keeps_first_last_and_indices_in_range() {
        let (count, stride) = (100usize, 6usize);
        let buf = make_buf(count, stride);
        for idx in [
            lttb_indices(&buf, count, 20, stride),
            downsample4point_indices(&buf, count, 20, stride),
        ] {
            assert_eq!(idx.first().copied(), Some(0));
            assert_eq!(idx.last().copied(), Some((count - 1) as u32));
            assert!(idx.iter().all(|&i| (i as usize) < count));
            assert!(idx.len() >= 2);
        }
    }

    /// [WHY] Metadata nói 100 bar nhưng buffer chỉ có 5 bar (lệch giữa controller và dữ liệu):
    /// trước đây index trả về vượt mảng ⇒ JS nhận `undefined`. Nay phải cắt theo buffer thật và
    /// tuyệt đối không panic.
    #[test]
    fn count_larger_than_buffer_is_clamped_not_panicking() {
        let (stride, real) = (6usize, 5usize);
        let buf = make_buf(real, stride);
        for idx in [
            lttb_indices(&buf, 100, 20, stride),
            downsample4point_indices(&buf, 100, 20, stride),
        ] {
            assert!(!idx.is_empty());
            assert!(idx.iter().all(|&i| (i as usize) < real), "{idx:?}");
            assert_eq!(idx.last().copied(), Some((real - 1) as u32));
        }
    }
}
