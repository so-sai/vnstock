import React, { useState, useEffect, useRef } from 'react';
import { Lock, Activity, Loader2, CheckCircle2, AlertCircle, X, Shield, Timer } from 'lucide-react';
import { useUIStore } from '../stores/uiStore';
import { listen } from '@tauri-apps/api/event';

const API_BASE = 'http://localhost:17039';

interface LogLine {
  type: 'stdout' | 'stderr';
  text: string;
  time: string;
}

interface GateStatus {
  can_sync: boolean;
  remaining_seconds: number;
  window_open: boolean;
  window_expiry: number;
}

const formatRemaining = (seconds: number): string => {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
};

const TacticalConsole: React.FC = () => {
  const { sidebarOpen } = useUIStore();
  const [loadingClose, setLoadingClose] = useState(false);
  const [loadingDrift, setLoadingDrift] = useState(false);
  const [statusLog, setStatusLog] = useState('Hệ thống đang quan sát...');
  const [statusType, setStatusType] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [showLogs, setShowLogs] = useState(false);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);

  // ── 2FA GATE STATES ──
  const [gateStatus, setGateStatus] = useState<GateStatus>({
    can_sync: false,
    remaining_seconds: 0,
    window_open: false,
    window_expiry: 0,
  });
  const [showModal, setShowModal] = useState(false);
  const [sliderValue, setSliderValue] = useState(0);
  const [countdown, setCountdown] = useState(0);
  const [syncPhase, setSyncPhase] = useState<'idle' | 'opening' | 'syncing' | 'closing'>('idle');

  // Polling gate status mỗi 10 giây
  useEffect(() => {
    const checkGate = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/system/gate`);
        if (res.ok) {
          const data = await res.json();
          setGateStatus(data);
        }
      } catch {
        // Backend chưa sẵn sàng — im lặng
      }
    };
    checkGate();
    const interval = setInterval(checkGate, 10000);
    return () => clearInterval(interval);
  }, []);

  // Countdown timer: 60s -> 0
  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          // Auto-close gate khi hết giờ
          fetch(`${API_BASE}/api/system/gate/close`, { method: 'POST' }).catch(() => {});
          setGateStatus((g) => ({ ...g, window_open: false }));
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [countdown > 0]);

  // Logs listener
  useEffect(() => {
    let active = true;
    let unsubStdout: (() => void) | null = null;
    let unsubStderr: (() => void) | null = null;

    const isTauri = typeof window !== 'undefined' && (window as any).__TAURI__ !== undefined;

    if (isTauri) {
      listen<string>('backend-stdout', (event) => {
        if (!active) return;
        const text = event.payload;
        setLogs((prev) => [...prev.slice(-499), { type: 'stdout', text, time: new Date().toLocaleTimeString() }]);
      }).then((unsub) => {
        unsubStdout = unsub;
        if (!active) unsub();
      });

      listen<string>('backend-stderr', (event) => {
        if (!active) return;
        const text = event.payload;
        setLogs((prev) => [...prev.slice(-499), { type: 'stderr', text, time: new Date().toLocaleTimeString() }]);
      }).then((unsub) => {
        unsubStderr = unsub;
        if (!active) unsub();
      });
    }

    return () => {
      active = false;
      if (unsubStdout) unsubStdout();
      if (unsubStderr) unsubStderr();
    };
  }, []);

  useEffect(() => {
    if (showLogs) {
      logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, showLogs]);

  const handleDailyClose = async () => {
    setLoadingClose(true);
    setStatusLog('Đang đồng bộ và khóa sổ cái SQLite...');
    setStatusType('loading');
    try {
      const res = await fetch(`${API_BASE}/api/operations/daily-close`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setStatusLog(data.message || 'CHỐT PHIÊN THÀNH CÔNG: Sổ cái SQLite đã khóa!');
      setStatusType('success');
    } catch (err) {
      setStatusLog(`Lỗi kết phiên: ${err}`);
      setStatusType('error');
    } finally {
      setLoadingClose(false);
    }
  };

  const handleTelemetryDrift = async () => {
    setLoadingDrift(true);
    setStatusLog('Đang phân tích độ lệch hệ thống (Drift)...');
    setStatusType('loading');
    try {
      const res = await fetch(`${API_BASE}/api/operations/telemetry-drift`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setStatusLog(data.message || 'ĐÃ GHI NHẬT KÝ DRIFT: Baseline 90 ngày ổn định!');
      setStatusType('success');
    } catch (err) {
      setStatusLog(`Lỗi đo Drift: ${err}`);
      setStatusType('error');
    } finally {
      setLoadingDrift(false);
    }
  };

  // ── 2FA HANDLERS ──
  const handleSyncAttempt = () => {
    if (!gateStatus.can_sync) return;
    setShowModal(true);
    setSliderValue(0);
  };

  const handleConfirmOpenGate = async () => {
    if (sliderValue < 90) return; // Phải kéo đến 90% mới cho phép
    setShowModal(false);
    setSyncPhase('opening');
    try {
      const openRes = await fetch(`${API_BASE}/api/system/gate/open`, { method: 'POST' });
      if (!openRes.ok) {
        const err = await openRes.json();
        throw new Error(err.message || 'Không thể mở van');
      }
      const openData = await openRes.json();
      setGateStatus((g) => ({ ...g, window_open: true, window_expiry: openData.expiry }));
      setCountdown(60);
      setSyncPhase('syncing');

      // Chạy daily-close trong cửa sổ mở
      await handleDailyClose();

      // Đóng van ngay sau khi xong
      setSyncPhase('closing');
      await fetch(`${API_BASE}/api/system/gate/close`, { method: 'POST' });
      setGateStatus((g) => ({ ...g, window_open: false }));
      setCountdown(0);
      setSyncPhase('idle');
    } catch (err) {
      setStatusLog(`Lỗi đồng bộ: ${err}`);
      setStatusType('error');
      setSyncPhase('idle');
      // Đảm bảo van đóng dù có lỗi
      fetch(`${API_BASE}/api/system/gate/close`, { method: 'POST' }).catch(() => {});
      setCountdown(0);
    }
  };

  const statusColors: Record<string, string> = {
    idle: 'text-japandi-muted-clay',
    loading: 'text-amber-600',
    success: 'text-emerald-600',
    error: 'text-rose-600',
  };

  // Trạng thái nút CHỐT
  const isButtonDisabled = !gateStatus.can_sync || syncPhase !== 'idle' || loadingClose;
  const buttonLabel = countdown > 0
    ? `VAN MỞ — ${countdown}s`
    : !gateStatus.can_sync
    ? `CHỜ 12H — ${formatRemaining(gateStatus.remaining_seconds)}`
    : '1. KÍCH HOẠT CẬP NHẬT';

  return (
    <>
      <div className="border-b border-japandi-muted-clay/30 bg-japandi-warm-sand/20 px-4 py-2.5 font-mono">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-japandi-muted-clay text-[10px] font-semibold uppercase tracking-wider whitespace-nowrap">
              PTCK_VN // Kỷ luật 90 ngày
            </span>
            <span className="text-japandi-muted-clay/40 hidden sm:inline">|</span>
            <span className={`text-[11px] truncate ${statusColors[statusType]}`}>
              {statusType === 'loading' && <Loader2 size={11} className="inline animate-spin mr-1" />}
              {statusType === 'success' && <CheckCircle2 size={11} className="inline mr-1" />}
              {statusType === 'error' && <AlertCircle size={11} className="inline mr-1" />}
              {statusLog}
            </span>
          </div>

          <div className="flex gap-2 shrink-0 ml-4">
            <button
              onClick={() => setShowLogs(!showLogs)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium rounded-lg border transition-all duration-150 ${
                showLogs
                  ? 'border-emerald-600 bg-emerald-600/10 text-emerald-600'
                  : 'border-japandi-muted-clay/40 text-japandi-earth hover:bg-japandi-muted-clay/20'
              }`}
            >
              <span className="font-bold">&gt;_</span>
              <span>{showLogs ? 'ẨN LOGS' : 'XEM LOGS'}</span>
            </button>

            {/* NÚT CHỐT DỮ LIỆU 2FA */}
            <button
              onClick={handleSyncAttempt}
              disabled={isButtonDisabled}
              title={
                !gateStatus.can_sync
                  ? 'Hệ thống đang đóng băng. Chỉ mở van sau 12 giờ kể từ lần cập nhật cuối.'
                  : '⚠️ Hành động này sẽ kết nối Internet. Chỉ bấm 1 lần/ngày sau 16:00.'
              }
              className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium rounded-lg border transition-all duration-150 group disabled:opacity-50 disabled:cursor-not-allowed ${
                countdown > 0
                  ? 'border-emerald-500 bg-emerald-50/30 text-emerald-700 animate-pulse'
                  : !gateStatus.can_sync
                  ? 'border-stone-300 bg-stone-100/50 text-stone-500'
                  : 'border-rose-400/60 bg-rose-50/30 text-japandi-earth hover:bg-rose-100 hover:border-rose-500 hover:text-rose-900'
              }`}
            >
              {loadingClose || syncPhase !== 'idle' ? (
                <Loader2 size={13} className="animate-spin" />
              ) : countdown > 0 ? (
                <Timer size={13} className="text-emerald-600" />
              ) : (
                <Lock size={13} className={gateStatus.can_sync ? 'text-rose-600 group-hover:text-rose-800' : 'text-stone-400'} />
              )}
              <span className="hidden sm:inline flex flex-col items-start leading-tight">
                <span>{buttonLabel}</span>
                <span className={`text-[9px] font-semibold uppercase tracking-wider ${countdown > 0 ? 'text-emerald-600' : gateStatus.can_sync ? 'text-rose-600/90' : 'text-stone-400'}`}>
                  {countdown > 0 ? '⚠ API ĐANG MỞ' : gateStatus.can_sync ? '⚠ API • 1 lần/ngày' : '⛔ ĐÓNG BĂNG'}
                </span>
              </span>
              <span className="sm:hidden">{loadingClose ? '...' : countdown > 0 ? countdown : 'CHỐT'}</span>
            </button>

            <button
              onClick={handleTelemetryDrift}
              disabled={loadingDrift}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium rounded-lg border border-japandi-moss/60 bg-japandi-moss/10 text-japandi-moss hover:bg-japandi-moss hover:text-japandi-oat disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-150"
            >
              {loadingDrift ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Activity size={13} />
              )}
              <span className="hidden sm:inline">{loadingDrift ? 'ĐANG ĐO...' : '2. ĐO ĐỘ LỆCH (DRIFT)'}</span>
              <span className="sm:hidden">{loadingDrift ? '...' : 'DRIFT'}</span>
            </button>
          </div>
        </div>

        {showLogs && (
          <div className={`fixed bottom-0 right-0 z-50 bg-stone-950 border-t border-stone-800 text-stone-300 font-mono text-xs flex flex-col transition-all duration-300 ${
            sidebarOpen ? 'left-64' : 'left-20'
          }`} style={{ height: '260px' }}>
            <div className="flex items-center justify-between px-4 py-2 bg-stone-900 border-b border-stone-800 select-none">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                <span className="font-bold text-stone-400">NHẬT KÝ TIẾN TRÌNH HỆ THỐNG (uv_backend logs)</span>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setLogs([])}
                  className="text-stone-400 hover:text-stone-200 text-[10px] px-2 py-0.5 rounded border border-stone-700 bg-stone-900 transition-colors"
                >
                  Xóa nhật ký
                </button>
                <button
                  onClick={() => setShowLogs(false)}
                  className="text-stone-400 hover:text-rose-400 transition-colors"
                >
                  <X size={14} />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-1 select-text scrollbar-thin scrollbar-thumb-stone-800">
              {logs.length === 0 ? (
                <div className="text-stone-600 italic">Chưa có bản ghi nào. Hãy thực hiện thao tác hoặc khởi động tiến trình để ghi nhận logs...</div>
              ) : (
                logs.map((log, idx) => (
                  <div key={idx} className={`leading-relaxed whitespace-pre-wrap ${
                    log.type === 'stderr' ? 'text-rose-400/90' : 'text-emerald-400/90'
                  }`}>
                    <span className="text-stone-600 mr-2">[{log.time}]</span>
                    {log.text}
                  </div>
                ))
              )}
              <div ref={logEndRef} />
            </div>
          </div>
        )}
      </div>

      {/* ═══════════════════════════════════════════════════════════
          MODAL 2FA — HẦM PHÓNG HẠT NHÂN
      ═══════════════════════════════════════════════════════════ */}
      {showModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-japandi-oat border border-japandi-earth/30 rounded-2xl shadow-2xl w-full max-w-md mx-4 p-6 space-y-5">
            <div className="flex items-center gap-3 text-rose-700">
              <Shield size={24} />
              <h2 className="text-lg font-bold uppercase tracking-wide">Cảnh báo cao độ</h2>
            </div>

            <p className="text-sm text-japandi-earth leading-relaxed">
              Hệ thống đang ở chế độ <strong>Offline tuyệt đối</strong>. Cập nhật Internet sẽ tiêu tốn 1 lượt API quota
              quý giá. Sau khi xác nhận, van sẽ mở trong đúng <strong>60 giây</strong>.
            </p>

            <div className="space-y-2">
              <label className="text-xs font-semibold uppercase tracking-wider text-japandi-muted-clay">
                Kéo thanh trượt để xác nhận
              </label>
              <input
                type="range"
                min={0}
                max={100}
                value={sliderValue}
                onChange={(e) => setSliderValue(Number(e.target.value))}
                className="w-full h-3 rounded-lg appearance-none cursor-pointer bg-stone-200 accent-rose-600"
              />
              <div className="flex justify-between text-[10px] text-stone-500">
                <span>Khóa</span>
                <span className={sliderValue >= 90 ? 'text-emerald-600 font-bold' : ''}>
                  {sliderValue >= 90 ? '✓ Sẵn sàng' : `${sliderValue}%`}
                </span>
              </div>
            </div>

            <div className="flex gap-3 pt-2">
              <button
                onClick={() => setShowModal(false)}
                className="flex-1 px-4 py-2.5 text-sm font-medium rounded-lg border border-stone-300 text-stone-600 hover:bg-stone-100 transition-colors"
              >
                HỦY BỎ
              </button>
              <button
                onClick={handleConfirmOpenGate}
                disabled={sliderValue < 90}
                className="flex-1 px-4 py-2.5 text-sm font-bold rounded-lg bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                XÁC NHẬN — MỞ VAN 60 GIÂY
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default TacticalConsole;
