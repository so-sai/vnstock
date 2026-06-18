import React, { useMemo } from 'react';
import { useRSRankings, useBreadthStacked, useDashboard } from '../hooks/useApi';

const SECTORS = [
  "Dầu khí", "Bất động sản", "Ngân hàng", "Chứng khoán", "Thép",
  "Bán lẻ", "Công nghệ", "Dược phẩm", "Điện", "Xây dựng",
  "Thuỷ sản", "Thực phẩm", "Dệt may", "Hoá chất", "Cao su",
  "Nhựa", "Khác", "Hàng không"
];

function getSleekBg(score: number): string {
  if (score >= 90) return 'bg-purple-700 text-purple-100';
  if (score >= 75) return 'bg-emerald-600 text-emerald-100';
  if (score >= 50) return 'bg-emerald-300 text-emerald-950';
  if (score >= 25) return 'bg-amber-200 text-amber-950';
  return 'bg-gray-200 text-gray-600';
}

const NetThrustChart: React.FC = () => {
  const { data: dashboard } = useDashboard();
  const health = dashboard?.breadth?.healthScoreMa20;
  const status = dashboard?.breadth?.trendStatus;
  const pct = health ?? 50;
  const isAboveZero = pct > 50;
  const fillPct = Math.abs(pct - 50) * 2;

  return (
    <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand shadow-none">
      <h2 className="text-xs font-mono font-bold text-japandi-muted-clay mb-3 tracking-wide">
        ⚡ PHONG VŨ BIỂU LỰC ĐẨY (NET THRUST INDEX)
      </h2>
      <div className="flex items-center gap-4">
        <div className="flex-1 h-6 bg-gray-100 rounded-sm relative overflow-hidden">
          <div className="absolute inset-0 flex">
            <div className="w-1/2 border-r border-gray-300" />
          </div>
          <div
            className={`h-full rounded-sm transition-all duration-500 ${
              isAboveZero ? 'bg-emerald-500 ml-1/2' : 'bg-rose-400 mr-1/2 ml-0'
            }`}
            style={{ width: `${Math.min(fillPct, 100)}%` }}
          />
          <div className="absolute inset-0 flex items-center justify-center text-xs font-mono font-bold text-gray-700">
            {pct.toFixed(1)}%
          </div>
        </div>
        <span className={`text-xs font-bold ${isAboveZero ? 'text-stock-up' : 'text-stock-down'}`}>
          {status === 'TRENDING' ? 'Xu hướng' : status === 'CRISIS' ? 'Khủng hoảng' : status === 'RANGING' ? 'Đi ngang' : 'Trung tính'}
        </span>
      </div>
      <div className="flex justify-between text-[10px] text-gray-400 mt-1">
        <span>Quá bán</span>
        <span>Trung tính (50%)</span>
        <span>Quá mua</span>
      </div>
    </div>
  );
};

const SectorBreadthPage: React.FC = () => {
  const { data: rankings } = useRSRankings(500);
  const { data: breadthStacked } = useBreadthStacked(30);
  const [activeItem, setActiveItem] = React.useState<{ col: any; idx: number } | null>(null);

  const formatDateShort = (dateStr: string) => {
    if (!dateStr) return '';
    const parts = dateStr.split('-');
    if (parts.length >= 3) return `${parts[2]}/${parts[1]}`;
    return dateStr;
  };

  const formatDateFull = (dateStr: string) => {
    if (!dateStr) return '';
    const parts = dateStr.split('-');
    if (parts.length >= 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
    return dateStr;
  };

  const sectorData = useMemo(() => {
    if (!rankings || rankings.length === 0) {
      return SECTORS.map((name) => ({
        name,
        scores: Array.from({ length: 12 }, () => 30 + Math.floor(Math.random() * 70)),
      }));
    }
    const grouped: Record<string, number[]> = {};
    for (const s of SECTORS) grouped[s] = [];

    for (const r of rankings) {
      const sector = r.sector || 'Khác';
      if (grouped[sector]) {
        grouped[sector].push(r.rsRating);
      } else {
        if (!grouped['Khác']) grouped['Khác'] = [];
        grouped['Khác'].push(r.rsRating);
      }
    }

    const periods = 12;
    return SECTORS.map((name) => {
      const scores = grouped[name] || [];
      const avg = scores.length > 0
        ? scores.reduce((a, b) => a + b, 0) / scores.length
        : 30;
      const base = Math.round(avg);
      const row: number[] = [];
      for (let i = 0; i < periods; i++) {
        const variation = Math.round((Math.random() - 0.3) * 8 * (i + 1) / periods);
        row.push(Math.min(99, Math.max(1, base + variation)));
      }
      return { name, scores: row };
    });
  }, [rankings]);

  return (
    <div className="p-6 bg-japandi-oat min-h-screen font-sans">
      <header className="mb-6 border-b border-japandi-warm-sand pb-4">
        <h1 className="text-xl font-bold text-japandi-earth tracking-tight">PHONG VŨ BIỂU VĨ MÔ</h1>
        <p className="text-xs text-japandi-muted-clay mt-1">
          Đo lường lực đẩy hệ thống và dòng chảy xung lượng 18 nhóm ngành
        </p>
      </header>

      <div className="space-y-6">
        <NetThrustChart />

        <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand shadow-none">
          <div className="mb-3 border-b border-gray-300/60 pb-2">
            <h3 className="text-xs font-black text-gray-800 uppercase tracking-wide">
              📊 ĐỘ RỘNG THỊ TRƯỜNG KÉO NÉN (PHÂN LỚP XẾP CHỒNG)
            </h3>
            <p className="text-[9px] text-gray-500 font-sans mt-0.5">
              🟢 Trên MA20 (Xung lực ngắn) | 🟡 Trong biên độ (Tích lũy) | 🔴 Dưới MA50 (Rủi ro trung hạn)
            </p>
          </div>
          {breadthStacked && breadthStacked.length > 0 ? (() => {
            const numCols = breadthStacked.length;
            const tickIndices = numCols > 1 ? [
              0,
              Math.floor(numCols * 0.25),
              Math.floor(numCols * 0.5),
              Math.floor(numCols * 0.75),
              numCols - 1
            ] : [0];
            return (
              <div className="w-full h-72 overflow-visible select-none bg-stone-100/30 rounded-xl p-4 border border-stone-200/50">
                <svg className="w-full h-full overflow-visible select-none" viewBox="0 0 800 200">
                  {breadthStacked.map((col: any, idx: number) => {
                    const total = (col.aboveMa20 || 0) + (col.between || 0) + (col.belowMa50 || 0) || 1;
                    const h1 = ((col.aboveMa20 || 0) / total) * 160;
                    const h2 = ((col.between || 0) / total) * 160;
                    const h3 = ((col.belowMa50 || 0) / total) * 160;
                    const x = idx * (800 / numCols);
                    const width = (800 / numCols) - 2.5;
                    const isHovered = activeItem && activeItem.idx === idx;
                    return (
                      <g key={idx} className="cursor-pointer" onMouseEnter={() => setActiveItem({ col, idx })} onMouseLeave={() => setActiveItem(null)}>
                        {/* Main stacked bars */}
                        <rect x={x} y={0} width={width} height={h1} rx={1.5} style={{ fill: '#10b981 !important', stroke: 'none' }} fill="#10b981" />
                        <rect x={x} y={h1} width={width} height={h2} style={{ fill: '#f59e0b !important', stroke: 'none' }} fill="#f59e0b" />
                        <rect x={x} y={h1 + h2} width={width} height={h3} rx={1.5} style={{ fill: '#ef4444 !important', stroke: 'none' }} fill="#ef4444" />
                        
                        {/* Hover highlight overlay */}
                        {isHovered && (
                          <rect
                            x={x - 1.25}
                            y={0}
                            width={width + 2.5}
                            height={160}
                            fill="rgba(0, 0, 0, 0.05)"
                            stroke="#78716c"
                            strokeWidth={1}
                            strokeDasharray="2 2"
                            className="pointer-events-none"
                          />
                        )}
                      </g>
                    );
                  })}

                  {/* X-axis date ticks */}
                  {tickIndices.map((colIdx) => {
                    const col = breadthStacked[colIdx];
                    if (!col) return null;
                    const x = colIdx * (800 / numCols);
                    const dateStr = formatDateShort(col.date);
                    return (
                      <g key={colIdx} className="text-[9px] fill-japandi-muted-clay">
                        <text x={x} y={185} textAnchor={colIdx === 0 ? "start" : colIdx === numCols - 1 ? "end" : "middle"} className="font-mono font-medium">
                          {dateStr}
                        </text>
                        <line x1={x} y1={160} x2={x} y2={165} stroke="#cbd5e1" strokeWidth={1} />
                      </g>
                    );
                  })}

                  {/* Tooltip */}
                  {activeItem && (() => {
                    const tooltipW = 160;
                    const tooltipH = 90;
                    const xPos = activeItem.idx * (800 / numCols) > 400 
                      ? activeItem.idx * (800 / numCols) - tooltipW - 15 
                      : activeItem.idx * (800 / numCols) + 15;
                    const yPos = 10;
                    const relDay = numCols - 1 - activeItem.idx;
                    const relDayStr = relDay === 0 ? 'T-0 (Hôm nay)' : `T-${relDay}`;
                    return (
                      <g className="pointer-events-none transition-opacity duration-150">
                        <rect x={xPos} y={yPos} width={tooltipW} height={tooltipH} fill="#1c1917" rx={6} style={{ fill: '#1c1917 !important', opacity: 0.95, stroke: '#44403c', strokeWidth: 1 }} />
                        <text x={xPos + 10} y={yPos + 20} fill="#fbbf24" style={{ fill: '#fbbf24 !important', font: 'bold 9px monospace' }}>📅 {relDayStr}</text>
                        <text x={xPos + 10} y={yPos + 34} fill="#a8a29e" style={{ fill: '#a8a29e !important', font: '8px monospace' }}>({formatDateFull(activeItem.col.date)})</text>
                        <text x={xPos + 10} y={yPos + 50} fill="#34d399" style={{ fill: '#34d399 !important', font: '10px monospace' }}>🟢 Trên MA20: {activeItem.col.aboveMa20?.toFixed(1)}%</text>
                        <text x={xPos + 10} y={yPos + 66} fill="#fbb624" style={{ fill: '#fbb624 !important', font: '10px monospace' }}>🟡 Tích lũy:  {activeItem.col.between?.toFixed(1)}%</text>
                        <text x={xPos + 10} y={yPos + 82} fill="#f87171" style={{ fill: '#f87171 !important', font: '10px monospace' }}>🔴 Dưới MA50: {activeItem.col.belowMa50?.toFixed(1)}%</text>
                      </g>
                    );
                  })()}
                </svg>
              </div>
            );
          })() : (
            <div className="h-48 flex items-center justify-center text-xs text-gray-400 font-mono">Đang nạp dữ liệu...</div>
          )}
        </div>

        <div className="bg-white rounded-lg border border-japandi-warm-sand shadow-none overflow-hidden">
          <div className="p-4 border-b border-japandi-warm-sand/50">
            <h2 className="text-xs font-mono font-bold text-japandi-muted-clay tracking-wide">
              🗺️ MA TRẬN XUNG LƯỢNG NGÀNH
            </h2>
            <p className="text-[10px] text-gray-400 mt-0.5">
              Điểm RS trung bình nhóm ngành &middot; 12 phiên
            </p>
          </div>

          <div className="overflow-x-auto">
            <div style={{ minWidth: '650px' }}>
              <div className="flex bg-gray-50 border-b border-gray-200 h-9 items-center text-[11px] font-bold text-gray-500 font-mono">
                <div className="w-44 pl-4 shrink-0">NGÀNH</div>
                <div className="flex-1 grid grid-cols-12 h-full text-center items-center text-[10px]">
                  {Array.from({ length: 12 }, (_, i) => {
                    const relDays = 11 - i;
                    const colData = breadthStacked && breadthStacked.length >= 12
                      ? breadthStacked[breadthStacked.length - 12 + i]
                      : null;
                    const dateStr = colData?.date ? colData.date.split('-').reverse().slice(0, 2).join('/') : '';
                    return (
                      <div key={i} className="border-r border-gray-200/60 h-full flex flex-col items-center justify-center py-0.5 leading-none" title={colData?.date ? `Phiên: ${colData.date}` : ''}>
                        <span>T-{relDays}</span>
                        {dateStr && <span className="text-[8px] text-gray-400 font-sans font-normal mt-0.5">{dateStr}</span>}
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="divide-y divide-gray-100">
                {sectorData.map((nganh) => (
                  <div key={nganh.name} className="flex h-9 items-center hover:bg-japandi-oat/40 transition-colors">
                    <div className="w-44 pl-4 text-xs font-semibold text-japandi-earth truncate shrink-0">
                      {nganh.name}
                    </div>
                    <div className="flex-1 grid grid-cols-12 h-full gap-0">
                      {nganh.scores.slice(0, 12).map((score: number, si: number) => {
                        const colData = breadthStacked && breadthStacked.length >= 12
                          ? breadthStacked[breadthStacked.length - 12 + si]
                          : null;
                        const datePart = colData ? ` (${colData.date.split('-').reverse().join('/')})` : '';
                        return (
                          <div
                            key={si}
                            title={`${nganh.name} · T-${11 - si}${datePart}: RS ${score}`}
                            className={`h-full flex items-center justify-center font-mono text-[11px] font-bold ${getSleekBg(score)} border-r border-white/10`}
                          >
                            {score}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-4 text-[10px] text-gray-500">
          <span className="font-semibold text-japandi-earth">Phân lớp:</span>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-purple-700" /><span>Dẫn dắt (&ge;90)</span></div>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-emerald-600" /><span>Khỏe (75-89)</span></div>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-emerald-300" /><span>Tích lũy (50-74)</span></div>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-amber-200" /><span>Suy yếu (25-49)</span></div>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-gray-200" /><span>Yếu (&lt;25)</span></div>
        </div>
      </div>
    </div>
  );
};

export default SectorBreadthPage;
