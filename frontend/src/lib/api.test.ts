/**
 * api.test.ts — Khóa lỗi raw JSON leak tại fetchJson (Patch B, finding #5).
 *
 * Root cause đã phát hiện qua Playwright audit: lib/api.ts throw
 * `API Error 429: {"error":"RATE_LIMITED","message":"..."}` — dump nguyên
 * envelope ra UI (ScreenerPage hiển thị raw JSON cho end-user).
 *
 * Invariant: error message đến UI PHẢI là chuỗi thân thiện từ trường
 * "message" của backend envelope; TUYỆT ĐỐI không chứa raw JSON.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

const okResponse = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200 });

const errResponse = (status: number, body: string | unknown) =>
  new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    statusText: status === 429 ? 'Too Many Requests' : 'Service Unavailable',
  });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchJson error formatting (no raw JSON leak)', () => {
  it('429 envelope -> message thân thiện, không chứa raw JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        errResponse(429, {
          error: 'RATE_LIMITED',
          message: 'Hệ thống đang bảo vệ IP — quá nhiều request. Đợi 60 giây.',
        }),
      ),
    );

    await expect(api.getScreener(50)).rejects.toThrow(
      'Hệ thống đang bảo vệ IP — quá nhiều request. Đợi 60 giây.',
    );
  });

  it('error message KHÔNG bao giờ chứa chuỗi JSON của envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        errResponse(503, {
          error: 'OFFLINE',
          message: 'Hệ thống đang ở chế độ Offline.',
        }),
      ),
    );

    try {
      await api.getScreener(50);
      expect.unreachable('phải throw');
    } catch (e) {
      const msg = (e as Error).message;
      expect(msg).toBe('Hệ thống đang ở chế độ Offline.');
      expect(msg).not.toMatch(/\{"/);
      expect(msg).not.toContain('"error"');
      expect(msg).not.toContain('API Error');
    }
  });

  it('body KHÔNG phải JSON -> fallback statusText, không crash', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(errResponse(502, '<html>Bad Gateway</html>')),
    );

    await expect(api.getScreener(50)).rejects.toThrow('Service Unavailable');
  });

  it('body rỗng -> fallback statusText (không crash, không raw JSON)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(errResponse(500, '')));

    await expect(api.getScreener(50)).rejects.toThrow('Service Unavailable');
  });

  it('200 -> trả JSON bình thường (không đụng error path)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(okResponse([{ symbol: 'VCB' }])),
    );

    const data = await api.getScreener(50);
    expect(data).toEqual([{ symbol: 'VCB' }]);
  });
});
