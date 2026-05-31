/**
 * HOOK: useIPOSignal — Lấy IPO Signal từ Backend
 * ============================================================================
 * 
 * Sử dụng: 
 *   const { data, isLoading, error } = useIPOSignal();
 * 
 * Auto-refetch: 60 giây
 * Caching: 30 giây
 */

import { useQuery } from '@tanstack/react-query';

interface IPOSignalData {
  symbol: string;
  listing_date: string;
  market_cap_billion: number;
  traffic_light: 'XANH' | 'VANG' | 'DO';
  valuation_risk_score: number;
  capital_absorption_trend: 'TANG_MANH' | 'ON_DINH' | 'GIAM';
  secondary_market_pressure: number;
  rotation_risk: 'CAO' | 'TRUNG_BINH' | 'THAP';
  midcap_smallcap_pressure: number;
  liquidity_regime: 'DONG_TIEN_MO_RONG' | 'TANG_GIAN' | 'THOAI_LUI';
  regime_modifier: number;
  days_until_decay: number;
  action_command: {
    primaryAction: string;
    riskLevel: 'THAP' | 'TRUNG_BINH' | 'CAO';
    marginSetting: number;
    sectorWatch: string[];
    interpretation: string;
    updatedAt: string;
  };
}

interface IPOHUDResponse {
  ipo_signal: IPOSignalData;
  updated_at: string;
}

export const useIPOSignal = () => {
  return useQuery<IPOHUDResponse>({
    queryKey: ['ipoSignal'],
    queryFn: async () => {
      const response = await fetch('/api/intelligence/ipo-signal/');
      if (!response.ok) {
        throw new Error(`IPO Signal API Error: ${response.statusText}`);
      }
      return response.json();
    },
    refetchInterval: 60 * 1000,  // Auto-refresh mỗi 60 giây
    staleTime: 30 * 1000,        // Data fresh trong 30 giây
    retry: 2,                     // Retry 2 lần nếu fail
    enabled: true,                // Luôn enable
  });
};

export const useIPOSignalHistory = (days: number = 30) => {
  return useQuery<any[]>({
    queryKey: ['ipoSignalHistory', days],
    queryFn: async () => {
      const response = await fetch(`/api/intelligence/ipo-signal/history/?days=${days}`);
      if (!response.ok) {
        throw new Error(`IPO Signal History API Error`);
      }
      return response.json();
    },
    staleTime: 5 * 60 * 1000,  // Cache 5 phút
    refetchInterval: 5 * 60 * 1000,
  });
};

export type { IPOSignalData, IPOHUDResponse };
