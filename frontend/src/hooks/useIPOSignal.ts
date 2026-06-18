/**
 * HOOK: useIPOSignal — Lấy IPO Signal từ Backend
 * ============================================================================
 * 
 * Sử dụng: 
 *   const { data, isLoading, error, refetch } = useIPOSignal();
 * 
 * ⚠️  KỶ LUẬT 90 NGÀY — ĐÃ VÔ HIỆU HÓA AUTO-FETCH
 *    Hệ thống ở chế độ OFFLINE mặc định. Chỉ gọi API khi người dùng bấm nút.
 *    Mọi lệnh refetchInterval đã bị cắt để tránh ban IP từ vnstock API.
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
      const response = await fetch(`${import.meta.env.DEV ? '' : 'http://localhost:17039'}/api/intelligence/ipo-signal/`);
      if (!response.ok) {
        throw new Error(`IPO Signal API Error: ${response.statusText}`);
      }
      return response.json();
    },
    // DISABLED: Auto-refresh removed to prevent rate limit ban.
    // Data only loads on manual trigger or component mount (single fetch).
    staleTime: Infinity,        // Dữ liệu không bao giờ stale — chỉ refetch khi user bấm nút
    retry: 1,                   // Chỉ retry 1 lần nếu fail
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
};

export const useIPOSignalHistory = (days: number = 30) => {
  return useQuery<any[]>({
    queryKey: ['ipoSignalHistory', days],
    queryFn: async () => {
      const response = await fetch(`${import.meta.env.DEV ? '' : 'http://localhost:17039'}/api/intelligence/ipo-signal/history/?days=${days}`);
      if (!response.ok) {
        throw new Error(`IPO Signal History API Error`);
      }
      return response.json();
    },
    // DISABLED: Auto-refresh removed — prevents thundering herd.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
};

export type { IPOSignalData, IPOHUDResponse };
