import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Lightbulb, AlertTriangle, Shield, TrendingUp, ChevronRight } from 'lucide-react';

const toneConfig: Record<string, { icon: React.ReactNode; color: string; bg: string }> = {
  WARNING: { icon: <AlertTriangle size={16} />, color: 'text-amber-800', bg: 'bg-amber-50 border-amber-200' },
  CRITICAL: { icon: <AlertTriangle size={16} className="animate-pulse" />, color: 'text-rose-800', bg: 'bg-rose-50 border-rose-200' },
  OPPORTUNITY: { icon: <TrendingUp size={16} />, color: 'text-emerald-800', bg: 'bg-emerald-50 border-emerald-200' },
  CAUTION: { icon: <Shield size={16} />, color: 'text-orange-800', bg: 'bg-orange-50 border-orange-200' },
  BALANCED: { icon: <Lightbulb size={16} />, color: 'text-blue-800', bg: 'bg-blue-50 border-blue-200' },
  NEUTRAL: { icon: <Lightbulb size={16} />, color: 'text-japandi-earth', bg: 'bg-japandi-warm-sand/50 border-japandi-muted-clay/30' },
};

const PortfolioCoach: React.FC = () => {
  const { data, isLoading } = useQuery({
    queryKey: ['portfolioCoach'],
    queryFn: () => api.getCoach(),
    refetchInterval: 60000,
  });

  if (isLoading || !data) {
    return (
      <div className="h-12 bg-japandi-warm-sand/20 border border-japandi-muted-clay/20 rounded-lg animate-pulse flex items-center px-4">
        <span className="text-japandi-muted-clay text-xs font-mono">Đang tư vấn...</span>
      </div>
    );
  }

  const coach = data.coach || {};
  const tc = toneConfig[coach.tone] || toneConfig.NEUTRAL;

  return (
    <div className={`${tc.bg} border rounded-lg p-4 space-y-2`}>
      <div className="flex items-center gap-2">
        {tc.icon}
        <span className={`text-[10px] font-mono tracking-wider ${tc.color}`}>PORTFOLIO COACH</span>
        <span className={`text-[10px] font-mono ml-auto ${tc.color}`}>
          {data.decision?.action} · {data.decision?.risk}
        </span>
      </div>
      <div className={`text-sm font-bold ${tc.color}`}>{coach.instruction}</div>
      <div className={`text-xs ${tc.color} opacity-80`}>{coach.rationale}</div>
      <div className="flex items-start gap-1.5 pt-1">
        <ChevronRight size={12} className={`mt-0.5 ${tc.color} opacity-60 shrink-0`} />
        <span className={`text-[10px] ${tc.color} opacity-70 font-mono`}>{coach.next_step}</span>
      </div>
      <div className="flex gap-3 pt-1 text-[9px] font-mono text-japandi-muted-clay/60">
        <span>{data.regime}</span>
        <span>{data.positions_count} positions</span>
        <span>Heat {data.portfolio_heat}%</span>
      </div>
    </div>
  );
};

export default PortfolioCoach;
