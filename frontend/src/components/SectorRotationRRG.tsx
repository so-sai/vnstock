import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

interface SectorData {
  sector: string;
  flow_score: number;
  momentum: number;
  return_20d: number;
  volatility_20d: number;
  phase: string;
}

interface SectorResponse {
  rotation_regime: string;
  flow_alignment_pct: number;
  sectors: SectorData[];
}

const phaseColors: Record<string, string> = {
  EXPANDING: '#16a34a',
  EARLY_ACCEL: '#eab308',
  CONTRACTING: '#f97316',
  CRISIS: '#dc2626',
};

const SectorRotationRRG: React.FC = () => {
  const { data, isLoading, error } = useQuery<SectorResponse>({
    queryKey: ['sectorRotation'],
    queryFn: () => api.get<SectorResponse>('/api/v1/flow/sector'),
    refetchInterval: 60000,
    staleTime: 30000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96 text-xs text-gray-400 font-mono">
        ĐANG TẢI MA TRẬN NGÀNH...
      </div>
    );
  }

  if (error || !data || !data.sectors || data.sectors.length === 0) {
    return (
      <div className="flex items-center justify-center h-96 text-xs text-gray-400 font-mono">
        KHÔNG CÓ DỮ LIỆU LUÂN CHUYỂN NGÀNH
      </div>
    );
  }

  const sectors = data.sectors.map((s) => ({
    ...s,
    flow_score: typeof s.flow_score === 'number' && !Number.isNaN(s.flow_score) ? s.flow_score : 50,
    return_20d: typeof s.return_20d === 'number' && !Number.isNaN(s.return_20d) ? s.return_20d : 0,
    volatility_20d: typeof s.volatility_20d === 'number' && !Number.isNaN(s.volatility_20d) ? s.volatility_20d : 10,
  }));
  const padding = 40;
  const size = 360;
  const cx = padding;
  const cy = padding;
  const w = size - padding * 2;
  const h = size - padding * 2;

  const scores = sectors.map((s) => s.flow_score);
  const returns = sectors.map((s) => s.return_20d);
  const maxX = Math.max(...scores, 60);
  const maxY = Math.max(...returns, 5);
  const minX = Math.min(...scores, 30);
  const minY = Math.min(...returns, -5);
  const rangeX = maxX - minX || 1;
  const rangeY = maxY - minY || 1;

  const scaleX = (v: number) => cx + ((v - minX) / rangeX) * w;
  const scaleY = (v: number) => cy + h - ((v - minY) / rangeY) * h;
  const midX = scaleX((maxX + minX) / 2);
  const midY = scaleY((maxY + minY) / 2);

  return (
    <div className="w-full">
      <svg viewBox={`0 0 ${size} ${size}`} className="w-full h-auto" style={{ maxHeight: 360 }}>
        <rect x="0" y="0" width={size} height={size} fill="#fafaf9" rx="4" />

        <line x1={cx} y1={midY} x2={cx + w} y2={midY} stroke="#e5e7eb" strokeWidth="1" />
        <line x1={midX} y1={cy} x2={midX} y2={cy + h} stroke="#e5e7eb" strokeWidth="1" />

        <text x={cx + 6} y={cy + 14} fontSize="10" fontFamily="monospace" fill="#16a34a" fontWeight="bold">
          DẪN DẮT
        </text>
        <text x={cx + w - 50} y={cy + 14} fontSize="10" fontFamily="monospace" fill="#eab308" fontWeight="bold">
          CẢI THIỆN
        </text>
        <text x={cx + 6} y={cy + h - 6} fontSize="10" fontFamily="monospace" fill="#f97316" fontWeight="bold">
          TỤT HẬU
        </text>
        <text x={cx + w - 50} y={cy + h - 6} fontSize="10" fontFamily="monospace" fill="#dc2626" fontWeight="bold">
          SUY YẾU
        </text>

        {sectors.map((s, i) => {
          const bx = scaleX(s.flow_score);
          const by = scaleY(s.return_20d);
          const r = Math.max(6, Math.min(20, Math.abs(s.volatility_20d) / 2 + 6));
          return (
            <g key={i}>
              <circle cx={bx} cy={by} r={r} fill={phaseColors[s.phase] || '#999'} opacity={0.6} stroke="#fff" strokeWidth="1.5" />
              <text x={bx} y={by + 3} textAnchor="middle" fontSize="7" fontFamily="monospace" fill="#fff" fontWeight="bold" pointerEvents="none">
                {s.sector.length > 4 ? s.sector.slice(0, 4) : s.sector}
              </text>
            </g>
          );
        })}
      </svg>

      <div className="flex justify-between items-center mt-1.5 px-1">
        <span className="text-[8px] text-gray-400 font-mono">Sức mạnh dòng tiền (RS) →</span>
        <div className="flex gap-3 text-[8px] font-mono text-gray-400">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-[#16a34a]" /> Mở rộng</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-[#eab308]" /> Tăng tốc</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-[#f97316]" /> Co hẹp</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-[#dc2626]" /> Khủng hoảng</span>
        </div>
      </div>
    </div>
  );
};

export default SectorRotationRRG;
