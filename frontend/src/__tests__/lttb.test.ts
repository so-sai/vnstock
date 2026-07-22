import { describe, it, expect } from 'vitest';
import { lttbTyped, downsample4Point } from '../utils/lttb';

/** Helper: tạo Float64Array từ mảng [time, open, high, low, close][] */
function makeBuf(data: [number, number, number, number, number][]): Float64Array {
  const buf = new Float64Array(data.length * 6); // stride=6
  for (let i = 0; i < data.length; i++) {
    const off = i * 6;
    buf[off] = data[i][0];     // time
    buf[off + 1] = data[i][1]; // open
    buf[off + 2] = data[i][2]; // high
    buf[off + 3] = data[i][3]; // low
    buf[off + 4] = data[i][4]; // close
    buf[off + 5] = 0;         // volume
  }
  return buf;
}

const DAY = 86400;

describe('lttbTyped', () => {
  it('trả về đúng target size', () => {
    const buf = makeBuf(Array.from({ length: 100 }, (_, i) => [
      i * DAY, 100 + Math.random() * 10, 105 + Math.random() * 5,
      95 + Math.random() * 5, 100 + Math.random() * 10,
    ] as [number, number, number, number, number]));
    const result = lttbTyped(buf, 100, 10, 6);
    expect(result).toHaveLength(10);
  });

  it('giữ nến đầu và cuối', () => {
    const buf = makeBuf([
      [DAY, 100, 105, 95, 102],
      [2 * DAY, 102, 108, 98, 105],
      [3 * DAY, 105, 110, 100, 103],
      [4 * DAY, 103, 107, 97, 101],
      [5 * DAY, 101, 106, 96, 104],
    ]);
    const result = lttbTyped(buf, 5, 3, 6);
    expect(result[0].time).toBe(DAY);
    expect(result[result.length - 1].time).toBe(5 * DAY);
  });

  it('dùng Unix Timestamp cho X (không dùng array index)', () => {
    // Tạo dữ liệu với timestamps không đều
    const buf = makeBuf([
      [100, 50, 55, 45, 52],
      [200, 52, 58, 48, 55],
      [400, 55, 60, 50, 53],
      [800, 53, 57, 47, 51],
    ]);
    const result = lttbTyped(buf, 4, 2, 6);
    // Kiểm tra X là timestamp, không phải index
    expect(result[0].time).toBe(100);
    expect(result[1].time).toBe(800);
    // Nếu nhầm index sẽ ra 0 và 3
  });

  it('edge: target >= count trả về toàn bộ', () => {
    const buf = makeBuf([[DAY, 100, 105, 95, 102]]);
    const result = lttbTyped(buf, 1, 5, 6);
    expect(result).toHaveLength(1);
    expect(result[0].time).toBe(DAY);
  });

  it('edge: count = 0 trả về rỗng', () => {
    const buf = new Float64Array(0);
    const result = lttbTyped(buf, 0, 5, 6);
    expect(result).toHaveLength(0);
  });

  it('chạy 100k bars dưới 10ms (không unpack JS objects)', () => {
    const n = 100_000;
    const buf = makeBuf(Array.from({ length: n }, (_, i) => [
      i * DAY, 100 + Math.random() * 10, 105 + Math.random() * 5,
      95 + Math.random() * 5, 100 + Math.random() * 10,
    ] as [number, number, number, number, number]));

    const start = performance.now();
    const result = lttbTyped(buf, n, 2000, 6);
    const elapsed = performance.now() - start;

    expect(result).toHaveLength(2000);
    expect(elapsed).toBeLessThan(100); // ngưỡng an toàn, thực tế <10ms
  });
});

describe('downsample4Point', () => {
  it('output chứa cả High peak và Low valley', () => {
    // Tạo dữ liệu với 1 peak rõ + 1 valley rõ
    const buf = makeBuf([
      [DAY, 100, 102, 98, 101],
      [2 * DAY, 101, 105, 99, 103],
      // peak:
      [3 * DAY, 103, 150, 101, 105],
      [4 * DAY, 105, 108, 102, 106],
      // valley:
      [5 * DAY, 106, 107, 50, 104],
      [6 * DAY, 104, 106, 96, 102],
      [7 * DAY, 102, 104, 96, 100],
    ]);
    const result = downsample4Point(buf, 7, 4, 6);

    // Nến High peak (150) phải được giữ
    const highs = result.map(r => r.high);
    expect(Math.max(...highs)).toBe(150);

    // Nến Low valley (50) phải được giữ
    const lows = result.map(r => r.low);
    expect(Math.min(...lows)).toBe(50);
  });

  it('giữ nến đầu và cuối', () => {
    const buf = makeBuf(Array.from({ length: 20 }, (_, i) => [
      (i + 1) * DAY, 100, 105, 95, 102,
    ] as [number, number, number, number, number]));
    const result = downsample4Point(buf, 20, 5, 6);
    expect(result[0].time).toBe(DAY);
    expect(result[result.length - 1].time).toBe(20 * DAY);
  });

  it('edge: target >= count trả về toàn bộ', () => {
    const buf = makeBuf([
      [DAY, 100, 105, 95, 102],
      [2 * DAY, 102, 108, 98, 105],
    ]);
    const result = downsample4Point(buf, 2, 10, 6);
    expect(result).toHaveLength(2);
  });
});
