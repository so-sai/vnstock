import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { TrendingUp, TrendingDown, Activity, RotateCw, Zap, Shield } from 'lucide-react';

const LiveSummaryBar: React.FC = () => {
  const { data, isLoading } = useQuery({
    queryKey: ['liveSummary'],
    queryFn: () => api.getLiveSummary(),
    refetchInterval: 60000,
  });

  if (isLoading || !data) {
    return (
      <div className="h-8 bg-japandi-warm-sand/20 border border-japandi-muted-clay/20 rounded-lg flex items-center px-3">
        <span className="text-japandi-muted-clay text-[10px] font-mono">Đang tổng hợp...</span>
      </div>
    );
  }

  const regimeColors: Record<string, string> = {
    TRENDING: 'text-emerald-600', RANGING: 'text-amber-600', CRISIS: 'text-rose-600',
  };
  const liqColors: Record<string, string> = {
    EXPANDING: 'text-emerald-600', NEUTRAL: 'text-amber-600', CONTRACTING: 'text-rose-600',
  };
  const breakoutColors: Record<string, string> = {
    HIGH_BREAKOUT_ACTIVITY: 'text-emerald-600',
    MODERATE_BREAKOUT_ACTIVITY: 'text-amber-600',
    LOW_BREAKOUT_ACTIVITY: 'text-zinc-500',
  };
  const actionColors: Record<string, string> = {
    ENTER: 'text-emerald-600 font-bold', HOLD: 'text-amber-600',
    REDUCE: 'text-orange-600', EXIT: 'text-rose-600', STAND_DOWN: 'text-zinc-500',
  };
  const riskColors: Record<string, string> = {
    SAFE: 'text-emerald-600', CAUTION: 'text-amber-600',
    STRESS: 'text-rose-600', LOCKED: 'text-red-700 font-black',
  };

  return (
    <div className="flex items-center gap-4 h-8 px-3 bg-white/40 border border-japandi-muted-clay/30 rounded-lg text-[10px] font-mono overflow-x-auto">
      <span className="text-japandi-earth/50 tracking-wider">LIVE</span>

      <span className="text-japandi-muted-clay">|</span>
      <span className="flex items-center gap-1">
        <Activity size={11} className={regimeColors[data.regime] || ''} />
        <span className={regimeColors[data.regime] || ''}>{data.regime}</span>
      </span>

      <span className="text-japandi-muted-clay">|</span>
      <span className={`${actionColors[data.decision?.action] || ''} flex items-center gap-1`}>
        {data.decision?.action || '---'}
        <span className="text-japandi-muted-clay/60">({data.decision?.confidence})</span>
      </span>
      <span className={riskColors[data.decision?.risk] || ''}>
        {data.decision?.risk}
      </span>
      {data.decision?.constraint !== 'ALLOWED' && (
        <span className="text-rose-600 font-bold">● {data.decision?.constraint}</span>
      )}

      <span className="text-japandi-muted-clay">|</span>
      <span className="flex items-center gap-1">
        <Zap size={11} className={liqColors[data.liquidity_phase] || ''} />
        <span className={liqColors[data.liquidity_phase] || ''}>{data.liquidity_phase}</span>
      </span>

      <span className="flex items-center gap-1">
        <RotateCw size={11} className="text-japandi-muted-clay/60" />
        <span className="text-japandi-muted-clay/80">{data.rotation_regime?.slice(0, 8)}</span>
      </span>

      <span className="text-japandi-muted-clay">|</span>
      <span className={breakoutColors[data.breakout_context] || ''}>
        {data.breakout_context === 'HIGH_BREAKOUT_ACTIVITY' ? '🔥' : data.breakout_context === 'LOW_BREAKOUT_ACTIVITY' ? '💤' : ''}
        {data.positions_count} positions
      </span>
    </div>
  );
};

export default LiveSummaryBar;
