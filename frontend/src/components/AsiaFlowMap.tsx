import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

interface LiquidityWave {
  symbol: string;
  sector: string;
  vol_ratio: number;
  vol_accel_20d: number;
  turnover_shock: number;
  retail_chase_score: number;
  wave_strength: string;
}

interface SectorNode {
  sector: string;
  flow_score: number;
  phase: string;
}

interface ChainLink {
  leader: string;
  followers: string[];
}

interface ForeignItem {
  symbol: string;
  net_10d_bn_vnd: number;
}

interface FlowBanner {
  banner: string;
  liquidity_phase: string;
  rotation_regime: string;
}

const CHANNEL_CONFIG = {
  liquidity: {
    label: 'SÓNG THANH KHOẢN',
    color: 'border-cyan-500/30 bg-cyan-500/5',
    textColor: 'text-cyan-600',
    badge: 'bg-cyan-500/10 text-cyan-600 border-cyan-500/20',
    edgeColor: 'stroke-cyan-500/30',
  },
  sector: {
    label: 'XOAY VÒNG NGÀNH',
    color: 'border-amber-500/30 bg-amber-500/5',
    textColor: 'text-amber-600',
    badge: 'bg-amber-500/10 text-amber-600 border-amber-500/20',
    edgeColor: 'stroke-amber-500/30',
  },
  foreign: {
    label: 'KHỐI NGOẠI',
    color: 'border-purple-500/30 bg-purple-500/5',
    textColor: 'text-purple-600',
    badge: 'bg-purple-500/10 text-purple-600 border-purple-500/20',
    edgeColor: 'stroke-purple-500/30',
  },
};

const WaveStrengthBadge: React.FC<{ strength: string; volRatio: number }> = ({ strength, volRatio }) => {
  if (strength === 'SURGE' || volRatio >= 2) return <span className="px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-600 text-[9px] font-bold">BÙNG NỔ</span>;
  if (strength === 'STRONG' || volRatio >= 1.5) return <span className="px-1.5 py-0.5 rounded bg-cyan-400/10 text-cyan-600 text-[9px] font-bold">MẠNH</span>;
  if (volRatio < 0.5) return <span className="px-1.5 py-0.5 rounded bg-zinc-200 text-zinc-500 text-[9px] font-bold">CẠN</span>;
  return <span className="px-1.5 py-0.5 rounded bg-zinc-100 text-zinc-400 text-[9px] font-bold">NORMAL</span>;
};

const FlowBanner: React.FC<{ banner: string; phase: string; rotation: string }> = ({ banner, phase, rotation }) => (
  <div className="w-full bg-gray-900 text-amber-400 text-xs font-mono font-bold px-4 py-2.5 rounded border border-amber-500/20 mb-4 flex items-center gap-2 tracking-wide">
    <span className="flex h-1.5 w-1.5 rounded-full bg-amber-400 animate-ping shrink-0" />
    <span className="truncate">{banner}</span>
    <span className="ml-auto shrink-0 text-[9px] text-zinc-500 uppercase tracking-wider border-l border-zinc-700 pl-3">
      {phase} | {rotation}
    </span>
  </div>
);

export const AsiaFlowMap: React.FC = () => {
  const { data: bannerData } = useQuery<FlowBanner>({
    queryKey: ['flowBanner'],
    queryFn: () => api.get<FlowBanner>('/v1/flow/banner'),
    refetchInterval: 120000,
    retry: 1,
  });

  const { data: liqData, isLoading: liqLoading } = useQuery<{ liquidity_phase: string; waves: LiquidityWave[] }>({
    queryKey: ['flowLiquidity'],
    queryFn: () => api.get<any>('/v1/flow/liquidity'),
    refetchInterval: 120000,
    retry: 1,
  });

  const { data: secData, isLoading: secLoading } = useQuery<{ sectors: SectorNode[]; leader_follower_chains: Record<string, ChainLink> }>({
    queryKey: ['flowSector'],
    queryFn: () => api.get<any>('/v1/flow/sector'),
    refetchInterval: 120000,
    retry: 1,
  });

  const { data: frnData, isLoading: frnLoading } = useQuery<{ total_net_10d_bn_vnd: number; market_pressure: string; top_accumulated: ForeignItem[]; top_distributed: ForeignItem[] }>({
    queryKey: ['flowForeign'],
    queryFn: () => api.get<any>('/v1/flow/foreign'),
    refetchInterval: 120000,
    retry: 1,
  });

  const isLoading = liqLoading || secLoading || frnLoading;

  if (isLoading) {
    return (
      <div className="w-full bg-white/60 backdrop-blur-md border border-gray-200 p-5 rounded-lg">
        <div className="animate-pulse space-y-3">
          <div className="h-8 bg-gray-200 rounded w-full" />
          <div className="grid grid-cols-3 gap-3">
            {[1, 2, 3].map(i => <div key={i} className="h-24 bg-gray-100 rounded" />)}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full bg-white/60 backdrop-blur-md border border-gray-200 p-5 rounded-lg font-sans select-none mb-6">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[11px] font-mono font-bold text-gray-400 uppercase tracking-widest flex items-center gap-2">
          🗺️ BẢN ĐỒ DÒNG CHẢY CHÂU Á
        </h3>
        <div className="flex items-center gap-3 text-[9px] font-mono text-zinc-400">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-cyan-500" /> THANH KHOẢN</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500" /> NGÀNH</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-purple-500" /> NGOẠI</span>
        </div>
      </div>

      {bannerData && <FlowBanner banner={bannerData.banner} phase={bannerData.liquidity_phase} rotation={bannerData.rotation_regime} />}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className={`border rounded-lg p-3 space-y-2 ${CHANNEL_CONFIG.liquidity.color}`}>
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono font-bold uppercase tracking-wider text-cyan-700">{CHANNEL_CONFIG.liquidity.label}</span>
            <span className="text-[9px] font-mono text-cyan-600">{liqData?.liquidity_phase}</span>
          </div>
          {(!liqData?.waves || liqData.waves.length === 0) ? (
            <p className="text-[10px] text-zinc-400 italic">Đang thu thập dữ liệu...</p>
          ) : (
            <div className="space-y-1.5">
              {liqData.waves.slice(0, 6).map((w, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] font-mono">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="font-bold text-zinc-800">{w.symbol}</span>
                    <span className="text-zinc-400 truncate text-[9px]">{w.sector}</span>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span className={`${w.vol_ratio >= 1.5 ? 'text-cyan-600 font-bold' : 'text-zinc-400'}`}>
                      {w.vol_ratio.toFixed(1)}x
                    </span>
                    <WaveStrengthBadge strength={w.wave_strength} volRatio={w.vol_ratio} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className={`border rounded-lg p-3 space-y-2 ${CHANNEL_CONFIG.sector.color}`}>
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono font-bold uppercase tracking-wider text-amber-700">{CHANNEL_CONFIG.sector.label}</span>
            <span className="text-[9px] font-mono text-amber-600">{secData ? `${secData.sectors?.length ?? 0} ngành` : ''}</span>
          </div>
          {(!secData?.sectors || secData.sectors.length === 0) ? (
            <p className="text-[10px] text-zinc-400 italic">Đang thu thập dữ liệu...</p>
          ) : (
            <div className="space-y-2">
              {secData.sectors.slice(0, 4).map((s, i) => {
                const chain = secData.leader_follower_chains?.[s.sector];
                return (
                  <div key={i} className="border-b border-amber-500/10 last:border-0 pb-1.5 last:pb-0">
                    <div className="flex items-center justify-between text-[10px] font-mono">
                      <span className="font-bold text-zinc-800 truncate">{s.sector}</span>
                      <span className={`font-black ${s.flow_score >= 70 ? 'text-amber-600' : 'text-zinc-400'}`}>
                        {s.flow_score}
                      </span>
                    </div>
                    {chain && (
                      <div className="flex items-center gap-1 text-[9px] text-zinc-500 font-mono mt-0.5">
                        <span className="text-amber-600 font-bold">👑 {chain.leader}</span>
                        {chain.followers.length > 0 && (
                          <>
                            <span className="text-zinc-300">→</span>
                            {chain.followers.map((f, fi) => (
                              <span key={fi} className="text-zinc-400">{f}</span>
                            ))}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className={`border rounded-lg p-3 space-y-2 ${CHANNEL_CONFIG.foreign.color}`}>
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-mono font-bold uppercase tracking-wider text-purple-700">{CHANNEL_CONFIG.foreign.label}</span>
            <span className={`text-[9px] font-mono font-bold ${(frnData?.total_net_10d_bn_vnd ?? 0) > 0 ? 'text-purple-600' : 'text-rose-600'}`}>
              {frnData ? `${(frnData.total_net_10d_bn_vnd ?? 0) >= 0 ? '+' : ''}${(frnData.total_net_10d_bn_vnd ?? 0).toFixed(0)} tỷ` : ''}
            </span>
          </div>
          {!frnData ? (
            <p className="text-[10px] text-zinc-400 italic">Đang thu thập dữ liệu...</p>
          ) : (
            <div className="space-y-2">
              <div>
                <p className="text-[9px] font-mono text-zinc-400 uppercase mb-1">HÚT RÒNG</p>
                {frnData.top_accumulated.map((item, i) => (
                  <div key={i} className="flex items-center justify-between text-[10px] font-mono py-0.5">
                    <span className="font-bold text-zinc-800">{item.symbol}</span>
                    <span className="text-purple-600 font-bold">+{item.net_10d_bn_vnd.toFixed(1)}</span>
                  </div>
                ))}
              </div>
              {frnData.top_distributed.length > 0 && frnData.top_distributed[0].net_10d_bn_vnd < 0 && (
                <div>
                  <p className="text-[9px] font-mono text-zinc-400 uppercase mb-1 mt-1">XẢ RÒNG</p>
                  {frnData.top_distributed.map((item, i) => (
                    <div key={i} className="flex items-center justify-between text-[10px] font-mono py-0.5">
                      <span className="font-bold text-zinc-800">{item.symbol}</span>
                      <span className="text-rose-600 font-bold">{item.net_10d_bn_vnd.toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
