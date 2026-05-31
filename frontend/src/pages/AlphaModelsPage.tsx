import React, { useState } from 'react';
import { 
  Card, 
  Text, 
  Metric, 
  Grid, 
  Title, 
  Badge,
  Tab, 
  TabGroup, 
  TabList,
  Table,
  TableHead,
  TableRow,
  TableHeaderCell,
  TableBody,
  TableCell,
  Flex,
} from '@tremor/react';
import HeatmapMatrix from '../components/HeatmapMatrix';
import AlphaToggle from '../components/AlphaToggle';
import AlphaObservabilityConsole from '../components/AlphaObservabilityConsole';
import { useRSRankings, useHeatmap, useDashboard } from '../hooks/useApi';
import { useUIStore } from '../stores/uiStore';
import { TrendingUp, TrendingDown, Zap, Target, Calendar, ShieldAlert } from 'lucide-react';
import ErrorBoundary from '../components/ErrorBoundary';
import RegimeAdvisorBanner from '../components/RegimeAdvisorBanner';
import { TableSkeleton, CardSkeleton } from '../components/Skeletons';
import { formatPrice, formatPercent } from '../utils/formatter';

function decayScore(rsRating: number, rvol: number) {
  const multiplier = rvol <= 1.5 ? 1.0 : rvol <= 2.5 ? 0.85 : 0.65;
  return Math.min(100, rsRating * multiplier);
}
function persistence(rvol: number) {
  if (rvol <= 1.0) return 0.9;
  if (rvol <= 1.5) return 0.75;
  if (rvol <= 2.5) return 0.55;
  return 0.25;
}

const AlphaModelsPage: React.FC = () => {
  const { data: rankings, isLoading, error } = useRSRankings(100);
  const { data: heatmapData } = useHeatmap(100);
  const { data: dashboard } = useDashboard();
  const [activeModel, setActiveModel] = useState(0);
  const [viewMode, setViewMode] = useState<'data' | 'action'>('action');
  const { openXRay } = useUIStore();

  const heatmapMap = new Map((heatmapData || []).map((h) => [h.symbol, h.rsHistory]));

  const modelA = rankings?.filter((r) => r.symbol !== 'VNINDEX' && r.rsRating >= 80).sort((a, b) => b.change1y - a.change1y).slice(0, 20) ?? [];
  const modelBPicks = dashboard?.model_b?.top_picks ?? [];
  const modelB = modelBPicks.map((p) => {
    const rank = rankings?.find((r) => r.symbol === p.symbol);
    return {
      symbol: p.symbol,
      rsRating: rank?.rsRating ?? 0,
      rsRaw: rank?.rsRaw ?? 0,
      price: p.price ?? 0,
      rvol: rank?.rvol ?? 0,
      avgVol20d: p.avg_vol_20d ?? 0,
      change1y: rank?.change1y ?? 0,
      sector: rank?.sector ?? '',
      distanceFromMa50: p.distance_from_ma50,
      rsi: p.rsi,
      rankScore: p.rank_score,
      contextSource: p.context_source,
    } as RSRanking & { distanceFromMa50: number; rsi: number; rankScore: number };
  }).slice(0, 20);
  const bContext = dashboard?.model_b;
  const displayList = activeModel === 0 ? modelA : (modelB.length > 0 ? modelB : []);

  const todayStr = new Date().toLocaleDateString('vi-VN');
  const regime = dashboard?.breadth?.trendStatus || 'N/A';
  const regimeColor = regime === 'TRENDING' ? 'text-stock-up' : regime === 'CRISIS' ? 'text-stock-down' : 'text-amber-600';
  const vnIndexReturn = rankings?.find((r) => r.symbol === 'VNINDEX')?.change1y;

  return (
    <div className="p-8 bg-japandi-oat min-h-screen">
      <div className="mb-8 flex flex-col sm:flex-row sm:items-center sm:justify-between">
        <div>
          <Title className="text-japandi-earth text-3xl font-bold">Mô hình Alpha</Title>
          <Text className="text-japandi-earth/60">
            Hệ thống mô hình: Lớp A — Động lượng giá | Lớp B — Dòng tiền | Lớp C — Điểm nổ nền giá
          </Text>
        </div>
        <div className="flex items-center gap-3 mt-3 sm:mt-0">
          <AlphaToggle mode={viewMode} setMode={setViewMode} />
          <div className="flex items-center gap-1.5 text-xs bg-japandi-warm-sand/80 border border-japandi-warm-sand px-3 py-1.5 rounded-lg text-japandi-earth">
            <Calendar size={14} className="text-japandi-muted-clay" />
            <span>Phiên: <b>{todayStr}</b></span>
          </div>
          <div className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg font-medium ${regime === 'CRISIS' ? 'bg-rose-50 text-rose-600 border border-rose-100' : 'bg-emerald-50 text-emerald-700 border border-emerald-100'}`}>
            <ShieldAlert size={14} />
            <span>{regime === 'TRENDING' ? 'Xu hướng tăng' : regime === 'CRISIS' ? 'Khủng hoảng' : 'Đi ngang'}</span>
          </div>
        </div>
      </div>

      <RegimeAdvisorBanner message={dashboard?.systemMessage} />

      <AlphaObservabilityConsole dashboardData={dashboard} modelBContext={bContext} />

      <TabGroup index={activeModel} onIndexChange={setActiveModel} className="mb-8">
        <TabList className="gap-2">
          <Tab>
            <div className="flex items-center gap-2">
              <Zap size={16} />
              <span>Mô hình A — Động lượng</span>
            </div>
          </Tab>
          <Tab>
            <div className="flex items-center gap-2">
              <Target size={16} />
              <span>Mô hình B — Dòng tiền</span>
            </div>
          </Tab>
        </TabList>
      </TabGroup>

      <ErrorBoundary>
        <Grid numItemsLg={4} className="gap-6 mb-8">
          {isLoading ? (
            <CardSkeleton count={4} />
          ) : (
            <>
              <Card className="bg-japandi-warm-sand border-none shadow-sm">
                <Flex>
                  <Text className="text-japandi-muted-clay">Ứng viên</Text>
                  {activeModel === 0 ? (
                    <TrendingUp className="text-stock-up" size={20} />
                  ) : (
                    <TrendingDown className="text-stock-down" size={20} />
                  )}
                </Flex>
                <Metric className="text-japandi-earth">{displayList.length}</Metric>
              </Card>

              {activeModel === 0 ? (
                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">RS Rating TB</Text>
                  <Metric className="text-japandi-earth">
                    {displayList.length > 0 ? (displayList.reduce((s, r) => s + r.rsRating, 0) / displayList.length).toFixed(0) : 'N/A'}
                  </Metric>
                </Card>
              ) : (
                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">K/c MA50 TB</Text>
                  <Metric className="text-japandi-earth">
                    {displayList.length > 0 ? (displayList.reduce((s: number, r: any) => s + (r.distanceFromMa50 ?? 0), 0) / displayList.length).toFixed(1) + '%' : 'N/A'}
                  </Metric>
                </Card>
              )}

              {activeModel === 0 ? (
                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">Hiệu suất 1N TB</Text>
                  <Metric className="text-japandi-earth">
                    {displayList.length > 0 ? formatPercent(displayList.reduce((s, r) => s + r.change1y, 0) / displayList.length) : 'N/A'}
                  </Metric>
                  {vnIndexReturn !== undefined && (
                    <Text className="text-xs text-gray-400 mt-1">VN-Index: {formatPercent(vnIndexReturn)}</Text>
                  )}
                </Card>
              ) : (
                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">RSI TB</Text>
                  <Metric className="text-japandi-earth">
                    {displayList.length > 0 ? (displayList.reduce((s: number, r: any) => s + r.rsi, 0) / displayList.length).toFixed(0) : 'N/A'}
                  </Metric>
                  {bContext && (
                    <Text className="text-xs text-gray-400 mt-1">Độ rộng: {bContext.breadth_pct}%</Text>
                  )}
                </Card>
              )}

              <Card className="bg-japandi-warm-sand border-none shadow-sm">
                <Text className="text-japandi-muted-clay">{activeModel === 0 ? 'RVOL TB' : 'Bối cảnh'}</Text>
                <Metric className="text-japandi-earth">
                  {activeModel === 0
                    ? (displayList.length > 0 ? (displayList.reduce((s, r) => s + r.rvol, 0) / displayList.length).toFixed(2) : 'N/A')
                    : (bContext?.context ?? 'N/A')}
                </Metric>
              </Card>
            </>
          )}
        </Grid>
      </ErrorBoundary>

      <ErrorBoundary>
        <Card className="bg-white border-none shadow-sm overflow-hidden p-0">
          {isLoading ? (
            <div className="p-6">
              <TableSkeleton rows={8} />
            </div>
          ) : error ? (
            <div className="p-8 text-center">
              <p className="text-stock-down font-medium">Lỗi: {error.message}</p>
            </div>
          ) : (
            <>
              <Table>
                <TableHead className="bg-japandi-warm-sand/50">
                  <TableRow>
                    <TableHeaderCell className="text-japandi-earth">#</TableHeaderCell>
                    <TableHeaderCell className="text-japandi-earth">Mã</TableHeaderCell>
                    {activeModel === 0 ? (
                      <>
                        <TableHeaderCell className="text-japandi-earth">Giá</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">RS Rating</TableHeaderCell>
                        {viewMode === 'data' && <TableHeaderCell className="text-japandi-earth">RS 10D</TableHeaderCell>}
                        <TableHeaderCell className="text-japandi-earth">{viewMode === 'action' ? 'Điểm Tiền Thực tế' : 'Điểm RS Thô'}</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">Hiệu suất 1 Năm</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">RVOL</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">Vol TB 20N</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">Ngành</TableHeaderCell>
                      </>
                    ) : (
                      <>
                        <TableHeaderCell className="text-japandi-earth">Giá</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">K/c MA50</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">RSI</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">Điểm</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">Bối cảnh</TableHeaderCell>
                        <TableHeaderCell className="text-japandi-earth">Ngành</TableHeaderCell>
                      </>
                    )}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {displayList.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={activeModel === 0 ? (viewMode === 'data' ? 10 : 9) : 7} className="text-center py-16">
                      <div className="flex flex-col items-center gap-2">
                        <span className="text-2xl font-bold text-stock-down tracking-wide">KHÔNG ĐẠT YÊU CẦU</span>
                        <span className="text-xs text-gray-400 font-sans max-w-md">
                          {activeModel === 0
                            ? 'Không có cổ phiếu đáp ứng tiêu chuẩn RS ≥ 80 của Mô hình A (Động lượng).'
                            : (bContext?.picks_count === 0
                              ? `Mô hình B bị khóa. Độ rộng: ${bContext?.breadth_pct ?? '?'}%, Độ lệch: ${bContext?.breadth_std ?? '?'}, Vận tốc: ${bContext?.breadth_velocity ?? '?'}. Chờ thị trường hồi phục xung lực.`
                              : 'Không có tín hiệu Mô hình B (Dòng tiền) ở thời điểm hiện tại.')
                          }
                        </span>
                      </div>
                    </TableCell>
                  </TableRow>
                ) : displayList.map((item: any, idx: number) => (
                    <TableRow
                      key={item.symbol}
                      className="hover:bg-japandi-oat/50 transition-colors cursor-pointer"
                      onClick={() => openXRay(item.symbol)}
                    >
                      <TableCell className="text-japandi-muted-clay">{idx + 1}</TableCell>
                      <TableCell className="font-bold text-japandi-earth">{item.symbol}</TableCell>
                      {activeModel === 0 ? (
                        <>
                          <TableCell><Text className="text-japandi-earth">{formatPrice(item.price)}</Text></TableCell>
                          <TableCell>
                            <Badge
                              color={item.rsRating >= 90 ? 'emerald' : item.rsRating >= 70 ? 'yellow' : 'slate'}
                              className={item.rsRating >= 90 ? "bg-japandi-moss/20 text-japandi-moss border-none" : ""}
                            >
                              {item.rsRating}
                            </Badge>
                          </TableCell>
                          {viewMode === 'data' && (
                            <TableCell>
                              <HeatmapMatrix history={heatmapMap.get(item.symbol) || []} />
                            </TableCell>
                          )}
                          <TableCell>
                            {viewMode === 'action' ? (
                              <div className="w-full max-w-[120px] flex flex-col gap-0.5 py-1">
                                <div className="flex justify-between text-[9px] font-mono font-bold">
                                  <span className={persistence(item.rvol) >= 0.75 ? "text-emerald-600" : "text-amber-600"}>
                                    {persistence(item.rvol) >= 0.75 ? "BỀN BỈ" : "SÓNG NGẮN"}
                                  </span>
                                  <span className="text-gray-900">{decayScore(item.rsRating, item.rvol).toFixed(0)}đ</span>
                                </div>
                                <div className="w-full bg-gray-100 h-1.5 rounded-sm overflow-hidden border border-gray-200/40">
                                  <div
                                    className={`h-full transition-all duration-500 ${persistence(item.rvol) >= 0.75 ? "bg-emerald-500" : "bg-amber-500"}`}
                                    style={{ width: `${decayScore(item.rsRating, item.rvol)}%` }}
                                  />
                                </div>
                              </div>
                            ) : (
                              <Text className="text-japandi-earth text-xs">{item.rsRaw.toFixed(2)}</Text>
                            )}
                          </TableCell>
                          <TableCell>
                            <Text className={item.change1y >= 0 ? "text-stock-up" : "text-stock-down"}>
                              {formatPercent(item.change1y)}
                            </Text>
                          </TableCell>
                          <TableCell><Text className="text-japandi-earth">{item.rvol.toFixed(2)}</Text></TableCell>
                          <TableCell><Text className="text-japandi-earth text-sm">{formatPrice(item.avgVol20d)}</Text></TableCell>
                          <TableCell><Text className="text-japandi-earth text-sm">{item.sector}</Text></TableCell>
                        </>
                      ) : (
                        <>
                          <TableCell><Text className="text-japandi-earth">{formatPrice(item.price)}</Text></TableCell>
                          <TableCell>
                            <Text className={`font-mono font-bold ${item.distanceFromMa50 < -8 ? 'text-stock-down' : 'text-amber-600'}`}>
                              {item.distanceFromMa50?.toFixed(1)}%
                            </Text>
                          </TableCell>
                          <TableCell>
                            <Badge color={item.rsi <= 35 ? 'rose' : item.rsi <= 40 ? 'yellow' : 'slate'} className="border-none">
                              {item.rsi?.toFixed(0) ?? 'N/A'}
                            </Badge>
                          </TableCell>
                          <TableCell><Text className="text-japandi-earth font-mono text-xs">{item.rankScore?.toFixed(3)}</Text></TableCell>
                          <TableCell>
                            <Text className="text-[10px] text-japandi-muted-clay font-mono">{item.contextSource}</Text>
                          </TableCell>
                          <TableCell><Text className="text-japandi-earth text-sm">{item.sector}</Text></TableCell>
                        </>
                      )}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </>
          )}
        </Card>
      </ErrorBoundary>

      <div className="mt-6 space-y-3">
        <div className="flex flex-wrap items-center gap-4 text-xs text-gray-500 font-sans">
          <span className="font-semibold text-japandi-earth">Chú giải RS 10D:</span>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-stock-ceil" /><span>Tối thượng (&ge;90)</span></div>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-stock-up" /><span>Mạnh (75–89)</span></div>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-emerald-200" /><span>Chớm tăng (50–74)</span></div>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-stock-ref" /><span>Tích lũy (25–49)</span></div>
          <div className="flex items-center gap-1"><div className="w-3 h-3 rounded-sm bg-gray-200" /><span>Yếu (&lt;25)</span></div>
        </div>
        <Text className="text-xs text-japandi-muted-clay italic">
          * Mô hình A: RS Rating &ge; 80, sắp xếp theo Hiệu suất 1N. Mô hình B: Dòng tiền (đã suy hao), ưu tiên độ bền sóng cao.
        </Text>
      </div>


    </div>
  );
};

export default AlphaModelsPage;
