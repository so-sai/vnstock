/**
 * capture_ui.mjs — UI Visual Audit Harness (Playwright + Node).
 *
 * Mục đích: chụp toàn bộ routes của frontend để audit UI/UX
 * (overflow, contrast, layout, white-screen) bằng multimodal vision.
 *
 * Usage:
 *   cd frontend && node scripts/capture_ui.mjs
 *
 * Env:
 *   BASE_URL — mặc định http://localhost:5173 (Vite dev, proxy /api -> 17039)
 *   OUT_DIR  — mặc định frontend/ui_audit
 *
 * WHY node, KHÔNG bun: bun 1.3.14 (Windows) treo khi spawn chromium
 * (launch timeout cả headless-shell lẫn msedge channel); node 24 chạy OK.
 *
 * Yêu cầu: backend đang chạy (python ptck.py serve) + Vite dev server.
 * Theme: app chỉ có MỘT theme Japandi light (index.css @theme — không có
 * dark mode/toggle). Script chụp theme thực tế tồn tại.
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE_URL = process.env.BASE_URL ?? "http://localhost:5173";
const OUT_DIR = resolve(process.env.OUT_DIR ?? join(HERE, "ui_audit"));

// Routes từ App.tsx (react-router-dom). "/" và "/nhip-dap-vi-mo" cùng
// MacroDashboard nhưng giữ nguyên như router khai báo.
const ROUTES = [
  { path: "/", slug: "home", name: "MacroDashboard(index)" },
  { path: "/nhip-dap-vi-mo", slug: "nhip-dap-vi-mo", name: "MacroDashboard" },
  { path: "/bo-loc-kim-cuong", slug: "bo-loc-kim-cuong", name: "ScreenerPage" },
  { path: "/mo-hinh-alpha", slug: "mo-hinh-alpha", name: "AlphaModelsPage" },
  { path: "/kiem-chung-lich-su", slug: "kiem-chung-lich-su", name: "TimeKernelPage" },
  { path: "/so-tay-danh-muc", slug: "so-tay-danh-muc", name: "PortfolioPage" },
  { path: "/quan-tri-danh-muc", slug: "quan-tri-danh-muc", name: "PortfolioObservatoryPage" },
  { path: "/do-rong-thi-truong", slug: "do-rong-thi-truong", name: "SectorBreadthPage" },
  { path: "/duong-chi-lich-su", slug: "duong-chi-lich-su", name: "ReplayTimelinePage" },
  { path: "/trung-tam-hanh-dong", slug: "trung-tam-hanh-dong", name: "ActionableIntelligencePage" },
  { path: "/bao-cao-tuan", slug: "bao-cao-tuan", name: "WeeklyCognitiveReport" },
  { path: "/paper-trading", slug: "paper-trading", name: "PaperTradingPage" },
  { path: "/epistemic", slug: "epistemic", name: "EpistemicDashboard" },
  { path: "/vn20", slug: "vn20", name: "VN20IndexDashboard" },
];

/**
 * FINDING (audit 2026-08-24): App gate gọi /api/session-info nhưng endpoint
 * KHÔNG tồn tại trong FastAPI -> white-screen toàn app khi backend thiếu.
 * Harness mock endpoint này (gate chỉ cần res.ok) để chụp UI thật với
 * data từ các API khác. Fix root cause thuộc patch riêng.
 */
async function mockHealthGate(context) {
  await context.route("**/api/session-info", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
}

const VIEWPORTS = [
  { w: 1440, h: 900, tag: "1440x900" },
  { w: 1280, h: 800, tag: "1280x800" },
];

// App.tsx gate: block render tới khi health endpoint phản hồi (~15 retry).
// Cho thêm thời gian chart/query settle; white-screen vẫn chụp để bắt lỗi.
async function settle(page) {
  try {
    await page.waitForSelector("#root > *", { timeout: 30_000 });
    await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => {});
    await page.waitForTimeout(1_500);
  } catch {
    /* white-screen case — vẫn chụp */
  }
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({ timeout: 60_000 });
  let captured = 0;
  const total = ROUTES.length * VIEWPORTS.length;

  for (const vp of VIEWPORTS) {
    const context = await browser.newContext({
      viewport: { width: vp.w, height: vp.h },
      deviceScaleFactor: 1,
    });
    await mockHealthGate(context);
    for (const route of ROUTES) {
      const page = await context.newPage();
      try {
        await page.goto(BASE_URL + route.path, {
          waitUntil: "domcontentloaded",
          timeout: 30_000,
        });
        await settle(page);
        const file = join(OUT_DIR, `${route.slug}_${vp.tag}.png`);
        await page.screenshot({ path: file, fullPage: true });
        captured++;
        console.log(`[capture] ${route.name} @ ${vp.tag} -> ${file}`);
      } catch (e) {
        console.error(`[capture] FAIL ${route.path} @ ${vp.tag}: ${e.message?.split("\n")[0]}`);
      } finally {
        await page.close();
      }
    }
    await context.close();
  }

  await browser.close();
  console.log(`\n[done] ${captured}/${total} screenshots -> ${OUT_DIR}`);
  if (captured === 0) process.exit(1);
}

main();
