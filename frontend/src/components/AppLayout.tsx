import React, { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import XRayDrawer from './XRayDrawer';
import TacticalConsole from './TacticalConsole';
import CommandPalette from './CommandPalette';
import ErrorBoundary from './ErrorBoundary';
import { useUIStore } from '../stores/uiStore';
import { Search, Calendar, AlertTriangle } from 'lucide-react';

interface SessionInfo {
  target_date: string | null;
  target_date_vn: string | null;
  session_label: string | null;
  session_time: string | null;
  regime_status: string;
  regime_score: number | null;
  breadth_pct: number | null;
  is_stale: boolean;
  server_now: string;
}

/** Boundary cấp route: lỗi 1 trang không giết khung điều hướng. */
const RouteErrorBoundary: React.FC = () => (
  <ErrorBoundary>
    <Outlet />
  </ErrorBoundary>
);

const AppLayout: React.FC = () => {
  const location = useLocation();
  const { xraySymbol, closeXRay, openXRay } = useUIStore();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [session, setSession] = useState<SessionInfo | null>(null);

  useEffect(() => {
    const handler = (e: CustomEvent<string>) => {
      openXRay(e.detail);
    };
    window.addEventListener('navigate-to-xray', handler as EventListener);
    return () => window.removeEventListener('navigate-to-xray', handler as EventListener);
  }, [openXRay]);

  // Fetch session info mỗi 60s — nhẹ (1 query SQL, không engine)
  useEffect(() => {
    let active = true;
    const fetchSession = async () => {
      try {
        const res = await fetch('/api/system/session-info');
        if (res.ok) {
          const data = await res.json();
          if (active) setSession(data);
        }
      } catch {
        // Silent — Header sẽ hiển thị "Mất kết nối CSDL"
      }
    };
    fetchSession();
    const interval = setInterval(fetchSession, 60000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="flex min-h-screen bg-japandi-oat/90 backdrop-blur-sm">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="sticky top-0 z-30 bg-japandi-oat/80 backdrop-blur-sm border-b border-japandi-muted-clay/20 px-6 py-2 flex items-center justify-between">
          <button
            onClick={() => setPaletteOpen(true)}
            className="flex items-center gap-2 px-4 py-1.5 bg-white/60 border border-japandi-muted-clay/30 rounded-lg text-xs text-japandi-muted-clay hover:text-japandi-earth hover:border-japandi-earth/40 transition-all max-w-sm"
          >
            <Search size={14} />
            <span>Tìm kiếm mã CK, ngành...</span>
            <kbd className="ml-2 px-1 py-0.5 bg-japandi-muted-clay/10 rounded text-[9px] font-mono">Ctrl+K</kbd>
          </button>

          {/* Session info — ghim cứng góc phải Header */}
          {session && (
            <div
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-[11px] font-mono ${
                session.is_stale
                  ? 'border-rose-400/60 bg-rose-50/50 text-rose-700'
                  : 'border-japandi-muted-clay/30 bg-white/60 text-japandi-earth'
              }`}
              title={`Regime: ${session.regime_status} | Score: ${session.regime_score ?? 'N/A'} | Breadth: ${session.breadth_pct ?? 'N/A'}%`}
            >
              {session.is_stale ? (
                <>
                  <AlertTriangle size={12} className="text-rose-600 shrink-0" />
                  <span className="font-semibold">{session.session_label || 'Mất kết nối CSDL'}</span>
                </>
              ) : (
                <>
                  <Calendar size={12} className="text-japandi-muted-clay shrink-0" />
                  <span className="text-japandi-muted-clay/70">Phiên tác chiến:</span>
                  <span className="font-semibold">{session.session_label}</span>
                </>
              )}
            </div>
          )}
        </div>
        <TacticalConsole />
        {/* Finding #8 (audit 24/08): ErrorBoundary cấp app làm fallback thay
            TOÀN BỘ layout khi 1 trang throw -> mất sidebar/navigation. Boundary
            cấp route giữ khung điều hướng; key=pathname để reset khi đổi trang. */}
        <RouteErrorBoundary key={location.pathname} />
      </main>
      <XRayDrawer symbol={xraySymbol} onClose={closeXRay} />
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
};

export default AppLayout;
