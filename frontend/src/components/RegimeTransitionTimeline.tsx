import React from 'react';

interface RegimeTransitionTimelineProps {
  timeline?: Array<{
    date: string;
    regime_score?: number;
    regimeScore?: number;
    status?: string;
    breadth_pct?: number;
    breadthPct?: number;
  }>;
}

const colorMap: Record<string, string> = {
  TRENDING: 'bg-emerald-500 hover:bg-emerald-600',
  CRISIS: 'bg-rose-500 hover:bg-rose-600',
};

const RegimeTransitionTimeline: React.FC<RegimeTransitionTimelineProps> = ({ timeline }) => {
  if (!timeline || timeline.length === 0) {
    return (
      <div className="p-4 bg-white/60 backdrop-blur-md rounded-sm border border-gray-200 text-center">
        <p className="text-xs text-gray-400 font-mono">Chưa có dữ liệu lịch sử trạng thái</p>
      </div>
    );
  }

  const recent = timeline.slice(-30).reverse();
  const regimeScore = (d: any) => d.regime_score ?? d.regimeScore ?? 0;

  const flipCount = recent.reduce((acc, curr, i) => {
    if (i === 0) return acc;
    const prevScore = regimeScore(recent[i - 1]);
    const currScore = regimeScore(curr);
    const prevRegime = prevScore >= 0.7 ? 'T' : prevScore < 0.35 ? 'C' : 'R';
    const currRegime = currScore >= 0.7 ? 'T' : currScore < 0.35 ? 'C' : 'R';
    return prevRegime !== currRegime ? acc + 1 : acc;
  }, 0);

  return (
    <div className="p-4 bg-white/60 backdrop-blur-md rounded-sm border border-gray-200">
      <div className="flex items-center justify-between mb-2">
        <p className="text-[10px] font-mono font-bold text-gray-400 uppercase tracking-wider">
          Trục dòng chảy lịch sử vĩ mô (30 phiên)
        </p>
        <p className="text-[9px] text-gray-400 font-mono">
          Đảo chiều: <span className="font-bold text-gray-600">{flipCount}</span>
        </p>
      </div>

      <div className="w-full flex h-4 gap-0.5 rounded-sm overflow-hidden">
        {recent.map((entry, idx) => {
          const score = regimeScore(entry);
          const status = entry.status;
          let colorClass = 'bg-amber-400 hover:bg-amber-500';
          if (status === 'TRENDING' || score >= 0.7) colorClass = colorMap.TRENDING;
          else if (status === 'CRISIS' || score < 0.35) colorClass = colorMap.CRISIS;
          const breadth = entry.breadth_pct ?? entry.breadthPct;
          return (
            <div
              key={idx}
              className={`flex-1 h-full cursor-pointer transition-colors ${colorClass}`}
              title={`${entry.date} | ${status || 'N/A'} (${score.toFixed(2)})${breadth !== undefined ? ` · R${Number(breadth).toFixed(0)}%` : ''}`}
            />
          );
        })}
      </div>

      <div className="flex justify-between text-[9px] font-mono text-gray-400 mt-1.5">
        <span>{recent[0]?.date}</span>
        <div className="flex gap-4">
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-sm bg-emerald-500" /> Tăng tốc</span>
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-sm bg-amber-400" /> Đi ngang</span>
          <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-sm bg-rose-500" /> Rủi ro</span>
        </div>
        <span>{recent[recent.length - 1]?.date}</span>
      </div>
    </div>
  );
};

export default RegimeTransitionTimeline;
