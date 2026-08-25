import type {
  MacroStatus, DiamondCandidate, DashboardResponse, BreadthData,
  RegimeHistory, RSRanking, PortfolioSummary, BacktestResult,
  StressTestResult, PositionInput, XRayData, HeatmapItem, BreadthStacked,
  ObservatorySummary, RiskPathPoint,
  WatchlistPins, AIRecommendationsResponse,
} from '../types/interfaces';

const API_BASE = import.meta.env.DEV ? '/api' : 'http://localhost:17039/api';

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, signal ? { signal } : undefined);
  if (!res.ok) {
    const errorText = await res.text().catch(() => '');
    // Backend error envelope: {"error": "...", "message": "..."}. Chỉ hiển thị
    // message thân thiện — cấm dump raw JSON ra UI (checklist Patch B:
    // clean localization, non-technical error surface).
    let friendly = res.statusText || `HTTP ${res.status}`;
    try {
      const parsed = JSON.parse(errorText);
      if (parsed?.message) friendly = parsed.message;
    } catch {
      /* không phải JSON — giữ statusText */
    }
    throw new Error(friendly);
  }
  return res.json();
}

export const api = {
  getMacro: async (): Promise<MacroStatus> => {
    return fetchJson<MacroStatus>(`${API_BASE}/macro/`);
  },

  getMacroHistory: async (limit = 90): Promise<RegimeHistory[]> => {
    return fetchJson<RegimeHistory[]>(`${API_BASE}/macro/history?limit=${limit}`);
  },

  getScreener: async (topN = 50): Promise<DiamondCandidate[]> => {
    return fetchJson<DiamondCandidate[]>(`${API_BASE}/screener/?top_n=${topN}`);
  },

  getRSRankings: async (topN = 100): Promise<RSRanking[]> => {
    return fetchJson<RSRanking[]>(`${API_BASE}/screener/rankings?top_n=${topN}`);
  },

  getDashboard: async (): Promise<DashboardResponse> => {
    return fetchJson<DashboardResponse>(`${API_BASE}/models/dashboard`);
  },

  getBreadth: async (): Promise<BreadthData> => {
    return fetchJson<BreadthData>(`${API_BASE}/breadth/`);
  },

  getBreadthHistory: async (limit = 60): Promise<RegimeHistory[]> => {
    return fetchJson<RegimeHistory[]>(`${API_BASE}/breadth/history?limit=${limit}`);
  },

  getPortfolio: async (): Promise<PortfolioSummary> => {
    return fetchJson<PortfolioSummary>(`${API_BASE}/portfolio/`);
  },

  getObservatorySummary: async (): Promise<ObservatorySummary> => {
    return fetchJson<ObservatorySummary>(`${API_BASE}/portfolio/observatory/summary`);
  },

  getObservatoryRiskPath: async (days = 30): Promise<RiskPathPoint[]> => {
    return fetchJson<RiskPathPoint[]>(`${API_BASE}/portfolio/observatory/risk-path?days=${days}`);
  },

  addPosition: async (pos: PositionInput): Promise<PortfolioSummary> => {
    const res = await fetch(`${API_BASE}/portfolio/position`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: pos.symbol,
        quantity: pos.quantity,
        entry_price: pos.entryPrice,
        fee_paid: pos.feePaid ?? 0.0015,
      }),
    });
    if (!res.ok) throw new Error(`Failed to add position: ${res.statusText}`);
    return res.json();
  },

  removePosition: async (symbol: string): Promise<PortfolioSummary> => {
    const res = await fetch(`${API_BASE}/portfolio/position/${symbol}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`Failed to remove position: ${res.statusText}`);
    return res.json();
  },

  updatePosition: async (symbol: string, updates: { quantity?: number; entry_price?: number }): Promise<PortfolioSummary> => {
    const res = await fetch(`${API_BASE}/portfolio/position/${symbol}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error(`Failed to update position: ${res.statusText}`);
    return res.json();
  },

  updateCash: async (amount: number): Promise<PortfolioSummary> => {
    const res = await fetch(`${API_BASE}/portfolio/cash`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount }),
    });
    if (!res.ok) throw new Error(`Failed to update cash: ${res.statusText}`);
    return res.json();
  },

  getBacktest: async (
    model = 'A',
    startDate = '2023-01-01',
    endDate = '2026-04-17',
  ): Promise<BacktestResult> => {
    return fetchJson<BacktestResult>(
      `${API_BASE}/backtest/?model=${model}&start_date=${startDate}&end_date=${endDate}`,
    );
  },

  getStressTest: async (startDate = '2022-01-01', endDate = '2023-06-30'): Promise<StressTestResult> => {
    return fetchJson<StressTestResult>(`${API_BASE}/backtest/stress-test?start_date=${startDate}&end_date=${endDate}`);
  },

  getXRay: async (symbol: string, timeframe = 'D'): Promise<XRayData> => {
    return fetchJson<XRayData>(`${API_BASE}/xray/${symbol}?timeframe=${timeframe}`);
  },

  getHeatmap: async (topN = 50): Promise<HeatmapItem[]> => {
    return fetchJson<HeatmapItem[]>(`${API_BASE}/screener/heatmap?top_n=${topN}`);
  },

  getBreadthStacked: async (limit = 60): Promise<BreadthStacked[]> => {
    return fetchJson<BreadthStacked[]>(`${API_BASE}/breadth/stacked-history?limit=${limit}`);
  },

  getLiveSummary: async (): Promise<any> => {
    return fetchJson<any>(`${API_BASE}/intelligence/live-summary`);
  },

  getCoach: async (): Promise<any> => {
    return fetchJson<any>(`${API_BASE}/intelligence/coach`);
  },

  getOpportunities: async (topN = 5): Promise<any> => {
    return fetchJson<any>(`${API_BASE}/intelligence/opportunities?top_n=${topN}`);
  },

  getScenario: async (scenario = 'drop_5pct'): Promise<any> => {
    return fetchJson<any>(`${API_BASE}/intelligence/scenario?scenario=${scenario}`);
  },

  getPositionNarrative: async (symbol: string): Promise<any> => {
    return fetchJson<any>(`${API_BASE}/intelligence/position-narrative/${symbol}`);
  },

  // --- Watchlist API (Layer 1: User Pins, Layer 2: AI Recommendations) ---

  getWatchlistPins: async (): Promise<WatchlistPins> => {
    return fetchJson<WatchlistPins>(`${API_BASE}/watchlist/pins`);
  },

  addWatchlistPin: async (symbol: string): Promise<any> => {
    const res = await fetch(`${API_BASE}/watchlist/pin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol }),
    });
    if (!res.ok) throw new Error(`Failed to pin ${symbol}: ${res.statusText}`);
    return res.json();
  },

  removeWatchlistPin: async (symbol: string): Promise<any> => {
    const res = await fetch(`${API_BASE}/watchlist/pin/${symbol}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`Failed to unpin ${symbol}: ${res.statusText}`);
    return res.json();
  },

  checkWatchlistPin: async (symbol: string): Promise<{ symbol: string; pinned: boolean }> => {
    return fetchJson(`${API_BASE}/watchlist/is-pinned/${symbol}`);
  },

  getAIRecommendations: async (): Promise<AIRecommendationsResponse> => {
    return fetchJson<AIRecommendationsResponse>(`${API_BASE}/watchlist/recommendations`);
  },

  getCombinedWatchlist: async (): Promise<any> => {
    return fetchJson(`${API_BASE}/watchlist/combined`);
  },

  get: async <T>(url: string, init?: { signal?: AbortSignal }): Promise<T> => {
    return fetchJson<T>(`${API_BASE}${url.startsWith('/') ? url : `/${url}`}`, init?.signal);
  },

  post: async <T>(url: string, opts?: { body?: string }): Promise<T> => {
    const res = await fetch(`${API_BASE}${url.startsWith('/') ? url : `/${url}`}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: opts?.body,
    });
    if (!res.ok) throw new Error(`POST ${url} failed: ${res.statusText}`);
    return res.json();
  },

  // --- Gold Macro API (Phase 14) ---

  getGoldRegime: async (): Promise<{ regime: Record<string, any>; cognition: Record<string, any> }> => {
    return fetchJson(`${API_BASE}/v1/gold/regime`);
  },

  getGoldPrices: async (): Promise<Record<string, any>> => {
    return fetchJson(`${API_BASE}/v1/gold/`);
  },

  getWeeklyReport: async (): Promise<Record<string, any>> => {
    return fetchJson(`${API_BASE}/v1/weekly/`);
  },

  searchSymbols: async (q: string, limit = 8): Promise<{ query: string; count: number; results: { symbol: string; icb_name2: string | null; icb_name3: string | null; icb_name4: string | null }[] }> => {
    return fetchJson(`${API_BASE}/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  },
};
