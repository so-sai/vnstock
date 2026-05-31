import React from 'react';
import FlowDecayMiniIndicator from './FlowDecayMiniIndicator';
import AsiaFlowChart from './AsiaFlowChart';
import SectorRotationRRG from './SectorRotationRRG';
import ErrorBoundary from './ErrorBoundary';

interface ObservabilityProps {
  dashboardData: any;
  modelBContext: any;
}

function deriveBlockReasons(context?: string) {
  const reasons = { stability: 0, trend: 0, pullback: 0, liquidity: 0 };
  if (!context) return reasons;
  if (context.includes('PRIMARY_MA200') || context.includes('SECONDARY_STABLE')) {
    return reasons;
  }
  if (context.includes('STD_HIGH')) reasons.stability = 40;
  if (context.includes('SLOPE_TOO_FLAT')) reasons.stability += 15;
  if (context.includes('VNINDEX_BELOW_MA200')) reasons.trend = 25;
  if (context.includes('VNINDEX_BELOW_MA50')) reasons.trend += 10;
  if (context.includes('No breadth expansion') || context.includes('[EMPTY]')) reasons.pullback = 30;
  if (context.includes('LOW_LIQUIDITY')) reasons.liquidity = 15;
  return reasons;
}

const AlphaObservabilityConsole: React.FC<ObservabilityProps> = ({ dashboardData, modelBContext }) => {
  const trendStatus = dashboardData?.breadth?.trendStatus || 'RANGING';
  const breadthPct = dashboardData?.macro?.breadthPct ?? dashboardData?.breadth?.healthScoreMa20 ?? 50;
  const std10D = dashboardData?.macro?.breadthStd10d ?? 0;
  const adx = dashboardData?.macro?.adx ?? 0;
  const atrRatio = dashboardData?.macro?.atrRatio ?? 0;

  const blockReasons = deriveBlockReasons(modelBContext?.context);
  const totalBlock = blockReasons.stability + blockReasons.trend + blockReasons.pullback + blockReasons.liquidity;
  const availability = Math.max(0, 100 - totalBlock);

  const regimeConfig: Record<string, { label: string; bar: string }> = {
    TRENDING: { label: 'CHỦ LỰC / XU HƯỚNG TĂNG', bar: 'bg-emerald-500' },
    RANGING: { label: 'PHỤ / ĐI NGANG', bar: 'bg-amber-400' },
    CRISIS: { label: 'BỊ CHẶN / KHỦNG HOẢNG', bar: 'bg-rose-500' },
  };
  const regime = regimeConfig[trendStatus] || regimeConfig.RANGING;

  return (
    <div className="w-full space-y-3 mb-8">
      <div className="flex flex-wrap items-center gap-3 text-[11px] font-mono text-gray-500 bg-white px-4 py-2 rounded-sm border border-gray-200">
        <span className={`px-2 py-0.5 rounded-sm font-bold text-white ${regime.bar}`}>
          {regime.label}
        </span>
        <span>Độ rộng: <b className="text-gray-900">{Number(breadthPct).toFixed(1)}%</b></span>
        <span>ADX: <b className="text-gray-900">{adx.toFixed(1)}</b></span>
        <span>Std 10D: <b className="text-gray-900">{std10D.toFixed(1)}</b></span>
        <span>ATR: <b className="text-gray-900">{atrRatio.toFixed(2)}</b></span>
        <span className="text-gray-300">|</span>
        <span>Khả dụng B: <b className="text-gray-900">{availability}%</b></span>
        <span className="ml-auto"><FlowDecayMiniIndicator /></span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ErrorBoundary fallback={<div className="bg-white border border-rose-200 rounded-sm p-8 text-center"><p className="text-xs text-rose-500 font-mono">⚠ LỖI: Asia Flow Chart không thể khởi tạo</p></div>}>
          <div className="bg-white border border-gray-200 rounded-sm p-4">
            <div className="flex items-center gap-2 mb-3">
              <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider">Asia Flow</h3>
              <span className="bg-gray-800 text-amber-400 text-[8px] px-1.5 py-0.5 font-mono uppercase tracking-wider">Kết quả đã thanh trùng</span>
            </div>
            <AsiaFlowChart />
          </div>
        </ErrorBoundary>

        <ErrorBoundary fallback={<div className="bg-white border border-rose-200 rounded-sm p-8 text-center"><p className="text-xs text-rose-500 font-mono">⚠ LỖI: Ma trận Luân chuyển không thể khởi tạo</p></div>}>
          <div className="bg-white border border-gray-200 rounded-sm p-4">
            <div className="flex items-center gap-2 mb-3">
              <h3 className="text-xs font-bold text-gray-900 uppercase tracking-wider">Ma trận Luân chuyển Dòng tiền</h3>
            </div>
            <SectorRotationRRG />
          </div>
        </ErrorBoundary>
      </div>
    </div>
  );
};

export default AlphaObservabilityConsole;
