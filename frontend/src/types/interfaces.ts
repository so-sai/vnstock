export interface MacroStatus {
  usdCnh: number;
  usdCny: number;
  copperPrice: number;
  dxyIndex: number;
  interbankRate?: number | null;
  sbvAction: string;
  riskLevel: string;
  regimeScore?: number;
  regimeStatus?: string;
  breadthPct?: number;
  breadthStd10d?: number;
  breadthMomentum?: number;
  ma50Slope?: number;
  adx?: number;
  atrRatio?: number;
  goldPrice?: number;
  btcPrice?: number;
  usdVnd?: number;
  goldRegime?: string;
  goldVelocity?: number;
  goldSpreadPressure?: number;
  goldMacroBias?: string;
  vgb10y?: number | null;
  vgb10yDataQuality?: string;
  vgb10yBpsChange?: string | null;
  vgb10yStatusLabel?: string | null;
  vgb10yRawBps?: number | null;
  us2yYield?: number | null;
  us5yYield?: number | null;
  us30yYield?: number | null;
  spread10y2y?: number | null;
  spread30y10y?: number | null;
  yieldCurveInversion?: string;
  tipPrice?: number | null;
  usRealYield?: number | null;
  breakevenInflation?: number | null;
}

export interface MarketBreadth {
  healthScoreMa20: number;
  healthScoreMa50: number;
  trendStatus: string;
  updatedAt: string;
  nh10Count?: number;
  advancers?: number;
  decliners?: number;
  totalActive?: number;
}

export interface DiamondCandidate {
  symbol: string;
  price: number;
  changePercent: number;
  return6m: number;
  signalV1: string;
  volumeRatio: number;
  rsRating?: number;
  sector?: string;
}

export interface SystemMessage {
  title: string;
  advise: string;
  color: 'rose' | 'emerald' | 'amber' | 'gray';
}

export interface ModelBPick {
  symbol: string;
  distance_from_ma50: number;
  rsi: number;
  rank_score: number;
  context_source: string;
  price?: number;
  avg_vol_20d?: number;
}

export interface DashboardResponse {
  macro: MacroStatus;
  breadth: MarketBreadth;
  topLeaders: DiamondCandidate[];
  shadowCashPercent: number;
  systemMessage: SystemMessage;
  regimeHistory?: RegimeHistory[];
  regimeScore?: number;
  goldPrice?: number;
  btcPrice?: number;
  usdVnd?: number;
  active_model?: string;
  consensus?: string;
  confidence?: number;
  breadth_velocity?: number;
  model_b?: {
    picks_count: number;
    top_picks: ModelBPick[];
    context: string;
    breadth_pct: number;
    breadth_std: number;
    breadth_velocity: number;
  };
  goldScenarios?: string[];
  silverPrice?: number;
  goldSilverRatio?: number;
}

export interface RegimeHistory {
  date: string;
  regime_score?: number;
  regimeScore?: number;
  status?: string;
  breadth_pct?: number;
  breadthPct?: number;
  trend_score?: number;
  vol_score?: number;
  breadthVelocity?: number;
}

export interface BreadthData {
  date: string;
  totalActive: number;
  advancers: number;
  decliners: number;
  unchanged: number;
  healthScoreMa20: number;
  nh10Count: number;
  nh10Consistency3d: number;
  trend: string;
  error?: string;
}

export interface RSRanking {
  symbol: string;
  rsRating: number;
  rsRaw: number;
  price: number;
  rvol: number;
  avgVol20d: number;
  change1y: number;
  sector: string;
  distanceFromMa50?: number;
}

export interface PositionDetail {
  symbol: string;
  quantity: number;
  entryPrice: number;
  currentPrice: number | null;
  pnlPercent: number | null;
  pnlVnd: number | null;
  marketValue: number | null;
  alert: string | null;
}

export interface PortfolioSummary {
  cash: number;
  totalCost: number;
  totalMarketValue: number;
  totalNav: number;
  totalPnlPercent: number;
  cashPercent: number;
  positions: PositionDetail[];
  alerts: Array<{ symbol: string; message: string }>;
  stopLossThreshold: number;
  updatedAt: string;
  error?: string;
}

export interface PositionInput {
  symbol: string;
  quantity: number;
  entryPrice: number;
  feePaid?: number;
}

export interface CashInput {
  amount: number;
}

export interface BacktestPick {
  symbol: string;
  return1y: number;
  sharpe: number;
  maxDrawdown: number;
  volatility: number;
}

export interface BacktestResult {
  model: string;
  startDate: string;
  endDate: string;
  totalSymbols: number;
  topPicks: BacktestPick[];
  portfolioStats: {
    avgReturn: number;
    avgSharpe: number;
    avgMaxDrawdown: number;
    avgVolatility: number;
  };
  equityCurve: Array<{ date: string; value: number }>;
  error?: string;
}

export interface HeatmapItem {
  symbol: string;
  rsRating: number;
  change1y: number;
  rsHistory: number[];
}

export interface BreadthStacked {
  date: string;
  aboveMa20: number;
  between: number;
  belowMa50: number;
}

export interface OHLCVCandle {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface XRayData {
  symbol: string;
  price: number;
  changePercent: number;
  rsRating: number;
  rsRaw: number;
  rvol: number;
  volumeRatio: number | null;
  change1y: number;
  sector: string;
  rsi14: number | null;
  zScore: number | null;
  foreign10dAcc: number;
  aboveMa50: boolean | null;
  aboveMa200: boolean | null;
  volumeSpike: boolean;
  regime: {
    status: string;
    score: number;
  };
  ohlcvHistory?: OHLCVCandle[];
}

export interface HoldingLifecycle {
  id: number;
  symbol: string;
  status: 'ENTERED' | 'SCALE_IN' | 'REDUCED' | 'EXIT_PENDING';
  regime_at_entry: string;
  entry_date: string;
  avg_cost: number;
  current_size: number;
  stop_loss_price: number;
  conviction_score: number;
  initial_risk_pct: number;
  thesis_source: string;
  thesis_notes: string;
}

export interface PortfolioTelemetry {
  total_nav: number;
  cash_balance: number;
  net_exposure_pct: number;
  portfolio_heat_pct: number;
  rolling_hit_rate_10d: number;
  current_dd_pct: number;
  risk_dampener_factor: number;
}

export interface ObservatorySummary {
  open_positions: number;
  total_shares: number;
  market_value_vnd: number;
  portfolio_heat_pct: number;
  net_exposure_pct: number;
  total_nav: number;
  telemetry: PortfolioTelemetry | null;
  holdings: HoldingLifecycle[];
}

export interface WatchlistPins {
  symbols: string[];
  count: number;
}

export interface AIRecommendation {
  symbol: string;
  tier: string;
  tier_vn: string;
  conviction: number;
  rationale: string;
  sector: string;
  total_score: number;
  flow_alignment_score: number;
  flow_alignment_label: string;
  regime_strength: number;
  breadth_score: number;
  momentum_score: number;
  liquidity_score: number;
  entry_suggestion: string;
}

export interface AITierGroup {
  label: string;
  symbols: string[];
  details: AIRecommendation[];
}

export interface AIRecommendationsResponse {
  date: string;
  regime: { status: string; regime_score: number; breadth_pct: number };
  recommendations: {
    core: AITierGroup;
    rotation: AITierGroup;
    opportunity: AITierGroup;
  };
  summary: {
    total_scanned: number;
    core_count: number;
    rotation_count: number;
    opportunity_count: number;
    top_core: string[];
    top_rotation: string[];
    top_opportunity: string[];
  };
}

export interface RiskPathPoint {
  timestamp: string;
  total_exposure: number;
  portfolio_heat: number;
  throttle_factor: number;
  dampener_avg: number;
  drawdown_pct: number;
  rolling_vol: number;
}

export interface AlternativeAction {
  action: string;
  score: number;
  reason_blocked: string;
  blocked_by: string;
}

export interface RationaleNode {
  label: string;
  detail: string;
  score_contribution: number | null;
  children: RationaleNode[];
}

export interface DecisionVectorV2 {
  action: string;
  confidence: number;
  risk_state: string;
  constraint: string;
  suggested_size_mult: number;
  reason: string;
  alternatives: AlternativeAction[];
  rationale_tree: RationaleNode[];
  decision_id: string;
  override_state: string;
  override_action: string | null;
  override_reason: string | null;
  calibrated_weights: Record<string, number>;
}

export interface StressTestResult {
  period: string;
  totalSymbols: number;
  avgMaxDrawdown: number;
  worstDrawdown: number;
  recoveryRate: number;
  worstPerformers?: Array<{
    symbol: string;
    maxDrawdown2022: number;
    recovered: boolean;
  }>;
  error?: string;
}
