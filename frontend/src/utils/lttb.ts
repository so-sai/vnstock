import type { BarData } from 'lightweight-charts';

/**
 * LTTB chuẩn hóa — vận hành trực tiếp trên Float64Array (stride ≥ 5: t,o,h,l,c,...)
 * Zero unpack allocation, đồng bộ hệ tọa độ X theo Unix Timestamp.
 * Độ phức tạp O(N), không kích hoạt GC.
 *
 * @param buf    - Float64Array với stride field mỗi bar
 * @param count  - Số bar trong buf
 * @param target - Số điểm đầu ra
 * @param stride - Số field mỗi bar (mặc định 6: t,o,h,l,c,v)
 */
export function lttbTyped(
  buf: Float64Array,
  count: number,
  target: number,
  stride = 6,
): BarData[] {
  if (target >= count || target < 2) {
    const all: BarData[] = new Array(count);
    for (let i = 0; i < count; i++) {
      const off = i * stride;
      all[i] = {
        time: buf[off] as any,
        open: buf[off + 1],
        high: buf[off + 2],
        low: buf[off + 3],
        close: buf[off + 4],
      };
    }
    return all;
  }

  const result: BarData[] = new Array(target);

  // Nến đầu tiên
  result[0] = {
    time: buf[0] as any,
    open: buf[1], high: buf[2], low: buf[3], close: buf[4],
  };

  const bucketSize = (count - 2) / (target - 2);
  let aTime = buf[0];
  let aClose = buf[4];

  for (let i = 0; i < target - 2; i++) {
    const bucketStart = Math.floor(i * bucketSize) + 1;
    const bucketEnd = Math.floor((i + 1) * bucketSize) + 1;
    const nextStart = bucketEnd;
    const nextEnd = Math.min(Math.floor((i + 2) * bucketSize) + 1, count);

    // Trung bình bucket kế (điểm C) — dùng Unix Timestamp cho X
    let avgX = 0, avgY = 0, cnt = 0;
    for (let j = nextStart; j < nextEnd; j++, cnt++) {
      const off = j * stride;
      avgX += buf[off];       // Timestamp
      avgY += buf[off + 4];   // Close
    }
    if (cnt > 0) { avgX /= cnt; avgY /= cnt; }
    else {
      const off = Math.min(nextStart, count - 1) * stride;
      avgX = buf[off];
      avgY = buf[off + 4];
    }

    // Chọn nến trong bucket có diện tích tam giác lớn nhất
    let maxArea = -1;
    let bestIdx = bucketStart;

    for (let j = bucketStart; j < bucketEnd && j < count; j++) {
      const off = j * stride;
      const x = buf[off];
      const y = buf[off + 4];

      const area = Math.abs(
        (aTime - avgX) * (y - aClose) - (aTime - x) * (avgY - aClose),
      ) * 0.5;

      if (area > maxArea) {
        maxArea = area;
        bestIdx = j;
      }
    }

    const bestOff = bestIdx * stride;
    result[i + 1] = {
      time: buf[bestOff] as any,
      open: buf[bestOff + 1],
      high: buf[bestOff + 2],
      low: buf[bestOff + 3],
      close: buf[bestOff + 4],
    };

    aTime = buf[bestOff];
    aClose = buf[bestOff + 4];
  }

  // Nến cuối cùng
  const lastOff = (count - 1) * stride;
  result[target - 1] = {
    time: buf[lastOff] as any,
    open: buf[lastOff + 1],
    high: buf[lastOff + 2],
    low: buf[lastOff + 3],
    close: buf[lastOff + 4],
  };

  return result;
}

/**
 * 4-Point Candlestick Downsampler.
 * Kết hợp MinMax (giữ High peak + Low valley) và LTTB (giữ xu hướng Close)
 * trong mỗi bucket — output 3 điểm/bucket: High candle, Low candle, LTTB candle.
 * Luôn giữ nến đầu và cuối.
 * Độ phức tạp O(N), single pass.
 *
 * @returns BarData[] với tối đa 2 + (target-2)*3 điểm
 */
export function downsample4Point(
  buf: Float64Array,
  count: number,
  target: number,
  stride = 6,
): BarData[] {
  if (target >= count || target < 3) {
    return lttbTyped(buf, count, count, stride);
  }

  // Ước lượng capacity tối đa
  const result: BarData[] = [];
  const seen = new Set<number>();

  function push(idx: number) {
    if (seen.has(idx)) return;
    seen.add(idx);
    const off = idx * stride;
    result.push({
      time: buf[off] as any,
      open: buf[off + 1],
      high: buf[off + 2],
      low: buf[off + 3],
      close: buf[off + 4],
    });
  }

  push(0); // Nến đầu

  const bucketSize = (count - 2) / (target - 2);
  let aTime = buf[0];
  let aClose = buf[4];

  for (let i = 0; i < target - 2; i++) {
    const bucketStart = Math.floor(i * bucketSize) + 1;
    const bucketEnd = Math.min(Math.floor((i + 1) * bucketSize) + 1, count);
    const nextStart = bucketEnd;
    const nextEnd = Math.min(Math.floor((i + 2) * bucketSize) + 1, count);

    if (bucketStart >= bucketEnd) continue;

    // MinMax trong bucket
    let maxHigh = -Infinity, maxHighIdx = bucketStart;
    let minLow = Infinity, minLowIdx = bucketStart;
    let bestArea = -1, bestIdx = bucketStart;

    // Trung bình bucket kế cho LTTB
    let avgX = 0, avgY = 0, cnt = 0;
    for (let j = nextStart; j < nextEnd && j < count; j++, cnt++) {
      const off = j * stride;
      avgX += buf[off];
      avgY += buf[off + 4];
    }
    if (cnt > 0) { avgX /= cnt; avgY /= cnt; }
    else {
      const off = Math.min(nextStart, count - 1) * stride;
      avgX = buf[off];
      avgY = buf[off + 4];
    }

    for (let j = bucketStart; j < bucketEnd; j++) {
      const off = j * stride;
      const h = buf[off + 2];
      const l = buf[off + 3];
      const y = buf[off + 4];
      const x = buf[off];

      if (h > maxHigh) { maxHigh = h; maxHighIdx = j; }
      if (l < minLow)  { minLow = l;  minLowIdx = j; }

      const area = Math.abs(
        (aTime - avgX) * (y - aClose) - (aTime - x) * (avgY - aClose),
      ) * 0.5;
      if (area > bestArea) { bestArea = area; bestIdx = j; }
    }

    // Push theo thứ tự thời gian
    const points = [maxHighIdx, minLowIdx, bestIdx].sort((a, b) => a - b);
    for (const p of points) push(p);

    aTime = buf[bestIdx * stride];
    aClose = buf[bestIdx * stride + 4];
  }

  push(count - 1); // Nến cuối
  return result;
}
