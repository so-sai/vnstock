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
import { ShieldAlert } from 'lucide-react';
import ErrorBoundary from '../components/ErrorBoundary';
import { CardSkeleton, ChartSkeleton } from '../components/Skeletons';
import { formatPercent, formatRatio } from '../utils/formatter';

const TimeKernelPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState(0);

  const { data: backtest, isLoading: btLoading } = useBacktest(
    activeTab === 0 ? 'A' : 'B',
    '2025-01-01',
    '2026-04-17',
  );

  const { data: stressTest, isLoading: stLoading } = useStressTest();

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
        <Title className="text-japandi-earth text-3xl font-bold">Kiểm chứng Lịch sử</Title>
        <Text className="text-japandi-earth/60">Công cụ Kiểm chứng & Thử nghiệm Khủng hoảng 2022</Text>
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
              <Grid numItemsLg={4} className="gap-6 mb-8">
                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Flex>
                    <Text className="text-japandi-muted-clay">Giai đoạn</Text>
                    <ShieldAlert className="text-stock-down" size={20} />
                  </Flex>
                  <Text className="text-japandi-earth text-lg font-semibold">{stressTest.period}</Text>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">Mã đã kiểm tra</Text>
                  <Metric className="text-japandi-earth">{stressTest.totalSymbols}</Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">Drawdown TB</Text>
                  <Metric className="text-stock-down">{formatPercent(stressTest.avgMaxDrawdown)}</Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">Tỷ lệ Phục hồi</Text>
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
                  <Text className="text-japandi-muted-clay">Lợi nhuận TB</Text>
                  <Metric className={backtest.portfolioStats.avgReturn >= 0 ? 'text-stock-up' : 'text-stock-down'}>
                    {formatPercent(backtest.portfolioStats.avgReturn)}
                  </Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">Sharpe TB</Text>
                  <Metric className="text-stock-up">{formatRatio(backtest.portfolioStats.avgSharpe)}</Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">Max Drawdown TB</Text>
                  <Metric className="text-stock-down">{formatPercent(backtest.portfolioStats.avgMaxDrawdown)}</Metric>
                </Card>

                <Card className="bg-japandi-warm-sand border-none shadow-sm">
                  <Text className="text-japandi-muted-clay">Biến động TB</Text>
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
                      <TableHeaderCell className="text-japandi-earth">Max Drawdown</TableHeaderCell>
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
