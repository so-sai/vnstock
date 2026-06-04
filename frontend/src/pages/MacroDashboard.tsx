import React from 'react';
import { 
  Card, 
  Text, 
  Metric, 
  Grid, 
  CategoryBar, 
  Flex,
  BadgeDelta,
  Title,
} from '@tremor/react';
import { useDashboard, useBreadthStacked } from '../hooks/useApi';
import { swuc } from '../lib/swuc';
import ErrorBoundary from '../components/ErrorBoundary';
import { CardSkeleton, ChartSkeleton } from '../components/Skeletons';

const MacroDashboard: React.FC = () => {
  const { data: dashboard, isLoading, error } = useDashboard();
  const { data: breadthStacked } = useBreadthStacked(60);
  const [activeItem, setActiveItem] = React.useState<{ col: (typeof breadthStacked extends (infer T)[] | undefined ? T : never); idx: number } | null>(null);

  if (error) return <div className="p-8 text-stock-down">Lỗi tải dữ liệu vĩ mô: {error.message}</div>;
  if (!dashboard) return null;
  if (!dashboard.macro || !dashboard.breadth) return null;

  const { macro, breadth, systemMessage, regimeScore, goldPrice, btcPrice, usdVnd, goldScenarios } = dashboard;

  const riskColor = macro.riskLevel === 'Emerald' ? 'emerald' : macro.riskLevel === 'Amber' ? 'yellow' : 'rose';
  const riskLabel = macro.riskLevel === 'Emerald' ? 'Ổn định' : macro.riskLevel === 'Amber' ? 'Thận trọng' : 'Khủng hoảng';

  const goldRegimeBg: Record<string, string> = {
    RISK_OFF: 'bg-orange-100 text-orange-800',
    DEFENSIVE: 'bg-yellow-100 text-yellow-800',
    NEUTRAL: 'bg-gray-100 text-gray-600',
    RISK_ON: 'bg-green-100 text-green-800',
  };
  const goldRegimeLabel: Record<string, string> = {
    RISK_OFF: 'Áp lực phòng thủ',
    DEFENSIVE: 'Phòng thủ nhẹ',
    NEUTRAL: 'Trung tính',
    RISK_ON: 'Chấp nhận rủi ro',
  };

  const trendMap: Record<string, string> = { Bullish: 'Tăng', Bearish: 'Giảm', Neutral: 'Đi ngang' };
  const trendLabel = trendMap[breadth.trendStatus] || breadth.trendStatus;

  return (
    <div className="p-8 bg-japandi-oat min-h-screen">
      <div className="mb-8">
        <Title className="text-japandi-earth text-3xl font-bold">Nhịp đập Vĩ mô</Title>
        <Text className="text-japandi-earth/60">{systemMessage?.title ?? systemMessage}</Text>
        <div className="flex items-center gap-2 mt-1">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
          <p className="text-[10px] text-japandi-muted-clay font-mono tracking-wider uppercase">
            DỮ LIỆU PHIÊN: {breadth?.updatedAt ? new Date(breadth.updatedAt).toLocaleDateString('vi-VN') : '21/05/2026'} | SỔ CÁI ĐÃ KHÓA SẠCH
          </p>
        </div>
      </div>

      <ErrorBoundary>
        <Card className="mb-8 bg-japandi-warm-sand border-none shadow-sm">
          <Flex>
            <Text className="text-japandi-earth font-semibold uppercase tracking-wider">Chỉ số Regime</Text>
            <BadgeDelta deltaType={macro.riskLevel === 'Emerald' ? 'increase' : 'decrease'} className={`bg-japandi-${riskColor === 'rose' ? 'rust' : riskColor === 'yellow' ? 'muted-clay' : 'moss'}/20 text-japandi-${riskColor === 'rose' ? 'rust' : riskColor === 'yellow' ? 'earth' : 'moss'}`}>
              {riskLabel}
            </BadgeDelta>
          </Flex>
          {isLoading ? (
            <div className="animate-pulse h-8 bg-japandi-oat/50 rounded mt-2" />
          ) : (
            <Metric className="text-japandi-earth mb-4">
              {regimeScore?.toFixed(2) ?? 'N/A'} — {trendLabel}
            </Metric>
          )}
          <CategoryBar
            values={[25, 25, 25, 25]}
            colors={["emerald", "yellow", "orange", "rose"]}
            markerValue={isLoading ? 0 : (regimeScore ?? 0) * 100}
            className="mt-3"
          />
          <div className="mt-4 grid grid-cols-3 gap-4 text-sm">
            <div>
              <Text className="text-japandi-muted-clay">Độ rộng (MA20)</Text>
              {isLoading ? (
                <div className="animate-pulse h-6 bg-japandi-oat/50 rounded mt-1 w-16" />
              ) : (
                <Metric className="text-japandi-earth">{breadth.healthScoreMa20?.toFixed(1) ?? '0.0'}%</Metric>
              )}
            </div>
            <div>
              <Text className="text-japandi-muted-clay">ADX</Text>
              {isLoading ? (
                <div className="animate-pulse h-6 bg-japandi-oat/50 rounded mt-1 w-12" />
              ) : (
                <Metric className="text-japandi-earth">{macro.adx?.toFixed(1) ?? 'N/A'}</Metric>
              )}
            </div>
            <div>
              <Text className="text-japandi-muted-clay">ATR Ratio</Text>
              {isLoading ? (
                <div className="animate-pulse h-6 bg-japandi-oat/50 rounded mt-1 w-12" />
              ) : (
                <Metric className="text-japandi-earth">{macro.atrRatio?.toFixed(2) ?? 'N/A'}</Metric>
              )}
            </div>
          </div>
        </Card>
      </ErrorBoundary>

      <ErrorBoundary>
        {breadthStacked && breadthStacked.length > 0 && (
          <Card className="mb-8 bg-japandi-warm-sand border-none shadow-sm">
            <div className="mb-3 border-b border-gray-300/60 pb-2">
              <Title className="text-xs font-black text-gray-800 uppercase tracking-wide">
                📊 ĐỘ RỘNG THỊ TRƯỜNG KÉO NÉN (PHÂN LỚP XẾP CHỒNG)
              </Title>
              <p className="text-[9px] text-gray-500 font-sans mt-0.5">
                🟢 Trên MA20 (Xung lực ngắn) | 🟡 Trong biên độ (Tích lũy) | 🔴 Dưới MA50 (Rủi ro trung hạn)
              </p>
            </div>
            {isLoading ? (
              <ChartSkeleton />
            ) : (
              <svg className="w-full h-48 overflow-visible select-none">
                {breadthStacked.map((col, idx) => {
                  const total = (col.aboveMa20 || 0) + (col.between || 0) + (col.belowMa50 || 0) || 1;
                  const h1 = ((col.aboveMa20 || 0) / total) * 150;
                  const h2 = ((col.between || 0) / total) * 150;
                  const h3 = ((col.belowMa50 || 0) / total) * 150;
                  const strokeWidth = 6;
                  return (
                    <g key={idx} className="cursor-pointer" onMouseEnter={() => setActiveItem({ col, idx })} onMouseLeave={() => setActiveItem(null)}>
                      <rect x={idx * strokeWidth} y={0} width={strokeWidth - 1} height={h1} style={{ fill: '#10b981 !important', stroke: 'none' }} fill="#10b981" />
                      <rect x={idx * strokeWidth} y={h1} width={strokeWidth - 1} height={h2} style={{ fill: '#f59e0b !important', stroke: 'none' }} fill="#f59e0b" />
                      <rect x={idx * strokeWidth} y={h1 + h2} width={strokeWidth - 1} height={h3} style={{ fill: '#ef4444 !important', stroke: 'none' }} fill="#ef4444" />
                    </g>
                  );
                })}
                {activeItem && (() => {
                  const tooltipW = 140;
                  const xPos = activeItem.idx * 6 > 300 ? activeItem.idx * 6 - tooltipW - 15 : activeItem.idx * 6 + 15;
                  return (
                    <g className="pointer-events-none transition-opacity duration-150">
                      <rect x={xPos} y={10} width={tooltipW} height={80} fill="#111827" rx={4} style={{ fill: '#111827 !important', opacity: 0.95, stroke: 'none' }} />
                      <text x={xPos + 10} y={30} fill="#fbbf24" style={{ fill: '#fbbf24 !important', font: 'bold 9px monospace' }}>📅 Phiên: {activeItem.col.date}</text>
                      <text x={xPos + 10} y={48} fill="#34d399" style={{ fill: '#34d399 !important', font: '10px monospace' }}>🟢 Trên MA20: {activeItem.col.aboveMa20?.toFixed(1)}%</text>
                      <text x={xPos + 10} y={64} fill="#fbb624" style={{ fill: '#fbb624 !important', font: '10px monospace' }}>🟡 Tích lũy:  {activeItem.col.between?.toFixed(1)}%</text>
                      <text x={xPos + 10} y={80} fill="#f87171" style={{ fill: '#f87171 !important', font: '10px monospace' }}>🔴 Dưới MA50: {activeItem.col.belowMa50?.toFixed(1)}%</text>
                    </g>
                  );
                })()}
              </svg>
            )}
          </Card>
        )}
      </ErrorBoundary>

      <ErrorBoundary>
        <Grid numItemsLg={3} className="gap-6">
          {isLoading ? (
            <CardSkeleton count={6} />
          ) : (
            <>
              <Card className={`${swuc('METRIC', 'macro')} border-none shadow-sm p-6`}>
                <Text className="text-japandi-muted-clay">USD/CNH (Tỷ giá offshore)</Text>
                <Metric className="text-japandi-earth">{macro.usdCnh?.toFixed(4)}</Metric>
                <Flex className="mt-4">
                  <Text className="text-xs text-japandi-muted-clay">Nhân dân tệ offshore</Text>
                </Flex>
              </Card>

              <Card className={`${swuc('METRIC', 'macro')} border-none shadow-sm p-6`}>
                <Text className="text-japandi-muted-clay">USD/CNY (Tỷ giá onshore)</Text>
                <Metric className="text-japandi-earth">{macro.usdCny?.toFixed(4)}</Metric>
                <Flex className="mt-4">
                  <Text className="text-xs text-japandi-muted-clay">Nhân dân tệ trên bờ</Text>
                </Flex>
              </Card>

              <Card className={`${swuc('METRIC', 'macro')} border-none shadow-sm p-6`}>
                <Text className="text-japandi-muted-clay">Chỉ số DXY</Text>
                <Metric className="text-japandi-earth">{macro.dxyIndex?.toFixed(2)}</Metric>
                <Flex className="mt-4">
                  <Text className="text-xs text-japandi-muted-clay">Sức mạnh USD</Text>
                </Flex>
              </Card>

              <Card className={`${swuc('METRIC', 'macro')} border-none shadow-sm p-6`}>
                <Text className="text-japandi-muted-clay">Lãi suất Interbank O/N</Text>
                <Metric className="text-japandi-earth">{macro.interbankRate?.toFixed(2)}%</Metric>
                <Flex className="mt-4">
                  <Text className="text-xs text-japandi-muted-clay">Lãi suất VND qua đêm</Text>
                </Flex>
              </Card>

              <Card className={`${swuc('METRIC', 'macro')} border-none shadow-sm p-6`}>
                <div className="flex items-center justify-between mb-2">
                  <Text className="text-japandi-muted-clay">🟡 Vàng & Phòng thủ</Text>
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${goldRegimeBg[macro.goldRegime ?? 'NEUTRAL'] ?? 'bg-gray-100 text-gray-600'}`}>
                    {goldRegimeLabel[macro.goldRegime ?? 'NEUTRAL'] ?? 'Trung tính'}
                  </span>
                </div>
                <Metric className="text-japandi-earth">${goldPrice?.toFixed(2) ?? 'N/A'}</Metric>
                <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <Text className="text-japandi-muted-clay">Velocity</Text>
                    <span className="font-mono font-semibold">{(macro.goldVelocity ?? 0).toFixed(2)}</span>
                  </div>
                  <div>
                    <Text className="text-japandi-muted-clay">Spread</Text>
                    <span className="font-mono font-semibold">{(macro.goldSpreadPressure ?? 0).toFixed(2)}</span>
                  </div>
                  <div>
                    <Text className="text-japandi-muted-clay">Bias</Text>
                    <span className="font-mono font-semibold">{macro.goldMacroBias ?? 'N/A'}</span>
                  </div>
                </div>
                {goldScenarios && goldScenarios.length > 0 && (
                  <div className="mt-2 border-t border-gray-200 pt-2">
                    {goldScenarios.map((s, i) => (
                      <p key={i} className="text-[10px] text-japandi-muted-clay leading-tight mb-0.5">• {s}</p>
                    ))}
                  </div>
                )}
              </Card>

              <Card className={`${swuc('METRIC', 'macro')} border-none shadow-sm p-6`}>
                <Text className="text-japandi-muted-clay">BTC/USD</Text>
                <Metric className="text-japandi-earth">${btcPrice?.toLocaleString() ?? 'N/A'}</Metric>
                <Flex className="mt-4">
                  <Text className="text-xs text-japandi-muted-clay">Tiền mã hóa</Text>
                </Flex>
              </Card>

              <Card className={`${swuc('METRIC', 'macro')} border-none shadow-sm p-6`}>
                <Text className="text-japandi-muted-clay">USD/VND</Text>
                <Metric className="text-japandi-earth">{usdVnd?.toLocaleString() ?? 'N/A'}</Metric>
                <Flex className="mt-4">
                  <Text className="text-xs text-japandi-muted-clay">Tỷ giá hối đoái</Text>
                </Flex>
              </Card>
            </>
          )}
        </Grid>
      </ErrorBoundary>

      <ErrorBoundary>
        <div className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-6">
          <Card className={`${swuc('LEADERSHIP_CHANGE', 'macro')} border-none shadow-sm`}>
            <Title className="text-japandi-earth">Động thái NHNN</Title>
            <Text className="mt-2 text-japandi-earth/80">
              Lập trường hiện tại: <span className="font-bold text-japandi-moss">{macro.sbvAction}</span>
            </Text>
            <Text className="mt-4 text-sm text-japandi-earth/60 italic">
              "Ngân hàng Nhà nước đang duy trì lập trường {macro.sbvAction.toLowerCase()} thanh khoản."
            </Text>
          </Card>

          <Card className={`${swuc('RANK_JUMP', 'macro')} border-none shadow-sm`}>
            <Title className="text-japandi-earth">Độ rộng Thị trường</Title>
            <div className="mt-4 grid grid-cols-3 gap-4">
              <div>
                <Text className="text-japandi-muted-clay text-xs">Mã tăng</Text>
                <Metric className="text-stock-up">{breadth.advancers ?? 0}</Metric>
              </div>
              <div>
                <Text className="text-japandi-muted-clay text-xs">Mã giảm</Text>
                <Metric className="text-stock-down">{breadth.decliners ?? 0}</Metric>
              </div>
              <div>
                <Text className="text-japandi-muted-clay text-xs">Tổng cộng</Text>
                <Metric className="text-japandi-earth">{breadth.totalActive ?? 0}</Metric>
              </div>
            </div>
          </Card>
        </div>
      </ErrorBoundary>
    </div>
  );
};

export default MacroDashboard;
