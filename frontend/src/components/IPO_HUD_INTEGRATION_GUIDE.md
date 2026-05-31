/**
 * IPO HUD WIDGET — HƯỚNG DẪN TÍCH HỢP VÀO FRONTEND
 * ============================================================================
 * 
 * Màn hình Chỉ huy IPO — 3 Chỉ báo Rút gọn, Thuần Việt 100%
 * - 🟢🟡🔴 Đèn tín hiệu (Traffic Light)
 * - Áp suất rút vốn (LDI — 0-100)
 * - Thời gian nhiễm độc (Time Decay Countdown)
 * 
 * ============================================================================
 */

// ═══════════════════════════════════════════════════════════════════════════
// PHẦN 1: TÍCH HỢP COMPONENT VÀO PAGES
// ═══════════════════════════════════════════════════════════════════════════

/**
 * OPTION A: Tích hợp vào ActionableIntelligencePage
 * ─────────────────────────────────────────────────────────────────────────
 * File: frontend/src/pages/ActionableIntelligencePage.tsx
 * 
 * Thay đổi:
 */

// Import component & hook
import IPOHUDWidget from '../components/IPOHUDWidget';
import { useIPOSignal } from '../hooks/useIPOSignal';

export const ActionableIntelligencePage: React.FC = () => {
  // Existing hooks...
  const { data: portfolioData } = usePortfolio();
  
  return (
    <div className="p-8 bg-japandi-oat min-h-screen">
      {/* Existing header */}
      <Title>Trung tâm Hành động</Title>
      
      {/* NEW: IPO HUD Widget */}
      <div className="mb-6">
        <ErrorBoundary>
          <IPOHUDWidget />
        </ErrorBoundary>
      </div>
      
      {/* Existing components */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <PortfolioCoach />
        <RegimeAdvisorBanner />
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════

/**
 * OPTION B: Tích hợp vào MacroDashboard (nơi tốt nhất)
 * ─────────────────────────────────────────────────────────────────────────
 * File: frontend/src/pages/MacroDashboard.tsx
 * 
 * Lý do: MacroDashboard là "Màn hình Tổng quan Vĩ mô", IPO là một tín hiệu
 *        Vĩ mô quan trọng → nên ở gần RegimeAdvisor
 * 
 * Thay đổi:
 */

import IPOHUDWidget from '../components/IPOHUDWidget';

export const MacroDashboard: React.FC = () => {
  return (
    <div className="p-8 bg-japandi-oat min-h-screen">
      <Title>Nhịp đập Vĩ mô</Title>
      
      {/* Top: Macro + IPO HUD side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
        <ErrorBoundary>
          <LiveSummaryBar />
        </ErrorBoundary>
        <ErrorBoundary>
          <IPOHUDWidget />
        </ErrorBoundary>
      </div>
      
      {/* Rest of dashboard */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <RegimeAdvisorBanner />
        <BreadthMetrics />
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════

/**
 * OPTION C: Tích hợp vào PortfolioObservatoryPage (Chi tiết nhất)
 * ─────────────────────────────────────────────────────────────────────────
 * File: frontend/src/pages/PortfolioObservatoryPage.tsx
 * 
 * Lý do: Observatory = "Trạm Quan sát" → Giám sát IPO impact trên Portfolio
 * 
 * Thay đổi:
 */

import IPOHUDWidget from '../components/IPOHUDWidget';

export const PortfolioObservatoryPage: React.FC = () => {
  return (
    <div className="p-8 bg-japandi-oat min-h-screen">
      <Title>Trạm Quan sát Danh mục</Title>
      
      {/* Alert: IPO có thể tác động */}
      <div className="mb-6">
        <ErrorBoundary>
          <IPOHUDWidget />
        </ErrorBoundary>
      </div>
      
      {/* Portfolio positions + IPO rotation risk analysis */}
      <PortfolioPositionRiskMatrix ipoAdjustment={true} />
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// PHẦN 2: COMPONENT USAGE
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Cách sử dụng component:
 * 
 * 1. Import hook (optional, nếu cần access dữ liệu trực tiếp)
 *    import { useIPOSignal } from '../hooks/useIPOSignal';
 * 
 * 2. Component tự động fetch data:
 *    <IPOHUDWidget />
 * 
 * 3. Component handle loading/error states:
 *    - Loading: Hiển thị skeleton
 *    - Error: Fallback không crash
 *    - Auto-refetch: Mỗi 60 giây
 */

// ═══════════════════════════════════════════════════════════════════════════
// PHẦN 3: STYLING & CUSTOMIZATION
// ═══════════════════════════════════════════════════════════════════════════

/**
 * HUD Widget tuân theo Japandi color scheme:
 * 
 * Traffic Light Colors:
 * ├─ 🟢 XANH:  bg-emerald-50, border-emerald-300
 * ├─ 🟡 VANG:  bg-amber-50, border-amber-300
 * └─ 🔴 DO:    bg-rose-50, border-rose-300
 * 
 * LDI Progress Bar:
 * ├─ Nặng (70-100):    bg-rose-500
 * ├─ Trung (50-70):    bg-amber-500
 * └─ Nhẹ (0-50):       bg-emerald-500
 * 
 * Time Decay Bar:
 * └─ Gradient: rose → amber → emerald (thể hiện tiến trình suy hao)
 * 
 * Để tùy chỉnh màu sắc, sửa trong IPOHUDWidget.tsx:
 *   - TrafficLightColors object
 *   - RotationRiskColors object
 *   - Tailwind className
 */

// ═══════════════════════════════════════════════════════════════════════════
// PHẦN 4: API & BACKEND REQUIREMENTS
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Backend cần cung cấp:
 * 
 * Endpoint: GET /api/intelligence/ipo-signal/
 * 
 * Response:
 * {
 *   "ipo_signal": {
 *     "symbol": "DMX",
 *     "listing_date": "2026-05-25",
 *     "market_cap_billion": 14360,
 *     "traffic_light": "DO",
 *     "valuation_risk_score": 65.5,
 *     "capital_absorption_trend": "TANG_MANH",
 *     "secondary_market_pressure": 72.0,
 *     "rotation_risk": "CAO",
 *     "midcap_smallcap_pressure": 65.0,
 *     "liquidity_regime": "TANG_GIAN",
 *     "regime_modifier": 0.82,
 *     "days_until_decay": 4,
 *     "action_command": {
 *       "primaryAction": "SELL / REDUCE",
 *       "riskLevel": "CAO",
 *       "marginSetting": 0.5,
 *       "sectorWatch": ["Midcap", "Smallcap"],
 *       "interpretation": "..."
 *     }
 *   },
 *   "updated_at": "2026-05-25T16:30:00Z"
 * }
 * 
 * Status codes:
 * - 200: IPO signal found
 * - 404: No active IPO (fallback: show "Không có tín hiệu IPO hiện tại")
 * - 500: Server error
 */

// ═══════════════════════════════════════════════════════════════════════════
// PHẦN 5: FALLBACK HANDLING (Khi không có IPO)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Nếu backend trả 404 (không có IPO nào):
 * 
 * Hiển thị:
 *   ✅ "Không có tín hiệu IPO đặc biệt hiện tại. Thị trường ổn định."
 * 
 * Styling: bg-emerald-50, border-emerald-300 (xanh nhẹ)
 * 
 * Cách implement (trong IPOHUDWidget.tsx):
 * 
 *   if (!ipo) {
 *     return (
 *       <div className="bg-emerald-50 border border-emerald-300 rounded-lg p-4">
 *         <span className="text-xs text-emerald-700">
 *           ✅ Không có tín hiệu IPO. Thị trường bình thường.
 *         </span>
 *       </div>
 *     );
 *   }
 */

// ═══════════════════════════════════════════════════════════════════════════
// PHẦN 6: TESTING
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Test các scenario:
 * 
 * 1. Test 🟢 XANH (IPO tốt, breadth khỏe)
 *    - Mock: traffic_light = "XANH", ldi_score = 30, days_until_decay = 0
 * 
 * 2. Test 🟡 VÀNG (IPO thành công, secondary yếu)
 *    - Mock: traffic_light = "VANG", ldi_score = 55, days_until_decay = 3
 * 
 * 3. Test 🔴 ĐỎ (IPO hot, Midcap chết thanh khoản)
 *    - Mock: traffic_light = "DO", ldi_score = 75, days_until_decay = 1
 * 
 * 4. Test Loading state
 *    - Mock: delay 2-3 giây
 * 
 * 5. Test Error state
 *    - Mock: API error 500
 * 
 * 6. Test Decay countdown
 *    - Mock: days_until_decay từ 5 → 0 (simulate 5 ngày)
 */

// ═══════════════════════════════════════════════════════════════════════════
// PHẦN 7: QUICK SETUP CHECKLIST
// ═══════════════════════════════════════════════════════════════════════════

/**
 * ✅ SETUP STEPS:
 * 
 * 1. [ ] Copy file IPOHUDWidget.tsx → frontend/src/components/
 * 2. [ ] Copy file useIPOSignal.ts → frontend/src/hooks/
 * 3. [ ] Copy file ipo_signal_api.py → backend/src/api/
 * 4. [ ] Tích hợp endpoint vào backend main.py:
 *        from api.ipo_signal_api import router as ipo_router
 *        app.include_router(ipo_router, prefix="/api")
 * 5. [ ] Thêm IPOHUDWidget vào 1 page (ví dụ: MacroDashboard)
 * 6. [ ] Test API endpoint: curl http://localhost:8000/api/intelligence/ipo-signal/
 * 7. [ ] Test frontend: npm run dev & verify widget renders
 * 8. [ ] Verify auto-refetch mỗi 60 giây
 * 9. [ ] Monitor Performance (check React Query DevTools)
 * 10. [ ] Deploy!
 * 
 * Optional:
 * - [ ] Thêm IPO signal history chart (frontend/src/components/IPOSignalChart.tsx)
 * - [ ] Add notifications khi traffic light đổi màu
 * - [ ] Thêm export/report IPO signal history
 */

// ═══════════════════════════════════════════════════════════════════════════
// PHẦN 8: PERFORMANCE NOTES
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Performance Optimization:
 * 
 * 1. Caching (React Query):
 *    - staleTime: 30s (dữ liệu tươi trong 30s)
 *    - cacheTime: 5min (mặc định, xóa cache sau 5min)
 *    - Auto-refetch: 60s
 * 
 * 2. Component Rendering:
 *    - useMemo cho decay calculation
 *    - useMemo cho LDI calculation
 *    - Prevent re-render khi không cần thiết
 * 
 * 3. Bundle Size:
 *    - IPOHUDWidget.tsx: ~4KB (minified)
 *    - useIPOSignal.ts: ~1KB (minified)
 *    - Total: ~5KB (negligible)
 * 
 * 4. API Load:
 *    - Endpoint được call 1 lần mỗi 60s
 *    - Backend cache nên be lightweight (~50ms response)
 */

// ═══════════════════════════════════════════════════════════════════════════

export default {};
