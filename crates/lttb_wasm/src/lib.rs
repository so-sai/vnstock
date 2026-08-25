use wasm_bindgen::prelude::*;

/// LTTB — return selected indices (Uint32Array) for Float64Array buf with stride.
/// Mirrors `frontend/src/utils/lttb.ts:lttbTyped` exactly (area via close, X via timestamp).
#[wasm_bindgen]
pub fn lttb_indices(buf: &[f64], count: usize, target: usize, stride: usize) -> Vec<u32> {
    let stride = if stride == 0 { 6 } else { stride };
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
            let j = next_start.min(count - 1);
            let off = j * stride;
            avg_x = buf[off];
            avg_y = buf[off + 4];
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
        a_time = buf[best_off];
        a_close = buf[best_off + 4];
    }
    out.push((count - 1) as u32);
    out
}

/// 4-Point: High+Low+LTTB per bucket, deduped and time-ordered, always keep first/last.
/// Returns indices (Uint32Array) mirroring `downsample4Point`.
#[wasm_bindgen]
pub fn downsample4point_indices(buf: &[f64], count: usize, target: usize, stride: usize) -> Vec<u32> {
    let stride = if stride == 0 { 6 } else { stride };
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
            let j = next_start.min(count - 1);
            let off = j * stride;
            avg_x = buf[off];
            avg_y = buf[off + 4];
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
        a_time = buf[best_off];
        a_close = buf[best_off + 4];
    }
    push(count - 1, &mut seen, &mut out);
    // Ensure time-ordered (pts per bucket already sorted, but cross-bucket insertion keeps order;
    // seen dedup preserves first-seen order which is already monotonic because buckets increase)
    out
}
