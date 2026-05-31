import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { TrendingDown, TrendingUp, AlertTriangle } from 'lucide-react';

const scenarios = [
  { value: 'drop_5pct', label: '📉 Giảm 5%', icon: <TrendingDown size={14} /> },
  { value: 'drop_10pct', label: '📉 Giảm 10%', icon: <TrendingDown size={14} /> },
  { value: 'surge_3pct', label: '📈 Tăng 3%', icon: <TrendingUp size={14} /> },
];

const ScenarioSimulation: React.FC = () => {
  const [selected, setSelected] = useState('drop_5pct');

  const { data, isLoading } = useQuery({
    queryKey: ['scenario', selected],
    queryFn: () => api.getScenario(selected),
    refetchInterval: 120000,
  });

  return (
    <div className="bg-white/60 border border-japandi-muted-clay/30 rounded-lg overflow-hidden">
      <div className="px-4 py-2 bg-japandi-warm-sand/50 border-b border-japandi-muted-clay/20">
        <span className="text-[10px] font-mono tracking-wider text-japandi-earth/70">KỊCH BẢN GIẢ LẬP</span>
      </div>

      <div className="p-3 space-y-2">
        <div className="flex gap-1.5">
          {scenarios.map((s) => (
            <button
              key={s.value}
              onClick={() => setSelected(s.value)}
              className={`flex-1 flex items-center justify-center gap-1 px-2 py-1.5 rounded text-[10px] font-mono font-bold transition-all ${
                selected === s.value
                  ? 'bg-japandi-earth text-white'
                  : 'bg-japandi-warm-sand/60 text-japandi-earth/70 hover:bg-japandi-warm-sand'
              }`}
            >
              {s.icon}
              {s.label}
            </button>
          ))}
        </div>

        {isLoading ? (
          <div className="h-16 bg-japandi-muted-clay/10 rounded animate-pulse" />
        ) : data ? (
          <div className="space-y-2">
            <div className="grid grid-cols-3 gap-2">
              <div className="bg-japandi-warm-sand/40 rounded p-2 text-center">
                <div className="text-[9px] font-mono text-japandi-muted-clay/60">HEAT HIỆN TẠI</div>
                <div className="text-sm font-bold font-mono text-japandi-earth">{data.current_heat}%</div>
              </div>
              <div className="bg-japandi-warm-sand/40 rounded p-2 text-center">
                <div className="text-[9px] font-mono text-japandi-muted-clay/60">HEAT DỰ KIẾN</div>
                <div className={`text-sm font-bold font-mono ${
                  data.projected_heat >= 7 ? 'text-rose-600' : data.projected_heat >= 4 ? 'text-amber-600' : 'text-emerald-600'
                }`}>
                  {data.projected_heat}%
                </div>
              </div>
              <div className="bg-japandi-warm-sand/40 rounded p-2 text-center">
                <div className="text-[9px] font-mono text-japandi-muted-clay/60">RỦI RO</div>
                <div className={`text-sm font-bold font-mono ${
                  data.projected_risk === 'LOCKED' ? 'text-red-700' : data.projected_risk === 'STRESS' ? 'text-rose-600' : data.projected_risk === 'CAUTION' ? 'text-amber-600' : 'text-emerald-600'
                }`}>
                  {data.projected_risk}
                </div>
              </div>
            </div>

            {data.estimated_loss_vnd > 0 && (
              <div className="flex items-center gap-2 bg-rose-50 border border-rose-200 rounded p-2">
                <AlertTriangle size={14} className="text-rose-600 shrink-0" />
                <div>
                  <div className="text-[10px] font-mono text-rose-800">
                    Lỗ dự kiến: {data.estimated_loss_vnd >= 1e9
                      ? `${(data.estimated_loss_vnd / 1e9).toFixed(1)} tỷ`
                      : `${(data.estimated_loss_vnd / 1e6).toFixed(0)} triệu`
                    } ({data.estimated_loss_pct}%)
                  </div>
                  <div className="text-[9px] text-rose-600/70 font-mono">{data.advice}</div>
                </div>
              </div>
            )}

            {data.last_dd_pct > 0 && (
              <div className="text-[9px] font-mono text-japandi-muted-clay/50 text-center">
                Lần giảm gần nhất: {data.last_dd_pct}% · {data.has_historical_precedent ? 'Có tiền lệ' : 'Chưa từng xảy ra'}
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
};

export default ScenarioSimulation;
