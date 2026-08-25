import { describe, it, expect } from 'vitest';
import { lttbTyped, downsample4Point } from './lttb';

// WASM parity — load nodejs build (Vitest runs in Node)
import * as wasm from '../../../crates/lttb_wasm/pkg-node/lttb_wasm.js';

function makeBuf(count: number, seed = 0): Float64Array {
  const buf = new Float64Array(count * 6);
  let s = seed;
  const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 0xffffffff; };
  for (let i = 0; i < count; i++) {
    const off = i * 6;
    const t = 1700000000 + i * 86400;
    const base = 10 + rnd() * 20;
    buf[off] = t;
    buf[off + 1] = base;
    buf[off + 2] = base + rnd() * 2;
    buf[off + 3] = base - rnd() * 2;
    buf[off + 4] = base + (rnd() - 0.5);
    buf[off + 5] = rnd() * 1e6;
  }
  return buf;
}

describe('LTTB WASM parity', () => {
  it('lttbTyped indices match WASM (10k bars → 500)', () => {
    const count = 10000; const target = 500;
    const buf = makeBuf(count, 42);
    const js = lttbTyped(buf, count, target, 6);
    const idx = wasm.lttb_indices(buf, count, target, 6);
    expect(idx.length).toBe(target);
    // Reconstruct JS indices from BarData time map for comparison
    // Simpler: compare length + first/last + WASM indices reconstruct same BarData
    const wasmBars = Array.from(idx as unknown as number[]).map(i => {
      const off = i * 6;
      return { time: buf[off], close: buf[off + 4] };
    });
    expect(wasmBars.length).toBe(js.length);
    expect(wasmBars[0].time).toBe(js[0].time);
    expect(wasmBars[wasmBars.length - 1].time).toBe(js[js.length - 1].time);
    // Full index parity (WASM vs JS bucket choice)
    const jsIdx = js.map(b => {
      for (let i = 0; i < count; i++) if (buf[i * 6] === (b.time as unknown as number)) return i;
      return -1;
    });
    expect(Array.from(idx as unknown as number[])).toEqual(jsIdx);
  });

  it('downsample4Point WASM vs JS length parity', () => {
    const count = 1000; const target = 100;
    const buf = makeBuf(count, 7);
    const js = downsample4Point(buf, count, target, 6);
    const idx = wasm.downsample4point_indices(buf, count, target, 6);
    // JS deduplicates via Set, WASM does same — sizes should match
    expect(idx.length).toBe(js.length);
    expect(idx[0]).toBe(0);
    expect(idx[idx.length - 1]).toBe(count - 1);
  });

  it('bench WASM <1ms for 10k bars', () => {
    const buf = makeBuf(10000, 99);
    const t0 = performance.now();
    for (let i = 0; i < 100; i++) wasm.lttb_indices(buf, 10000, 500, 6);
    const avg = (performance.now() - t0) / 100;
    expect(avg).toBeLessThan(1.5); // <1.5ms per 10k→500 (target <1ms, allow CI jitter)
  });
});
