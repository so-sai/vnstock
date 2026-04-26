export interface MacroStatus {
  usdCnh: number;
  copperPrice: number;
  dxyIndex: number;
  interbankRate: number;
  sbvAction: string;
  riskLevel: string;
}

export interface MarketBreadth {
  healthScoreMa20: number;
  healthScoreMa50: number;
  trendStatus: string;
  updatedAt: string;
}

export interface DiamondCandidate {
  symbol: string;
  price: number;
  changePercent: number;
  return6m: number;
  signalV1: string;
  volumeRatio: number;
}

export interface DashboardResponse {
  macro: MacroStatus;
  breadth: MarketBreadth;
  topLeaders: DiamondCandidate[];
  shadowCashPercent: number;
  systemMessage: string;
}
