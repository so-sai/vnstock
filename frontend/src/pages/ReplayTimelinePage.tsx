import React, { useState, useMemo, useCallback } from 'react';
import { useReplayTimeline, type ReplayDay, type ReplayEvent } from '../hooks/useApi';
import { Card } from '@tremor/react';

const CHART_H = 360;
const PAD = { t: 20, r: 20, b: 52, l: 56 };
const BAND_H = 24;
const INSPECTOR_H = 180;

const REGIME_COLORS: Record<string, { fill: string; bar: string; border: string; label: string }> = {
  TRENDING: { fill: '#10b981', bar: 'bg-emerald-500', border: 'border-emerald-200', label: 'XU HƯỚNG' },
  RANGING: { fill: '#f59e0b', bar: 'bg-amber-400', border: 'border-amber-200', label: 'ĐI NGANG' },
  CRISIS: { fill: '#f43f5e', bar: 'bg-rose-500', border: 'border-rose-200', label: 'KHỦNG HOẢNG' },
};

const EVENT_MARKERS: Record<string, { symbol: string; color: string; label: string }> = {
  PICK: { symbol: '▲', color: '#10b981', label: 'PICK' },
  BLOCKED: { symbol: '×', color: '#f43f5e', label: 'BLOCKED' },
  EXIT: { symbol: '●', color: '#f59e0b', label: 'EXIT' },
  REGIME_FLIP: { symbol: '▶', color: '#8b5cf6', label: 'REGIME FLIP' },
  RECOVERY_FIRE: { symbol: '✦', color: '#3b82f6', label: 'RECOVERY' },
};

const DEAD_ZONE_LABELS: Record<string, string> = {
  FILTERED: 'FILTERED — blocked but healthy',
  NOISE_REJECTION: 'NOISE_REJECTION — instability',
  RISK_LOCK: 'RISK_LOCK — macro defence',
  PARTICIPATION_FAIL: 'PARTICIPATION_FAIL — low breadth',
};

function fmt(n: number | null | undefined, d = 1): string {
  if (n === null || n === undefined) return '—';
  return Number(n).toFixed(d);
}

function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return Number(n).toFixed(1) + '%';
}

const ReplayTimelinePage: React.FC = () => {
  const { data, isLoading, error } = useReplayTimeline(365);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const days: ReplayDay[] = useMemo(() => data?.days ?? [], [data]);
  const events: ReplayEvent[] = useMemo(() => data?.events ?? [], [data]);

  const eventMap = useMemo(() => {
    const m = new Map<string, ReplayEvent[]>();
    for (const e of events) {
      const existing = m.get(e.date) || [];
      existing.push(e);
      m.set(e.date, existing);
    }
    return m;
  }, [events]);

  const setHover = useCallback((idx: number | null) => setHoverIdx(idx), []);

  const chart = useMemo(() => {
    const total = days.length;
    if (total === 0) return null;
    const W = 1200;
    const areaH = CHART_H - PAD.t - PAD.b;
    const bandTop = CHART_H + 4;

    const closes = days.map(d => d.close ?? 0);
    const ma50s = days.map(d => d.ma50 ?? 0);
    const ma200s = days.map(d => d.ma200 ?? 0);
    const all = [...closes, ...ma50s, ...ma200s].filter(v => v > 0);
    if (all.length === 0) return null;
    const minP = Math.min(...all) * 0.97;
    const maxP = Math.max(...all) * 1.03;
    const range = maxP - minP || 1;

    const x = (i: number) => PAD.l + (i / (total - 1)) * W;
    const y = (v: number) => PAD.t + areaH - ((v - minP) / range) * areaH;

    // Price paths
    const mkPath = (vals: (number | null)[]) =>
      vals.map((v, i) => {
        if (v === null || v === 0) return '';
        const cmd = i === 0 || vals[i - 1] === null || (vals[i - 1] ?? 0) === 0 ? 'M' : 'L';
        return `${cmd}${x(i)},${y(v)}`;
      }).filter(Boolean).join('');

    const closePath = mkPath(closes);
    const ma50Path = mkPath(ma50s);
    const ma200Path = mkPath(ma200s);

    // Y ticks
    const yTicks: { v: number; y: number }[] = [];
    for (let i = 0; i <= 5; i++) {
      const v = minP + (range * i) / 5;
      yTicks.push({ v, y: y(v) });
    }

    // X ticks
    const xStride = Math.max(1, Math.floor(total / 14));
    const xTicks: { label: string; x: number }[] = [];
    for (let i = 0; i < total; i += xStride) {
      if (days[i]?.date) xTicks.push({ label: days[i].date.slice(0, 7), x: x(i) });
    }

    // Regime bands
    const bands: { x: number; w: number; fill: string; status: string }[] = [];
    if (total > 0) {
      let start = 0;
      let prev = days[0].regime_status || 'RANGING';
      for (let i = 1; i <= total; i++) {
        const cur = i < total ? (days[i].regime_status || 'RANGING') : null;
        if (cur !== prev || i === total) {
          const c = REGIME_COLORS[prev] || REGIME_COLORS.RANGING;
          bands.push({ x: x(start), w: x(i - 1) - x(start), fill: c.fill, status: prev });
          start = i;
          if (cur) prev = cur;
        }
      }
    }

    // Event markers on chart
    const chartEvents: { x: number; y: number; ev: ReplayEvent }[] = [];
    for (let i = 0; i < total; i++) {
      const dayEvents = eventMap.get(days[i].date) || [];
      for (const ev of dayEvents) {
        const marker = EVENT_MARKERS[ev.event_type];
        if (!marker) continue;
        const px = x(i);
        const py = y(days[i].close ?? closes[i]);
        chartEvents.push({ x: px, y: py, ev });
      }
    }

    // Dead zone streaks
    const deadZones: { start: number; end: number; label: string }[] = [];
    let dzStart = -1;
    for (let i = 0; i < total; i++) {
      const d = days[i];
      const isDead = d.regime_status === 'RANGING' || d.regime_status === 'CRISIS';
      const hasEvents = (eventMap.get(d.date) || []).length > 0;
      if (isDead && !hasEvents) {
        if (dzStart === -1) dzStart = i;
      } else {
        if (dzStart !== -1 && i - dzStart >= 3) {
          const reason = d.atr_ratio && d.atr_ratio > 1.2 ? 'NOISE_REJECTION'
            : d.breadth_pct && d.breadth_pct < 30 ? 'PARTICIPATION_FAIL'
            : d.regime_status === 'CRISIS' ? 'RISK_LOCK' : 'FILTERED';
          deadZones.push({ start: dzStart, end: i - 1, label: reason });
        }
        dzStart = -1;
      }
    }

    const hoverDay = hoverIdx !== null ? days[hoverIdx] : null;
    const hoverEvents = hoverDay ? (eventMap.get(hoverDay.date) || []) : [];
    const hoverX = hoverIdx !== null ? x(hoverIdx) : null;

    return {
      W, areaH, bandTop, closePath, ma50Path, ma200Path, x, y,
      yTicks, xTicks, bands, chartEvents, deadZones,
      hoverIdx, hoverDay, hoverEvents, hoverX, total, minP, maxP,
    };
  }, [days, eventMap, hoverIdx]);

  if (error) {
    return (
      <div className="p-6 bg-japandi-oat min-h-screen flex items-center justify-center">
        <div className="text-center max-w-md">
          <p className="text-sm font-mono text-rose-500 mb-2">Lỗi kết nối dữ liệu</p>
          <p className="text-[10px] font-mono text-japandi-muted-clay/50">
            Không thể kết nối đến Backend. Vui lòng kiểm tra server.
          </p>
          <p className="text-[10px] font-mono text-japandi-muted-clay/40 mt-1">
            Chạy: <code className="bg-zinc-100 px-1 rounded">uvicorn src.api.main:app --port 1234</code>
          </p>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="p-6 bg-japandi-oat min-h-screen flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin w-6 h-6 border-2 border-japandi-muted-clay border-t-transparent rounded-full mx-auto mb-3" />
          <p className="text-sm font-mono text-japandi-muted-clay">Đang tải dữ liệu Replay...</p>
        </div>
      </div>
    );
  }

  if (!chart || days.length === 0) {
    return (
      <div className="p-6 bg-japandi-oat min-h-screen flex items-center justify-center">
        <div className="text-center max-w-md">
          <p className="text-sm font-mono text-japandi-muted-clay mb-2">Chưa có dữ liệu Replay</p>
          <p className="text-[10px] font-mono text-japandi-muted-clay/50">
            Backend chưa chạy hoặc chưa có dữ liệu lịch sử.
          </p>
          <p className="text-[10px] font-mono text-japandi-muted-clay/40 mt-1">
            Chạy: <code className="bg-zinc-100 px-1 rounded">uvicorn src.api.main:app --port 1234</code>
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 bg-japandi-oat min-h-screen">
      <header className="mb-4 border-b border-japandi-warm-sand pb-3">
        <h1 className="text-xl font-bold text-japandi-earth tracking-tight">PHÒNG THÍ NGHIỆM HỆ THỐNG</h1>
        <p className="text-xs text-japandi-muted-clay mt-1 font-mono">
          Bóc tách mọi phán quyết của Model C qua từng phiên —{` `}
          <span className="text-zinc-400">▲ PICK</span> ·{` `}
          <span className="text-rose-500">× BLOCKED</span> ·{` `}
          <span className="text-amber-500">● EXIT</span> ·{` `}
          <span className="text-purple-500">▶ REGIME FLIP</span> ·{` `}
          <span className="text-blue-500">✦ RECOVERY</span>
        </p>
      </header>

      <Card className="bg-white border border-japandi-warm-sand shadow-none p-0 overflow-visible mb-4">
        <svg
          width="100%" height="100%"
          viewBox={`0 0 ${chart.W + PAD.l + PAD.r} ${CHART_H + PAD.b + BAND_H + INSPECTOR_H + 32}`}
          preserveAspectRatio="xMidYMid meet"
          style={{ display: 'block' }}
        >
          {/* === LAYER 2: REGIME COLOR BANDS === */}
          {chart.bands.map((b, i) => (
            <rect key={`band-${i}`} x={b.x} y={PAD.t} width={Math.max(b.w, 1)} height={chart.areaH}
              fill={b.fill} opacity={0.08} />
          ))}

          {/* === GRIDLINES === */}
          {chart.yTicks.map((t, i) => (
            <g key={`yt-${i}`}>
              <line x1={PAD.l} y1={t.y} x2={chart.W + PAD.l} y2={t.y} stroke="#e5e7eb" strokeWidth={0.5} />
              <text x={PAD.l - 6} y={t.y + 3} textAnchor="end" className="fill-gray-400 text-[10px] font-mono">
                {t.v.toFixed(0)}
              </text>
            </g>
          ))}
          {chart.xTicks.map((t, i) => (
            <text key={`xt-${i}`} x={t.x} y={CHART_H - 4} textAnchor="middle" className="fill-gray-400 text-[10px] font-mono">
              {t.label}
            </text>
          ))}

          {/* === LAYER 1: PRICE LINES === */}
          <path d={chart.ma200Path} fill="none" stroke="#a78bfa" strokeWidth={1} strokeDasharray="4 3" opacity={0.6} />
          <path d={chart.ma50Path} fill="none" stroke="#60a5fa" strokeWidth={1} strokeDasharray="3 3" opacity={0.6} />
          <path d={chart.closePath} fill="none" stroke="#1f2937" strokeWidth={1.5} />

          {/* === LEGEND === */}
          <g transform={`translate(${chart.W + PAD.l - 140}, 6)`}>
            <rect x={0} y={0} width={140} height={82} rx={2} fill="white" stroke="#e5e7eb" strokeWidth={0.5} />
            <line x1={8} y1={16} x2={28} y2={16} stroke="#1f2937" strokeWidth={1.5} />
            <text x={32} y={19} className="fill-gray-500 text-[10px] font-mono">Close</text>
            <line x1={8} y1={32} x2={28} y2={32} stroke="#60a5fa" strokeWidth={1.2} strokeDasharray="3 3" />
            <text x={32} y={35} className="fill-gray-500 text-[10px] font-mono">MA50</text>
            <line x1={8} y1={48} x2={28} y2={48} stroke="#a78bfa" strokeWidth={1.2} strokeDasharray="4 3" />
            <text x={32} y={51} className="fill-gray-500 text-[10px] font-mono">MA200</text>
            <rect x={8} y={62} width={4} height={4} rx={1} fill="#f59e0b" opacity={0.6} />
            <text x={32} y={66} className="fill-gray-500 text-[10px] font-mono">Regime band</text>
          </g>

          {/* === LAYER 3: EVENT MARKERS (▲ × ●) === */}
          {chart.chartEvents.map((ce, i) => {
            const m = EVENT_MARKERS[ce.ev.event_type];
            return (
              <g key={`ev-${i}`}>
                {ce.ev.event_type === 'PICK' && (
                  <polygon
                    points={`${ce.x},${ce.y - 16} ${ce.x - 5},${ce.y - 7} ${ce.x + 5},${ce.y - 7}`}
                    fill={m.color} stroke="white" strokeWidth={1}
                  />
                )}
                {ce.ev.event_type === 'BLOCKED' && (
                  <text x={ce.x - 4} y={ce.y - 7} fill={m.color} stroke="white" strokeWidth={0.8}
                    className="text-[11px] font-bold font-mono">×</text>
                )}
                {ce.ev.event_type === 'EXIT' && (
                  <circle cx={ce.x} cy={ce.y - 10} r={4} fill={m.color} stroke="white" strokeWidth={1} />
                )}
                {ce.ev.event_type === 'REGIME_FLIP' && (
                  <text x={ce.x - 4} y={ce.y - 7} fill={m.color} stroke="white" strokeWidth={0.8}
                    className="text-[10px] font-bold font-mono">▶</text>
                )}
                {ce.ev.event_type === 'RECOVERY_FIRE' && (
                  <text x={ce.x - 4} y={ce.y - 8} fill={m.color} stroke="white" strokeWidth={0.8}
                    className="text-[11px] font-bold font-mono">✦</text>
                )}
                <line x1={ce.x} y1={ce.y} x2={ce.x} y2={chart.areaH + PAD.t} stroke="#9ca3af" strokeWidth={0.5}
                  strokeDasharray="2 2" opacity={0.4} />
              </g>
            );
          })}

          {/* === DEAD ZONE ANNOTATIONS === */}
          {chart.deadZones.map((dz, i) => {
            const cx = chart.x(Math.floor((dz.start + dz.end) / 2));
            const cy = PAD.t + chart.areaH / 2;
            const w = chart.x(dz.end) - chart.x(dz.start);
            return (
              <g key={`dz-${i}`}>
                <rect x={chart.x(dz.start)} y={PAD.t} width={Math.max(w, 1)} height={chart.areaH}
                  fill="#f59e0b" opacity={0.04} stroke="#f59e0b" strokeWidth={0.5} strokeDasharray="2 2" opacity={0.15} />
                <text x={cx} y={cy} textAnchor="middle" className="fill-amber-600/50 text-[9px] font-bold font-mono tracking-wider"
                  transform={`rotate(-90, ${cx}, ${cy})`}>
                  {DEAD_ZONE_LABELS[dz.label] || dz.label}
                </text>
              </g>
            );
          })}

          {/* === HOVER CROSSHAIR + HOVER RECT ZONES === */}
          {chart.hoverX !== null && (
            <line x1={chart.hoverX} y1={PAD.t} x2={chart.hoverX} y2={CHART_H + PAD.b} stroke="#6366f1" strokeWidth={1}
              strokeDasharray="3 2" opacity={0.5} />
          )}
          {days.map((_, i) => {
            const segW = Math.max(1, (chart.W) / chart.total);
            const segX = PAD.l + (i / chart.total) * chart.W;
            return (
              <rect key={`hz-${i}`} x={segX - segW / 2} y={PAD.t} width={segW} height={chart.areaH + BAND_H}
                className="fill-transparent hover:fill-indigo-500/5 cursor-crosshair"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              />
            );
          })}

          {/* === REGIME STATUS BAR === */}
          {chart.bands.map((b, i) => {
            const c = REGIME_COLORS[b.status] || REGIME_COLORS.RANGING;
            return (
              <g key={`rsb-${i}`}>
                <rect x={b.x} y={chart.bandTop} width={Math.max(b.w, 1)} height={BAND_H}
                  fill={b.fill} opacity={0.15} stroke={c.fill} strokeWidth={0.5} opacity={0.3} />
                <text x={b.x + b.w / 2} y={chart.bandTop + BAND_H / 2 + 3} textAnchor="middle"
                  className={`fill-gray-500 text-[9px] font-bold font-mono tracking-wider`}>
                  {c.label}
                </text>
              </g>
            );
          })}
          <text x={PAD.l} y={chart.bandTop + BAND_H + 14} className="fill-gray-400 text-[9px] font-mono">TRẠNG THÁI VĨ MÔ</text>

          {/* === X-AXIS DATE LABEL ON HOVER === */}
          {chart.hoverDay && (
            <text x={chart.hoverX!} y={CHART_H + PAD.b - 2} textAnchor="middle"
              className="fill-indigo-600 text-[9px] font-bold font-mono">
              {chart.hoverDay.date}
            </text>
          )}
        </svg>
      </Card>

      {/* === METRICS INSPECTOR PANEL (3-COLUMN) === */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 font-mono text-xs">
        {/* COLUMN A — MARKET STATE */}
        <div className="bg-white border border-gray-200 rounded-sm p-3">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">THỊ TRƯỜNG</p>
          {chart.hoverDay ? (
            <div className="space-y-1.5">
              <div className="flex justify-between"><span className="text-gray-400">Close</span><span className="font-bold text-japandi-earth">{fmt(chart.hoverDay.close, 1)}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">MA50</span><span className="text-gray-700">{fmt(chart.hoverDay.ma50, 1)}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">MA200</span><span className="text-gray-700">{fmt(chart.hoverDay.ma200, 1)}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">Độ rộng</span><span className="text-gray-700">{fmtPct(chart.hoverDay.breadth_pct)}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">Vận tốc</span><span className="text-gray-700">{fmt(chart.hoverDay.breadth_velocity)}</span></div>
            </div>
          ) : (
            <p className="text-gray-300 italic text-[11px]">Rê chuột vào biểu đồ</p>
          )}
        </div>

        {/* COLUMN B — DECISION STATE */}
        <div className="bg-white border border-gray-200 rounded-sm p-3">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">QUYẾT ĐỊNH</p>
          {chart.hoverDay ? (
            <div className="space-y-1.5">
              <div className="flex justify-between">
                <span className="text-gray-400">Trạng thái</span>
                <span className={`font-bold ${chart.hoverDay.regime_status === 'TRENDING' ? 'text-emerald-600' : chart.hoverDay.regime_status === 'CRISIS' ? 'text-rose-600' : 'text-amber-600'}`}>
                  {chart.hoverDay.regime_status || 'N/A'}
                </span>
              </div>
              <div className="flex justify-between"><span className="text-gray-400">Điểm số</span><span className="font-bold text-japandi-earth">{fmt(chart.hoverDay.regime_score, 2)}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">Model</span><span className="text-gray-700">{chart.hoverDay.active_model || '—'}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">Phục hồi</span><span className="text-gray-700">{chart.hoverDay.recovery_flag ? '✅' : '—'}</span></div>
              <div className="flex justify-between"><span className="text-gray-400">ATR tỷ lệ</span><span className="text-gray-700">{fmt(chart.hoverDay.atr_ratio, 2)}</span></div>
            </div>
          ) : (
            <p className="text-gray-300 italic text-[11px]">Rê chuột vào biểu đồ</p>
          )}
        </div>

        {/* COLUMN C — RISK / EVENTS */}
        <div className="bg-white border border-gray-200 rounded-sm p-3">
          <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">SỰ KIỆN & RỦI RO</p>
          {chart.hoverDay && chart.hoverEvents.length > 0 ? (
            <div className="space-y-1.5 max-h-[140px] overflow-y-auto">
              {chart.hoverEvents.map((ev, i) => {
                const m = EVENT_MARKERS[ev.event_type] || { symbol: '?', color: '#6b7280', label: ev.event_type };
                return (
                  <div key={i} className="border-b border-gray-50 pb-1 last:border-0">
                    <div className="flex items-center gap-1.5">
                      <span style={{ color: m.color }} className="text-[11px] font-bold">{m.symbol}</span>
                      <span className="font-bold text-gray-800 text-[11px]">{m.label}</span>
                      <span className="text-gray-400 text-[10px]">· {ev.model}</span>
                    </div>
                    <p className="text-gray-500 text-[10px] mt-0.5 ml-4">{ev.reason}</p>
                    <p className="text-gray-400 text-[9px] ml-4">Confidence: {fmt(ev.confidence, 2)}</p>
                  </div>
                );
              })}
            </div>
          ) : chart.hoverDay ? (
            <div className="flex items-center justify-center h-full min-h-[60px]">
              <p className="text-gray-300 italic text-[11px] text-center">
                {chart.hoverDay.regime_status === 'RANGING' || chart.hoverDay.regime_status === 'CRISIS'
                  ? 'Hệ thống đứng ngoài — vùng chết an toàn'
                  : 'Chưa có sự kiện trong phiên này'}
              </p>
            </div>
          ) : (
            <p className="text-gray-300 italic text-[11px]">Rê chuột vào biểu đồ</p>
          )}
        </div>
      </div>

      {/* === LEGEND === */}
      <div className="mt-4 flex flex-wrap items-center gap-4 text-[10px] text-gray-500">
        <span className="font-semibold text-japandi-earth">Sự kiện:</span>
        {Object.entries(EVENT_MARKERS).map(([k, v]) => (
          <div key={k} className="flex items-center gap-1">
            <span style={{ color: v.color }} className="font-bold text-[11px]">{v.symbol}</span>
            <span>{v.label}</span>
          </div>
        ))}
        <span className="font-semibold text-japandi-earth ml-2">Trạng thái vĩ mô:</span>
        {Object.entries(REGIME_COLORS).map(([k, v]) => (
          <div key={k} className="flex items-center gap-1">
            <div className="w-3 h-2 rounded-sm" style={{ backgroundColor: v.fill, opacity: 0.4 }} />
            <span>{v.label}</span>
          </div>
        ))}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-4 text-[10px] text-gray-400">
        <span className="font-semibold text-japandi-earth">Vùng chết:</span>
        {Object.entries(DEAD_ZONE_LABELS).map(([k, v]) => (
          <span key={k}>{v}</span>
        ))}
      </div>
    </div>
  );
};

export default ReplayTimelinePage;
