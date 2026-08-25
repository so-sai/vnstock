/**
 * layout_invariant.test.ts — Khóa finding #8: error 1 trang KHÔNG được
 * giết khung điều hướng (Route-level ErrorBoundary).
 *
 * Root cause: ErrorBoundary cấp app (App.tsx) làm fallback thay TOÀN BỘ
 * layout khi 1 trang throw -> mất sidebar/topbar (Playwright audit 24/08,
 * trang PaperTrading trắng toàn bộ).
 *
 * Fix đã commit (1641c16): RouteErrorBoundary wrap <Outlet/> trong
 * AppLayout, key=pathname. Test này khóa invariant ở mức source contract
 * (repo chưa có @testing-library — không test DOM, kiểm chứng cấu trúc).
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { resolve } from 'node:path';

const read = (p: string) =>
  readFileSync(resolve(__dirname, p), 'utf-8');

describe('Route-level error boundary invariant (finding #8)', () => {
  const appLayout = read('../components/AppLayout.tsx');

  it('AppLayout wrap <Outlet/> bằng ErrorBoundary', () => {
    expect(appLayout).toMatch(/ErrorBoundary/);
    expect(appLayout).toMatch(/<ErrorBoundary>\s*<Outlet\s*\/>\s*<\/ErrorBoundary>/);
  });

  it('boundary reset theo pathname (không giữ lỗi state cũ khi điều hướng)', () => {
    expect(appLayout).toMatch(/key=\{location\.pathname\}/);
    expect(appLayout).toMatch(/useLocation\(\)/);
  });

  it('App.tsx vẫn giữ app-level boundary làm last resort', () => {
    const app = read('../App.tsx');
    expect(app).toMatch(/<ErrorBoundary>\s*<BackendGate>/);
  });

  it('fallback của ErrorBoundary có nút "Thử lại" (recovery path)', () => {
    const boundary = read('../components/ErrorBoundary.tsx');
    expect(boundary).toMatch(/Thử lại/);
    expect(boundary).toMatch(/hasError: false/);
  });
});
