import React, { useState, useCallback } from 'react';
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
  AreaChart,
} from '@tremor/react';
import { useBacktest, useStressTest } from '../hooks/useApi';
import { ShieldAlert, HelpCircle } from 'lucide-react';
import ErrorBoundary from '../components/ErrorBoundary';
import { CardSkeleton, ChartSkeleton } from '../components/Skeletons';
import { formatPercent, formatRatio } from '../utils/formatter';

const TimeKernelPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState(0);

  const [startInput, setStartInput] = useState('2022-01-01');
  const [endInput, setEndInput] = useState('2023-06-30');
  const [queryStart, setQueryStart] = useState('2022-01-01');
  const [queryEnd, setQueryEnd] = useState('2023-06-30');

  const { data: backtest, isLoading: btLoading } = useBacktest(
    activeTab === 0 ? 'A' : 'B',
    '2025-01-01',
    '2026-04-17',
  );

  const { data: stressTest, isLoading: stLoading } = useStressTest(queryStart, queryEnd);

  const handleRunStressTest = (e: React.FormEvent) => {
    e.preventDefault();
    setQueryStart(startInput);
    setQueryEnd(endInput);
  };

  const handleTabChange = useCallback((idx: number) => {
    setActiveTab(idx);
  }, []);

  const equityData = backtest?.equityCurve?.map((d) => ({
    date: d.date,
    Value: d.value,
  })) ?? [];

  return (
    <div className="p-8 bg-japandi-oat min-h-screen">
      <div className="mb-8">
        <Title className="text-japandi-earth text-3xl font-bold">Mô phỏng Lịch sử (Backtest)</Title>
        <Text className="text-japandi-earth/60">Công cụ Kiểm chứng & Thử nghiệm Khủng hoảng</Text>
      </div>

      <TabGroup index={activeTab} onIndexChange={handleTabChange} className="mb-8">
        <TabList className="gap-2">
          <Tab>Mô hình A — Động lượng</Tab>
          <Tab>Mô hình B — Hồi quy</Tab>
          <Tab>Stress Test 2022</Tab>
        </TabList>
      </TabGroup>

      <ErrorBoundary>
        {activeTab === 2 && (
          stLoading ? (
            <CardSkeleton count={4} />
          ) : stressTest ? (
            <>
              {/* Backtest Simulation Warning Banner */}
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6 flex items-start gap-3 shadow-none">
                <ShieldAlert className="text-amber-600 shrink-0 mt-0.5" size={18} />
                <div>
                  <h4 className="text-xs font-bold text-amber-800 uppercase tracking-wide">
                    ⚠️ BẢN CHẤT DỮ LIỆU: ĐÂY LÀ DỮ LIỆU MÔ PHỎNG (BACKTEST)
                  </h4>
                  <p className="text-[11px] text-amber-700/90 mt-1 leading-relaxed font-sans">
                    Toàn bộ kết quả hiển thị dưới đây là kết quả mô phỏng lịch sử dựa trên danh mục giả định. 
                    Mức sụt giảm tài sản (Drawdown) và lợi nhuận âm phản ánh các kiểm thử kịch bản khủng hoảng thực tế của thị trường, 
                    không liên quan và không ảnh hưởng đến số dư tài khoản thật của quý vị.
                  </p>
                </div>
              </div>

              {/* Dynamic Date Filter Form */}
              <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-none">
                <div>
                  <h4 className="text-xs font-bold text-japandi-earth">Khung Thời Gian Thử Nghiệm</h4>
                  <p className="text-[10px] text-japandi-muted-clay mt-0.5">Dữ liệu khả dụng từ 2022 đến 2026</p>
                </div>
                <form onSubmit={handleRunStressTest} className="flex flex-wrap items-center gap-2">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] text-japandi-muted-clay font-mono">Từ</span>
                    <input
                      type="date"
                      value={startInput}
                      onChange={(e) => setStartInput(e.target.value)}
                      className="px-3 py-1.5 text-xs border border-japandi-warm-sand rounded-lg font-mono text-japandi-earth focus:outline-none focus:border-japandi-earth bg-white/80 w-36"
                    />
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] text-japandi-muted-clay font-mono">Đến</span>
                    <input
                      type="date"
                      value={endInput}
                      onChange={(e) => setEndInput(e.target.value)}
                      className="px-3 py-1.5 text-xs border border-japandi-warm-sand rounded-lg font-mono text-japandi-earth focus:outline-none focus:border-japandi-earth bg-white/80 w-36"
                    />
                  </div>
                  <button
                    type="submit"
                    className="px-4 py-1.5 bg-japandi-moss text-white rounded-lg hover:bg-japandi-moss/90 text-xs font-semibold transition-all"
                  >
                    Chạy Thử Nghiệm
                  </button>
                </form>
              </div>

              <Grid numItemsLg={4} className="gap-6 mb-8">
                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex>
                    <Text className="text-japandi-muted-clay">Giai đoạn</Text>
                    <ShieldAlert className="text-stock-down" size={20} />
                  </Flex>
                  <Text className="text-japandi-earth text-lg font-semibold">{stressTest.period}</Text>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex className="items-center justify-between">
                    <Text className="text-japandi-muted-clay">Mã đã kiểm tra</Text>
                    <span title="Số lượng mã cổ phiếu được quét qua bộ lọc phân tích" className="cursor-help text-japandi-muted-clay/60">
                      <HelpCircle size={14} />
                    </span>
                  </Flex>
                  <Metric className="text-japandi-earth">{stressTest.totalSymbols}</Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex className="items-center justify-between">
                    <Text className="text-japandi-muted-clay">Sụt giảm TB</Text>
                    <span title="Mức sụt giảm trung bình từ đỉnh đến đáy của các mã cổ phiếu trong giai đoạn này" className="cursor-help text-japandi-muted-clay/60">
                      <HelpCircle size={14} />
                    </span>
                  </Flex>
                  <Metric className="text-stock-down">{formatPercent(stressTest.avgMaxDrawdown)}</Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex className="items-center justify-between">
                    <Text className="text-japandi-muted-clay">Tỷ lệ Phục hồi</Text>
                    <span title="Tỷ lệ phần trăm cổ phiếu đã phục hồi về đỉnh cũ sau đợt sụt giảm" className="cursor-help text-japandi-muted-clay/60">
                      <HelpCircle size={14} />
                    </span>
                  </Flex>
                  <Metric className="text-stock-up">{formatPercent(stressTest.recoveryRate)}</Metric>
                </Card>
              </Grid>

              <Card className="bg-white border-none shadow-sm p-6">
                <Title className="text-japandi-earth mb-4">Tổng kết Stress Test</Title>
                <Text className="text-japandi-earth/80">
                  Trong giai đoạn khủng hoảng 2022, drawdown tối đa trung bình trên {stressTest.totalSymbols} mã là{' '}
                  <span className="font-bold text-stock-down">{formatPercent(stressTest.avgMaxDrawdown)}</span>, với mã giảm sâu nhất{' '}
                  <span className="font-bold text-stock-down">{formatPercent(stressTest.worstDrawdown)}</span>.
                  Chỉ {formatPercent(stressTest.recoveryRate)} mã phục hồi về đỉnh trước khủng hoảng vào giữa năm 2023.
                </Text>
              </Card>
            </>
          ) : (
            <div className="p-8 text-center text-japandi-muted-clay">Không có dữ liệu Stress Test</div>
          )
        )}
      </ErrorBoundary>

      <ErrorBoundary>
        {activeTab < 2 && (
          btLoading ? (
            <div className="space-y-6">
              <CardSkeleton count={4} />
              <ChartSkeleton />
            </div>
          ) : backtest ? (
            <>
              <Grid numItemsLg={4} className="gap-6 mb-8">
                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex className="items-center justify-between">
                    <Text className="text-japandi-muted-clay">Lợi nhuận TB</Text>
                    <span title="Lợi nhuận trung bình hàng năm của mô hình trên các mã được thử nghiệm" className="cursor-help text-japandi-muted-clay/60">
                      <HelpCircle size={14} />
                    </span>
                  </Flex>
                  <Metric className={backtest.portfolioStats.avgReturn >= 0 ? 'text-stock-up' : 'text-stock-down'}>
                    {formatPercent(backtest.portfolioStats.avgReturn)}
                  </Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex className="items-center justify-between">
                    <Text className="text-japandi-muted-clay">Sharpe TB</Text>
                    <span title="Hệ số Sharpe đo lường tỷ suất sinh lời trên mỗi đơn vị rủi ro" className="cursor-help text-japandi-muted-clay/60">
                      <HelpCircle size={14} />
                    </span>
                  </Flex>
                  <Metric className="text-stock-up">{formatRatio(backtest.portfolioStats.avgSharpe)}</Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex className="items-center justify-between">
                    <Text className="text-japandi-muted-clay">Sụt giảm tối đa TB</Text>
                    <span title="Mức sụt giảm lớn nhất từ đỉnh cũ của mô hình trong quá trình backtest" className="cursor-help text-japandi-muted-clay/60">
                      <HelpCircle size={14} />
                    </span>
                  </Flex>
                  <Metric className="text-stock-down">{formatPercent(backtest.portfolioStats.avgMaxDrawdown)}</Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex className="items-center justify-between">
                    <Text className="text-japandi-muted-clay">Biến động TB</Text>
                    <span title="Độ lệch chuẩn của lợi nhuận, thể hiện độ biến động của mô hình" className="cursor-help text-japandi-muted-clay/60">
                      <HelpCircle size={14} />
                    </span>
                  </Flex>
                  <Metric className="text-japandi-earth">{formatPercent(backtest.portfolioStats.avgVolatility)}</Metric>
                </Card>
              </Grid>

              {equityData.length > 0 && (
                <Card className="mb-8 bg-japandi-warm-sand border-none shadow-sm">
                  <Title className="text-japandi-earth mb-4">Đường cong Vốn (Equity Curve)</Title>
                  <AreaChart
                    className="h-64"
                    data={equityData}
                    index="date"
                    categories={["Value"]}
                    colors={["emerald"]}
                    showXAxis
                    showYAxis={false}
                    showLegend={false}
                    startEndOnly
                  />
                </Card>
              )}

              <Card className="bg-white border-none shadow-sm overflow-hidden p-0">
                <Table>
                  <TableHead className="bg-japandi-warm-sand/50">
                    <TableRow>
                      <TableHeaderCell className="text-japandi-earth">Mã</TableHeaderCell>
                      <TableHeaderCell className="text-japandi-earth">1 Năm</TableHeaderCell>
                      <TableHeaderCell className="text-japandi-earth">Sharpe</TableHeaderCell>
                      <TableHeaderCell className="text-japandi-earth">Sụt giảm tối đa</TableHeaderCell>
                      <TableHeaderCell className="text-japandi-earth">Biến động</TableHeaderCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {backtest.topPicks.map((pick) => (
                      <TableRow key={pick.symbol} className="hover:bg-japandi-oat/50 transition-colors">
                        <TableCell className="font-bold text-japandi-earth">{pick.symbol}</TableCell>
                        <TableCell>
                          <Text className={pick.return1y >= 0 ? "text-stock-up" : "text-stock-down"}>
                            {formatPercent(pick.return1y)}
                          </Text>
                        </TableCell>
                        <TableCell>
                          <Badge
                            color={pick.sharpe >= 2 ? 'emerald' : pick.sharpe >= 1 ? 'yellow' : 'slate'}
                            className={pick.sharpe >= 2 ? "bg-japandi-moss/20 text-japandi-moss border-none" : ""}
                          >
                            {formatRatio(pick.sharpe)}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Text className="text-stock-down">{formatPercent(pick.maxDrawdown)}</Text>
                        </TableCell>
                        <TableCell><Text className="text-japandi-earth">{formatPercent(pick.volatility)}</Text></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Card>
            </>
          ) : (
            <div className="p-8 text-center text-stock-down">Không có dữ liệu backtest</div>
          )
        )}
      </ErrorBoundary>
    </div>
  );
};

export default TimeKernelPage;
