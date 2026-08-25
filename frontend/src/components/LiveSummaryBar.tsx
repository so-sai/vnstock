import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { Activity, RotateCw, Zap } from 'lucide-react';

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
    'Xu hướng rõ ràng': 'text-emerald-600', 'Đi ngang': 'text-amber-600',
    'Khủng hoảng': 'text-rose-600', 'Rủi ro cao': 'text-rose-600',
    'Không xác định': 'text-zinc-500',
  };
  const liqColors: Record<string, string> = {
    'Mở rộng': 'text-emerald-600', 'Trung tính': 'text-amber-600', 'Thu hẹp': 'text-rose-600',
  };
  const actionVi: Record<string, string> = { ENTER: 'Mở vị thế', SCALE_IN: 'Tăng', HOLD: 'Giữ', REDUCE: 'Giảm', EXIT: 'Thoát', STAND_DOWN: 'Đứng ngoài' };
  const riskVi: Record<string, string> = { SAFE: 'An toàn', CAUTION: 'Thận trọng', STRESS: 'Căng thẳng', LOCKED: 'Khóa' };
  const constraintVi: Record<string, string> = { ALLOWED: 'Được phép', PARTIAL: 'Giảm 50%', BLOCKED: 'Bị khóa' };
  const breakoutColors: Record<string, string> = {
    'Nhiều phá vỡ': 'text-emerald-600',
    'Ít phá vỡ': 'text-zinc-500',
    'Cao': 'text-emerald-600',
    'Vừa phải': 'text-amber-600',
    'Thấp': 'text-zinc-500',
  };
  const actionColors: Record<string, string> = {
    'Mở vị thế': 'text-emerald-600 font-bold', 'Giữ': 'text-amber-600',
    'Giảm': 'text-orange-600', 'Thoát': 'text-rose-600', 'Đứng ngoài': 'text-zinc-500',
  };
  const riskColors: Record<string, string> = {
    'An toàn': 'text-emerald-600', 'Thận trọng': 'text-amber-600',
    'Căng thẳng': 'text-rose-600', 'Khóa': 'text-red-700 font-black',
  };

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-h-8 px-3 py-1 bg-white/40 backdrop-blur-md border border-japandi-muted-clay/30 rounded-lg text-[10px] font-mono">
      <span className="text-japandi-earth/50 tracking-wider">LIVE</span>

      <span className="text-japandi-muted-clay">|</span>
      <span className="flex items-center gap-1">
        <Activity size={11} className={regimeColors[data.regime] || ''} />
        <span className={regimeColors[data.regime] || ''}>{data.regime}</span>
      </span>

      <span className="text-japandi-muted-clay">|</span>
      <span className={`${actionColors[actionVi[data.decision?.action] || data.decision?.action] || ''} flex items-center gap-1`}>
        {actionVi[data.decision?.action] || data.decision?.action || '---'}
        <span className="text-japandi-muted-clay/60">({data.decision?.confidence})</span>
      </span>
      <span className={riskColors[riskVi[data.decision?.risk] || data.decision?.risk] || ''}>
        {riskVi[data.decision?.risk] || data.decision?.risk}
      </span>
      {(constraintVi[data.decision?.constraint] || data.decision?.constraint) !== 'Được phép' && (
        <span className="text-rose-600 font-bold">● {constraintVi[data.decision?.constraint] || data.decision?.constraint}</span>
      )}

      <span className="text-japandi-muted-clay">|</span>
      <span className="flex items-center gap-1">
        <Zap size={11} className={liqColors[data.liquidity_phase] || ''} />
        <span className={liqColors[data.liquidity_phase] || ''}>{data.liquidity_phase}</span>
      </span>

      <span className="flex items-center gap-1">
        <RotateCw size={11} className="text-japandi-muted-clay/60" />
        <span className="text-japandi-muted-clay/80">{data.rotation_regime ?? ''}</span>
      </span>

      <span className="text-japandi-muted-clay">|</span>
      <span className={breakoutColors[data.breakout_context] || ''}>
        {data.breakout_context === 'Nhiều phá vỡ' ? '🔥' : data.breakout_context === 'Ít phá vỡ' ? '💤' : ''}
        {data.positions_count} vị thế
      </span>
    </div>
  );
};

export default LiveSummaryBar;
