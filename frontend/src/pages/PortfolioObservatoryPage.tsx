import React from 'react';
import { useObservatorySummary, useObservatoryRiskPath } from '../hooks/useApi';
import { Card, Text, Metric, Title, Badge } from '@tremor/react';
import { AlertTriangle, ShieldCheck } from 'lucide-react';
import { swuc } from '../lib/swuc';
import ErrorBoundary from '../components/ErrorBoundary';
import { CardSkeleton } from '../components/Skeletons';
import DecisionStripV2 from '../components/DecisionStripV2';

const calculateDistanceToSl = (current: number, sl: number) => {
  if (!current || !sl || current <= sl) return 0;
  return ((current - sl) / current) * 100;
};

const HeatMeter: React.FC<{ value: number; max: number; label: string; color?: string }> = ({ value, max, label, color: _color }) => {
  const pct = Math.min((value / max) * 100, 100);
  const barColor = value >= max * 0.7 ? 'bg-rose-500' : value >= max * 0.4 ? 'bg-amber-500' : 'bg-emerald-500';
  return (
    <div className="bg-japandi-oat/60 p-4 rounded-lg border border-japandi-muted-clay/40 space-y-2">
      <div className="flex justify-between items-center">
        <Text className="text-japandi-earth/70 font-mono text-xs tracking-wider">{label}</Text>
        <span className={`font-mono text-sm font-bold ${value >= max * 0.7 ? 'text-stock-down' : 'text-japandi-earth'}`}>
          {value.toFixed(1)}%
        </span>
      </div>
      <div className="w-full h-2 bg-japandi-warm-sand rounded-full overflow-hidden">
        <div className={`h-full transition-all duration-500 ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
};

const DampenerBadge: React.FC<{ factor: number }> = ({ factor }) => {
  if (factor >= 0.9) return <Badge color="emerald" className="bg-emerald-100 text-emerald-800 border-none font-mono">{factor.toFixed(2)}x</Badge>;
  if (factor >= 0.5) return <Badge color="amber" className="bg-amber-100 text-amber-800 border-none font-mono">{factor.toFixed(2)}x</Badge>;
  return <Badge color="rose" className="bg-rose-100 text-rose-800 border-none font-mono animate-pulse">{factor.toFixed(2)}x</Badge>;
};

const statusVi: Record<string, string> = {
  ENTERED: 'Đã vào', SCALE_IN: 'Tăng thêm', REDUCED: 'Đã giảm', EXIT_PENDING: 'Chờ thoát',
};
const StatusBadge: React.FC<{ status: string }> = ({ status }) => {
  const styles: Record<string, string> = {
    ENTERED: 'bg-emerald-100 text-emerald-800 border-emerald-200',
    SCALE_IN: 'bg-purple-100 text-purple-800 border-purple-200',
    REDUCED: 'bg-amber-100 text-amber-800 border-amber-200',
    EXIT_PENDING: 'bg-rose-100 text-rose-800 border-rose-200',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono border ${styles[status] || 'bg-gray-100 text-gray-800'}`}>
      {statusVi[status] || status.replace('_', ' ')}
    </span>
  );
};

const PortfolioObservatoryPage: React.FC = () => {
  const { data: summary, isLoading, error } = useObservatorySummary();
  const { data: riskPath } = useObservatoryRiskPath(30);

  const latestRisk = riskPath && riskPath.length > 0 ? riskPath[riskPath.length - 1] : null;
  const telemetry = summary?.telemetry;

  return (
    <div className="p-8 bg-japandi-oat min-h-screen select-none">
      <div className="mb-8 border-b border-japandi-muted-clay/40 pb-5">
        <div className="flex items-start justify-between">
          <div>
            <Title className="text-japandi-earth text-2xl font-bold tracking-tight">Đài Quan sát Dòng vốn</Title>
            <Text className="text-japandi-earth/50 text-xs mt-1 font-mono">Đài Quan sát Dòng vốn — Trung tâm Đo lường & Vận hành Danh mục</Text>
          </div>
          <div className="flex items-center gap-3">
            {telemetry && (
              <>
                <DampenerBadge factor={telemetry.risk_dampener_factor} />
                <div className="text-right font-mono">
                  <Text className="text-japandi-earth/50 text-[10px]">TỔNG TÀI SẢN (NAV)</Text>
                  <Text className="text-japandi-earth font-bold text-sm">
                    {summary?.total_nav ? `${(summary.total_nav / 1e6).toFixed(1)}M` : '—'}
                  </Text>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Phase 10.2 — Cognitive Decision Strip (counterfactual + rationale + override) */}
      <ErrorBoundary>
        <DecisionStripV2 />
      </ErrorBoundary>

      <div className="mt-6">
      <ErrorBoundary>
        {isLoading ? (
          <div className="space-y-6">
            <div className="grid grid-cols-3 gap-4">
              <CardSkeleton count={3} />
            </div>
            <CardSkeleton count={1} />
          </div>
        ) : error ? (
          <Card className="bg-rose-50 border border-rose-200 p-6">
            <div className="flex items-center gap-2">
              <AlertTriangle className="text-stock-down" size={20} />
              <Text className="text-stock-down font-medium">Không thể tải dữ liệu: {error.message}</Text>
            </div>
          </Card>
        ) : summary ? (
          <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <HeatMeter value={summary.net_exposure_pct} max={100} label="GIẢI NGÂN THỰC TẾ" />
              <HeatMeter value={summary.portfolio_heat_pct} max={10} label="MỨC NHIỆT RỦI RO" />
              <HeatMeter
                value={telemetry?.current_dd_pct || 0}
                max={5}
                label="MỨC SỤT GIẢM NAV"
                color="rose"
              />
            </div>

            <div className="grid grid-cols-4 gap-3">
              <Card className="bg-japandi-warm-sand/40 border border-japandi-muted-clay/30 shadow-none">
                <Text className="text-japandi-earth/60 text-[10px] font-mono">VỊ THẾ MỞ</Text>
                <Metric className="text-japandi-earth text-lg">{summary.open_positions}</Metric>
              </Card>
              <Card className="bg-japandi-warm-sand/40 border border-japandi-muted-clay/30 shadow-none">
                <Text className="text-japandi-earth/60 text-[10px] font-mono">GIÁ TRỊ GIẢI NGÂN</Text>
                <Metric className="text-japandi-earth text-lg">{summary.market_value_vnd ? `${(summary.market_value_vnd / 1e6).toFixed(1)}M` : '—'}</Metric>
              </Card>
              <Card className="bg-japandi-warm-sand/40 border border-japandi-muted-clay/30 shadow-none">
                <Text className="text-japandi-earth/60 text-[10px] font-mono">TỶ LỆ THẮNG 10 PHIÊN</Text>
                <Metric className="text-japandi-earth text-lg">
                  {telemetry?.rolling_hit_rate_10d != null ? `${telemetry.rolling_hit_rate_10d.toFixed(0)}%` : '—'}
                </Metric>
              </Card>
              <Card className="bg-japandi-warm-sand/40 border border-japandi-muted-clay/30 shadow-none">
                <Text className="text-japandi-earth/60 text-[10px] font-mono">TRẠNG THÁI BỘ GIẢM CHẤN</Text>
                <Metric className="text-japandi-earth text-lg">
                  {latestRisk ? (
                    <span className={latestRisk.throttle_factor === 0 ? 'text-stock-down' : 'text-japandi-earth'}>
                      {latestRisk.throttle_factor === 0 ? 'Khóa' : latestRisk.throttle_factor === 0.5 ? 'Thận trọng' : 'Bình thường'}
                    </span>
                  ) : '—'}
                </Metric>
              </Card>
            </div>

            <div className={`${swuc('POSITION', 'portfolio')} border border-japandi-muted-clay/40 rounded-lg overflow-hidden`}>
              <div className="bg-japandi-warm-sand/50 px-4 py-2 border-b border-japandi-muted-clay/30">
                <Text className="text-japandi-earth/70 text-xs font-mono font-bold tracking-wider">
                  DANH SÁCH VỊ THẾ ĐANG MỞ
                </Text>
              </div>

              {summary.holdings.length === 0 ? (
                <div className="p-8 text-center">
                  <ShieldCheck className="mx-auto text-japandi-muted-clay mb-2" size={32} />
                  <Text className="text-japandi-muted-clay">Không có vị thế mở. Hệ thống đang ở trạng thái an toàn.</Text>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left font-mono text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-japandi-muted-clay/20 text-japandi-earth/50">
                        <th className="px-4 py-3 font-medium">MÃ</th>
                        <th className="px-4 py-3 font-medium">TRẠNG THÁI</th>
                        <th className="px-4 py-3 font-medium">REGIME</th>
                        <th className="px-4 py-3 font-medium text-right">GIÁ VỐN</th>
                        <th className="px-4 py-3 font-medium text-right">RỦI RO</th>
                        <th className="px-4 py-3 font-medium text-right">TIN CẬY</th>
                        <th className="px-4 py-3 font-medium">KHOẢNG SL</th>
                        <th className="px-4 py-3 font-medium">NHẬN ĐỊNH</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-japandi-muted-clay/15">
                      {summary.holdings.map((pos) => {
                        const distSl = calculateDistanceToSl(pos.avg_cost, pos.stop_loss_price);
                        const danger = distSl <= 3.5;
                        return (
                          <tr key={pos.id} className="hover:bg-japandi-oat/60 transition-colors">
                            <td className="px-4 py-3 font-bold text-japandi-earth text-sm">{pos.symbol}</td>
                            <td className="px-4 py-3"><StatusBadge status={pos.status} /></td>
                            <td className="px-4 py-3 text-japandi-earth/60">{pos.regime_at_entry}</td>
                            <td className="px-4 py-3 text-right text-japandi-earth">{pos.avg_cost.toLocaleString()}₫</td>
                            <td className="px-4 py-3 text-right text-stock-down">{pos.initial_risk_pct}%</td>
                            <td className="px-4 py-3 text-right">
                              <span className={pos.conviction_score >= 0.7 ? 'text-emerald-600 font-bold' : 'text-japandi-earth/70'}>
                                {pos.conviction_score.toFixed(2)}
                              </span>
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-2">
                                <div className="flex-1 h-2.5 bg-japandi-warm-sand rounded-full overflow-hidden max-w-[100px]">
                                  <div
                                    className={`h-full rounded-full transition-all duration-300 ${danger ? 'bg-rose-500 animate-pulse' : 'bg-japandi-moss/60'}`}
                                    style={{ width: `${Math.min((distSl / 10) * 100, 100)}%` }}
                                  />
                                </div>
                                <span className={`font-bold text-[11px] ${danger ? 'text-stock-down' : 'text-japandi-earth/60'}`}>
                                  {distSl.toFixed(1)}%
                                </span>
                              </div>
                            </td>
                            <td className="px-4 py-3 text-japandi-earth/50 max-w-[160px] truncate font-sans">
                              {pos.thesis_notes || pos.thesis_source || '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {riskPath && riskPath.length > 1 && (
              <div className={`${swuc('CHART', 'portfolio')} border border-japandi-muted-clay/40 rounded-lg p-4`}>
                <Text className="text-japandi-earth/70 text-xs font-mono font-bold tracking-wider mb-3">
                  ĐƯỜNG CONG RỦI RO DANH MỤC (30 ngày gần nhất)
                </Text>
                <div className="h-20 flex items-end gap-[2px]">
                  {riskPath.map((p, i) => {
                    const h = Math.min((p.portfolio_heat / 10) * 100, 100);
                    return (
                      <div key={i} className="flex-1 flex flex-col justify-end items-center group relative">
                        <div
                          className={`w-full rounded-t transition-all duration-200 hover:opacity-80 ${
                            p.portfolio_heat >= 7 ? 'bg-rose-400' : p.portfolio_heat >= 4 ? 'bg-amber-400' : 'bg-emerald-400'
                          }`}
                          style={{ height: `${h}%` }}
                        />
                      </div>
                    );
                  })}
                </div>
                <div className="flex justify-between mt-1">
                  <Text className="text-japandi-earth/40 text-[9px] font-mono">
                    {riskPath[0]?.timestamp?.slice(0, 10)}
                  </Text>
                  <Text className="text-japandi-earth/40 text-[9px] font-mono">
                    {riskPath[riskPath.length - 1]?.timestamp?.slice(0, 10)}
                  </Text>
                </div>
              </div>
            )}
          </div>
        ) : null}
      </ErrorBoundary>
      </div>
    </div>
  );
};

export default PortfolioObservatoryPage;
