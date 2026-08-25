/**
 * test_ui_contract.mjs — E2E Regression Contract (Playwright + Node).
 *
 * Biến toàn bộ bài học từ Playwright audit 24/08 thành contract tự động.
 * Exit code != 0 khi vi phạm — dùng được cho CI/CD hoặc chạy thủ công:
 *
 *   cd frontend && node scripts/test_ui_contract.mjs
 *
 * Contracts:
 *   1. ANTI-WHITE-SCREEN : #root innerHTML >= 5000 bytes, không có gate text.
 *   2. ANTI-RAW-DEBUG    : body không chứa "undefined"/"NaN"/"[object Object]"/
 *                          "API Error"/raw JSON envelope/enum thô.
 *   3. ANTI-DARK-THEME   : không element nào dùng bg slate-900/neutral-900
 *                          (app là Japandi light-only).
 *
 * WHY node, KHÔNG bun: bun 1.3.14 (Windows) treo khi spawn chromium.
 * Rate-limit backend 15 req/60s -> delay 30s/route + retry 65s khi dính.
 */

import { chromium } from "playwright";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE_URL = process.env.BASE_URL ?? "http://localhost:5173";

const ROUTES = [
  { path: "/", name: "MacroDashboard(index)" },
  { path: "/nhip-dap-vi-mo", name: "MacroDashboard" },
  { path: "/bo-loc-kim-cuong", name: "ScreenerPage" },
  { path: "/mo-hinh-alpha", name: "AlphaModelsPage" },
  { path: "/kiem-chung-lich-su", name: "TimeKernelPage" },
  { path: "/so-tay-danh-muc", name: "PortfolioPage" },
  { path: "/quan-tri-danh-muc", name: "PortfolioObservatoryPage" },
  { path: "/do-rong-thi-truong", name: "SectorBreadthPage" },
  { path: "/duong-chi-lich-su", name: "ReplayTimelinePage" },
  { path: "/trung-tam-hanh-dong", name: "ActionableIntelligencePage" },
  { path: "/bao-cao-tuan", name: "WeeklyCognitiveReport" },
  { path: "/paper-trading", name: "PaperTradingPage" },
  { path: "/epistemic", name: "EpistemicDashboard" },
  { path: "/vn20", name: "VN20IndexDashboard" },
];

const MIN_ROOT_BYTES = 5_000;
const GATE_TEXT = /Đang kết nối Backend|Backend không phản hồi/;
const RAW_DEBUG = /API Error|RATE_LIMITED|distribution_equivalent|\[object Object\]|\{"[a-z_]+":/;
const RAW_TOKEN = /\bundefined\b|\bNaN\b/;
const DARK_CLASS = '[class*="slate-900"], [class*="neutral-900"]';

async function settle(page) {
  try {
    await page.waitForSelector("#root > *", { state: "attached", timeout: 20_000 });
    await page.waitForLoadState("networkidle", { timeout: 12_000 }).catch(() => {});
    await page.waitForTimeout(2_500);
  } catch {
    /* contract sẽ báo fail */
  }
}

async function auditRoute(page, route) {
  const rootLen = await page.evaluate(
    () => document.getElementById("root")?.innerHTML.length ?? 0,
  );
  const bodyText = await page.evaluate(() => document.body.innerText).catch(() => "");
  const darkCount = await page.evaluate(
    (sel) => document.querySelectorAll(sel).length,
    DARK_CLASS,
  );

  const violations = [];
  if (rootLen < MIN_ROOT_BYTES) violations.push(`C1 white-screen (root=${rootLen}B < ${MIN_ROOT_BYTES}B)`);
  if (GATE_TEXT.test(bodyText)) violations.push("C1 gate text còn hiển thị");
  const rawMatch = bodyText.match(RAW_DEBUG);
  if (rawMatch) violations.push(`C2 raw debug leak: "${rawMatch[0]}"`);
  const tokenMatch = bodyText.match(RAW_TOKEN);
  if (tokenMatch) violations.push(`C2 raw token: "${tokenMatch[0]}"`);
  if (darkCount > 0) violations.push(`C3 dark theme class x ${darkCount}`);
  return { rootLen, violations };
}

async function main() {
  const browser = await chromium.launch({ timeout: 60_000 });

  // ── Warm-up: backend cold-start làm fetch gate (AbortSignal 3s) abort
  // vài vòng đầu -> gate treo -> false positive C1. Đánh thức engine trước.
  console.log("[warmup] đánh thức backend (cold imports 2-5s/endpoint)...");
  const warmPage = await (await browser.newContext()).newPage();
  await warmPage.goto(BASE_URL + "/", { waitUntil: "domcontentloaded", timeout: 30_000 });
  await warmPage.waitForTimeout(8_000);
  await warmPage.close();

  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  // Chặn request ngoài localhost (deterministic, không firewall prompt).
  await context.route(/^https?:\/\/(?!localhost|127\.0\.0\.1)/, (r) => r.abort());

  let passed = 0;
  let failed = 0;
  const failures = [];

  for (const route of ROUTES) {
    const page = await context.newPage();
    try {
      await page.goto(BASE_URL + route.path, {
        waitUntil: "domcontentloaded",
        timeout: 30_000,
      });
      await settle(page);
      let result = await auditRoute(page, route);

      // Gate-text retry: session-info là async endpoint nhưng các API nặng
      // compute sync có thể block event loop uvicorn vài giây -> AbortSignal
      // 3s của gate cắt -> gate fail THEO CỤM. Retry 3×10s (gate tự retry 2s).
      let gateRetries = 0;
      while (
        result.violations.some((v) => v.includes("gate text")) &&
        gateRetries < 3
      ) {
        gateRetries++;
        console.log(`[retry] ${route.name} — gate chưa pass (backend nghẽn?), đợi 10s [${gateRetries}/3]...`);
        await page.waitForTimeout(10_000);
        result = await auditRoute(page, route);
      }

      // Retry 1 lần khi dính rate-limit (65s = sliding window 60s hết).
      const rateLimited = /HTTP 429|RATE_LIMITED|quá nhiều request|Không thể tải|Không thể truy cập/i.test(
        await page.evaluate(() => document.body.innerText).catch(() => ""),
      );
      if (rateLimited) {
        console.log(`[retry] ${route.name} — 429, đợi 65s...`);
        await new Promise((r) => setTimeout(r, 65_000));
        await page.reload({ waitUntil: "domcontentloaded", timeout: 30_000 });
        await settle(page);
        result = await auditRoute(page, route);
      }

      if (result.violations.length === 0) {
        passed++;
        console.log(`[PASS] ${route.name} (root=${result.rootLen}B)`);
      } else {
        failed++;
        failures.push({ route: route.name, violations: result.violations });
        console.error(`[FAIL] ${route.name}: ${result.violations.join(" | ")}`);
      }
    } catch (e) {
      failed++;
      failures.push({ route: route.name, violations: [`EXCEPTION ${e.message?.split("\n")[0]}`] });
      console.error(`[FAIL] ${route.name}: ${e.message?.split("\n")[0]}`);
    } finally {
      await page.close();
      await new Promise((r) => setTimeout(r, 10_000)); // rate-limit spacing
    }
  }

  await browser.close();
  console.log(`\n[contract] PASS=${passed} FAIL=${failed} / ${ROUTES.length}`);
  if (failures.length > 0) {
    console.log(JSON.stringify(failures, null, 2));
    process.exit(1);
  }
}

main();
