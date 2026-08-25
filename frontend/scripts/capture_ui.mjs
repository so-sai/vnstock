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
import { mkdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE_URL = process.env.BASE_URL ?? "http://localhost:5173";
// OUT_DIR mặc định: frontend/ui_audit (khớp .gitignore) — KHÔNG phải scripts/.
const OUT_DIR = resolve(process.env.OUT_DIR ?? join(HERE, "..", "ui_audit"));

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
 * Block mọi request ngoài localhost: ảnh chụp deterministic (không phụ thuộc
 * Google Fonts/CDN) và không trigger Windows Firewall prompt cho headless
 * chromium. Font Inter sẽ fallback về sans-serif — chấp nhận cho audit.
 */
async function blockExternalRequests(context) {
  await context.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (route) =>
    route.abort(),
  );
}

const VIEWPORTS = [
  { w: 1440, h: 900, tag: "1440x900" },
  { w: 1280, h: 800, tag: "1280x800" },
];

// App.tsx gate: block render tới khi /api/system/session-info phản hồi
// (đã whitelist trong circuit breaker — commit e7a4f9b; KHÔNG mock ở đây:
// harness phải phản chiếu behavior thật để bắt regression của chính gate).
// state:'attached' — KHÔNG dùng default 'visible': react-aria overlay
// (section ẩn) là element đầu tiên trong #root và làm visible-wait treo 30s.
async function settle(page) {
  try {
    await page.waitForSelector("#root > *", {
      state: "attached",
      timeout: 20_000,
    });
    await page.waitForLoadState("networkidle", { timeout: 12_000 }).catch(() => {});
    await page.waitForTimeout(2_500);
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
    await blockExternalRequests(context);
    for (const route of ROUTES) {
      const page = await context.newPage();
      try {
        await page.goto(BASE_URL + route.path, {
          waitUntil: "domcontentloaded",
          timeout: 30_000,
        });
        await settle(page);
        const file = join(OUT_DIR, `${route.slug}_${vp.tag}.png`);
        // White-screen guard: ảnh <40KB gần như chắc chắn #root rỗng
        // (Vite/backend cold start). Retry tối đa 2 lần.
        for (let attempt = 0; attempt < 3; attempt++) {
          if (attempt > 0) {
            console.log(`[retry] ${route.slug}@${vp.tag} attempt ${attempt + 1} (ảnh quá nhỏ = trắng)`);
            await page.reload({ waitUntil: "domcontentloaded", timeout: 30_000 });
            await settle(page);
          }
          await page.screenshot({ path: file, fullPage: true });
          const kb = statSync(file).size / 1024;
          if (kb >= 40) {
            captured++;
            console.log(`[capture] ${route.name} @ ${vp.tag} -> ${file} (${Math.round(kb)}KB)`);
            break;
          }
          if (attempt === 2) {
            captured++;
            console.log(`[warn] ${route.name} @ ${vp.tag} vẫn trắng sau 3 lần — giữ ảnh cuối (${Math.round(kb)}KB)`);
          }
        }
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
