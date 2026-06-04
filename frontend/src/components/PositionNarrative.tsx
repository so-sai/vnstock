import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';


const PositionNarrative: React.FC<{ symbol: string }> = ({ symbol }) => {
  const { data, isLoading } = useQuery({
    queryKey: ['positionNarrative', symbol],
    queryFn: () => api.getPositionNarrative(symbol),
    refetchInterval: 60000,
  });

  if (isLoading) {
    return (
      <div className="h-16 bg-japandi-warm-sand/20 rounded-lg animate-pulse flex items-center px-3">
        <span className="text-xs text-japandi-muted-clay font-mono">Đang phân tích {symbol}...</span>
      </div>
    );
  }

  if (!data || data.status === 'NOT_FOUND') {
    return (
      <div className="text-xs text-japandi-muted-clay font-mono italic">Không tìm thấy vị thế {symbol}</div>
    );
  }

  const pnlColor = data.pnl_pct > 5 ? 'text-emerald-600' : data.pnl_pct > 0 ? 'text-emerald-500' : data.pnl_pct > -5 ? 'text-rose-500' : 'text-rose-700 font-bold';
  const slColor = data.distance_to_sl_pct < 2.5 ? 'text-rose-600 font-bold' : data.distance_to_sl_pct < 5 ? 'text-amber-600' : 'text-emerald-600';
  const convictionColor = data.conviction_score >= 0.7 ? 'text-emerald-600' : data.conviction_score >= 0.4 ? 'text-amber-600' : 'text-rose-600';
  const systemColor = data.system_action === 'EXIT' ? 'text-rose-600' : data.system_action === 'REDUCE' ? 'text-orange-600' : data.system_action === 'ENTER' ? 'text-emerald-600' : 'text-japandi-earth/60';

  return (
    <div className="bg-white/60 backdrop-blur-md border border-japandi-muted-clay/30 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-bold text-sm text-japandi-earth">{data.symbol}</span>
        <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
          data.status === 'ENTERED' ? 'bg-emerald-100 text-emerald-700' : data.status === 'REDUCED' ? 'bg-amber-100 text-amber-700' : 'bg-zinc-100 text-zinc-600'
        }`}>
          {data.status}
        </span>
        {data.system_action && (
          <span className={`text-[10px] font-mono ${systemColor}`}>
            Hệ thống: {data.system_action}
          </span>
        )}
      </div>

      <div className="grid grid-cols-4 gap-2 text-center">
        <div>
          <div className="text-[9px] font-mono text-japandi-muted-clay/60">P&L</div>
          <div className={`text-xs font-bold font-mono ${pnlColor}`}>+{data.pnl_pct}%</div>
        </div>
        <div>
          <div className="text-[9px] font-mono text-japandi-muted-clay/60">ĐẾN SL</div>
          <div className={`text-xs font-bold font-mono ${slColor}`}>{data.distance_to_sl_pct}%</div>
        </div>
        <div>
          <div className="text-[9px] font-mono text-japandi-muted-clay/60">CONVICTION</div>
          <div className={`text-xs font-bold font-mono ${convictionColor}`}>{data.conviction_score.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-[9px] font-mono text-japandi-muted-clay/60">RISK</div>
          <div className={`text-xs font-bold font-mono ${
            data.system_risk === 'SAFE' ? 'text-emerald-600' : data.system_risk === 'CAUTION' ? 'text-amber-600' : 'text-rose-600'
          }`}>
            {data.system_risk}
          </div>
        </div>
      </div>

      <div className="text-[11px] text-japandi-earth/80 font-sans leading-relaxed border-t border-japandi-muted-clay/15 pt-2">
        {data.verdict}
      </div>

      {data.narrative && (
        <div className="text-[10px] text-japandi-muted-clay/70 font-mono italic border-t border-japandi-muted-clay/15 pt-1">
          {data.narrative}
        </div>
      )}
    </div>
  );
};

export default PositionNarrative;
