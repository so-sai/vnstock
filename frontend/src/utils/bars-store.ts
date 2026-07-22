import type { BarData } from 'lightweight-charts';

/** Số field trên mỗi bar: time, open, high, low, close */
export const STRIDE = 5;

/**
 * Mã hóa mảng BarData[] thành Float64Array phẳng.
 * RAM ~ 5 × 8 bytes × N = 40MB / 1M bars
 */
export function encodeBars(bars: BarData[]): Float64Array {
  const n = bars.length;
  const buf = new Float64Array(n * STRIDE);
  for (let i = 0; i < n; i++) {
    const off = i * STRIDE;
    const b = bars[i];
    buf[off]     = b.time as number;
    buf[off + 1] = b.open;
    buf[off + 2] = b.high;
    buf[off + 3] = b.low;
    buf[off + 4] = b.close;
  }
  return buf;
}

/**
 * Giải mã một khoảng từ Float64Array thành BarData[].
 * @param buf  - Float64Array đầu vào
 * @param from - Index bắt đầu (theo bar, không phải byte)
 * @param to   - Index kết thúc (exclusive)
 */
export function decodeBars(buf: Float64Array, from: number, to: number): BarData[] {
  const result: BarData[] = [];
  for (let i = from; i < to; i++) {
    const off = i * STRIDE;
    result.push({
      time: buf[off] as any,
      open: buf[off + 1],
      high: buf[off + 2],
      low:  buf[off + 3],
      close: buf[off + 4],
    });
  }
  return result;
}

/**
 * Đếm số bar trong khoảng thời gian [fromTime, toTime].
 * Dùng binary search, O(log N).
 */
export function countInRange(buf: Float64Array, fromTime: number, toTime: number): number {
  const n = buf.length / STRIDE;
  let lo = 0, hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (buf[mid * STRIDE] < fromTime) lo = mid + 1;
    else hi = mid;
  }
  const start = lo;
  hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (buf[mid * STRIDE] < toTime) lo = mid + 1;
    else hi = mid;
  }
  return lo - start;
}

/**
 * Giải mã toàn bộ Float64Array thành BarData[] (dùng cho LTTB).
 */
export function decodeAll(buf: Float64Array): BarData[] {
  return decodeBars(buf, 0, buf.length / STRIDE);
}

/**
 * Chuyển đổi timestamp từ string "YYYY-MM-DD" sang number (Unix seconds).
 */
export function dateStrToTime(s: string): number {
  return new Date(s).getTime() / 1000;
}
