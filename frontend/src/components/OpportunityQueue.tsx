import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { BarChart } from 'lucide-react';

const actionLabels: Record<string, string> = {
  STRONG_BUY: "MUA MẠNH", BUY: "MUA", WATCH: "THEO DÕI", OBSERVE: "QUAN SÁT",
};
const actionColors: Record<string, string> = {
  STRONG_BUY: 'bg-emerald-800 text-emerald-50',
  BUY: 'bg-emerald-600 text-emerald-50',
  WATCH: 'bg-amber-600 text-amber-50',
  OBSERVE: 'bg-zinc-500 text-zinc-50',
};

const breakoutLabels: Record<string, string> = {
  '200D': '200 ngày', '50D': '50 ngày', '20D': '20 ngày', NONE: 'Không',
};
const breakoutColors: Record<string, string> = {
  '200D': 'text-purple-600 font-bold',
  '50D': 'text-blue-600',
  '20D': 'text-teal-600',
  NONE: 'text-japandi-muted-clay',
};

const chaseLabels: Record<string, string> = {
  EXTREME: 'Cao', HIGH: 'Trung bình', MODERATE: 'Thấp',
};
const chaseColors: Record<string, string> = {
  EXTREME: 'text-rose-600', HIGH: 'text-orange-600', MODERATE: 'text-amber-600',
};

const OpportunityQueue: React.FC<{ topN?: number }> = ({ topN = 5 }) => {
  const { data, isLoading } = useQuery({
    queryKey: ['opportunities', topN],
    queryFn: () => api.getOpportunities(topN),
    refetchInterval: 120000,
  });

  if (isLoading || !data) {
    return (
      <div className="bg-japandi-warm-sand/20 border border-japandi-muted-clay/20 rounded-lg p-4">
        <div className="h-4 w-40 bg-japandi-muted-clay/20 rounded animate-pulse mb-3" />
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-10 bg-japandi-muted-clay/10 rounded animate-pulse mb-2" />
        ))}
      </div>
    );
  }

  const opps = data.opportunities || [];

  return (
    <div className="bg-white/60 backdrop-blur-md border border-japandi-muted-clay/30 rounded-lg overflow-hidden">
      <div className="px-4 py-2 bg-japandi-warm-sand/50 border-b border-japandi-muted-clay/20 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart size={14} className="text-japandi-earth/60" />
          <span className="text-[10px] font-mono tracking-wider text-japandi-earth/70">CƠ HỘI HÀNH ĐỘNG</span>
        </div>
        <span className="text-[9px] font-mono text-japandi-muted-clay/50">
          {data.market_context?.breakout_context?.replace(/_/g, ' ') || ''}
        </span>
      </div>

      {opps.length === 0 ? (
        <div className="p-6 text-center text-japandi-muted-clay text-xs font-mono">
          Chưa có cơ hội phù hợp. Đang theo dõi thị trường.
        </div>
      ) : (
        <div className="divide-y divide-japandi-muted-clay/15">
          {opps.map((opp: any, i: number) => (
            <div key={i} className="flex items-center gap-3 px-4 py-2.5 hover:bg-japandi-oat/40 transition-colors">
              <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold font-mono ${actionColors[opp.action] || 'bg-zinc-100'}`}>
                {actionLabels[opp.action] || opp.action?.replace(/_/g, ' ')}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-sm text-japandi-earth">{opp.symbol}</span>
                  <span className={`text-[10px] font-mono ${breakoutColors[opp.breakout_level] || ''}`}>
                    {breakoutLabels[opp.breakout_level] || opp.breakout_level}
                  </span>
                  <span className={`text-[9px] font-mono ${chaseColors[opp.retail_chase] || ''}`}>
                    {chaseLabels[opp.retail_chase] || opp.retail_chase}
                  </span>
                </div>
                <div className="text-[10px] text-japandi-muted-clay font-mono truncate">{opp.reason}</div>
              </div>
              <div className="text-right">
                <div className="text-xs font-bold font-mono text-japandi-earth">{opp.total_score}</div>
                <div className="text-[9px] text-japandi-muted-clay/60 font-mono">{opp.vol_ratio?.toFixed(1)}x vol</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default OpportunityQueue;
