import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

interface DecayedBanner {
  banner: string;
  liquidity_phase_decayed: string;
  rotation_regime_decayed: string;
  persistence_score: number;
  instability_score: number;
  signal_strength: number;
  conflict_flag: boolean;
  confidence_band: string;
}

const FlowDecayMiniIndicator: React.FC = () => {
  const { data, isLoading, error } = useQuery<DecayedBanner>({
    queryKey: ['flowBannerDecayed'],
    queryFn: () => api.get<DecayedBanner>('/v1/flow/banner/decayed'),
    refetchInterval: 60000,
    staleTime: 30000,
    retry: 1,
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-[10px] text-gray-400 font-mono">
        <span className="w-2 h-2 rounded-full bg-gray-300 animate-pulse" />
        ĐANG TẢI
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex items-center gap-2 text-[10px] text-gray-400 font-mono">
        <span className="w-2 h-2 rounded-full bg-gray-300" />
        DỮ LIỆU DÒNG CHẢY KHÔNG KHẢ DỤNG
      </div>
    );
  }

  const hasConflict = data.conflict_flag;
  const persistence = data.persistence_score;
  const confidence = data.confidence_band;

  return (
    <div className="flex items-center gap-2">
      {hasConflict ? (
        <div className="flex items-center gap-1 text-[10px] font-mono" title="Khối ngoại mua ròng ảo nhưng thanh khoản nội địa kiệt quệ — CHẶN LỆNH MUA ĐUỔI">
          <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-pulse" />
          <span className="text-red-400 font-bold tracking-tight">DÒNG TIỀN XUNG ĐỘT</span>
        </div>
      ) : (
        <div className="flex items-center gap-1 text-[10px] font-mono">
          <span className={`w-1.5 h-1.5 rounded-full ${persistence >= 0.7 ? 'bg-emerald-400' : 'bg-amber-400'}`} />
          <span className="text-gray-500">DÒNG TIỀN</span>
        </div>
      )}
      <span className={`px-1 py-0.5 rounded-sm text-[9px] font-mono font-bold ${
        persistence >= 0.7
          ? 'bg-emerald-950/30 text-emerald-400 border border-emerald-900/40'
          : persistence >= 0.5
            ? 'bg-amber-950/30 text-amber-400 border border-amber-900/40'
            : 'bg-rose-950/30 text-rose-400 border border-rose-900/40'
      }`}>
        {(persistence * 100).toFixed(0)}%
      </span>
      <span className="text-[9px] text-gray-400 font-mono hidden sm:inline">
        {confidence}
      </span>
    </div>
  );
};

export default FlowDecayMiniIndicator;
