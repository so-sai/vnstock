import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

export function useScreener(topN = 50) {
  return useQuery({
    queryKey: ['screener', topN],
    queryFn: () => api.getScreener(topN),
  });
}

export function useRSRankings(topN = 100) {
  return useQuery({
    queryKey: ['rsRankings', topN],
    queryFn: () => api.getRSRankings(topN),
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: api.getDashboard,
    refetchInterval: 60000,
  });
}

export function usePortfolio() {
  return useQuery({
    queryKey: ['portfolio'],
    queryFn: api.getPortfolio,
  });
}

export function useObservatorySummary() {
  return useQuery({
    queryKey: ['observatorySummary'],
    queryFn: api.getObservatorySummary,
    refetchInterval: 30000,
  });
}

export function useObservatoryRiskPath(days = 30) {
  return useQuery({
    queryKey: ['observatoryRiskPath', days],
    queryFn: () => api.getObservatoryRiskPath(days),
    refetchInterval: 60000,
  });
}

export function useBacktest(model = 'A', startDate = '2025-01-01', endDate = '2026-04-17') {
  return useQuery({
    queryKey: ['backtest', model, startDate, endDate],
    queryFn: () => api.getBacktest(model, startDate, endDate),
    staleTime: 300000,
  });
}

export function useStressTest() {
  return useQuery({
    queryKey: ['stressTest'],
    queryFn: api.getStressTest,
  });
}

export function useHeatmap(topN = 50) {
  return useQuery({
    queryKey: ['heatmap', topN],
    queryFn: () => api.getHeatmap(topN),
    staleTime: 30000,
  });
}

export function useBreadthStacked(limit = 60) {
  return useQuery({
    queryKey: ['breadthStacked', limit],
    queryFn: () => api.getBreadthStacked(limit),
    staleTime: 30000,
  });
}

export function useReplayTimeline(limit = 365) {
  return useQuery({
    queryKey: ['replayTimeline', limit],
    queryFn: () => api.get<{ days: ReplayDay[]; events: ReplayEvent[] }>(`/replay/timeline?limit=${limit}`),
    staleTime: 60000,
  });
}

export type { ReplayDay, ReplayEvent };

interface ReplayDay {
  date: string; open: number | null; high: number | null; low: number | null; close: number | null;
  volume: number | null; ma50: number | null; ma200: number | null;
  regime_score: number | null; regime_status: string | null;
  breadth_pct: number | null; breadth_velocity: number | null;
  atr_ratio: number | null; active_model: string | null;
  recovery_flag: number | null; trend_score: number | null; vol_score: number | null;
}

interface ReplayEvent {
  date: string; event_type: string; model: string;
  reason: string; context: string; confidence: number;
  meta: Record<string, any>;
}

export function useWatchlistPins() {
  return useQuery({
    queryKey: ['watchlistPins'],
    queryFn: api.getWatchlistPins,
    staleTime: 30000,
  });
}

export function useAIRecommendations() {
  return useQuery({
    queryKey: ['aiRecommendations'],
    queryFn: api.getAIRecommendations,
    staleTime: 60000,
  });
}

export function useCombinedWatchlist() {
  return useQuery({
    queryKey: ['combinedWatchlist'],
    queryFn: api.getCombinedWatchlist,
    staleTime: 60000,
  });
}

export function useXRay(symbol: string | null, timeframe = 'D') {
  return useQuery({
    queryKey: ['xray', symbol, timeframe],
    queryFn: () => api.getXRay(symbol!, timeframe),
    enabled: !!symbol,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
}
