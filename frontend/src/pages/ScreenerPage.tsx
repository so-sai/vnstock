import React, { useState } from 'react';
import { toast } from 'sonner';
import { 
  Card, 
  Table, 
  TableHead, 
  TableRow, 
  TableHeaderCell, 
  TableBody, 
  TableCell, 
  Text, 
  Title, 
  Badge,
  Icon,
} from '@tremor/react';
import { swuc } from '../lib/swuc';
import { Diamond, Info, RefreshCw, List, Columns } from 'lucide-react';
import { useScreener, useHeatmap } from '../hooks/useApi';
import { api } from '../lib/api';
import HeatmapMatrix from '../components/HeatmapMatrix';
import XRayDrawer from '../components/XRayDrawer';
import ErrorBoundary from '../components/ErrorBoundary';
import { TableSkeleton } from '../components/Skeletons';
import { formatPrice } from '../utils/formatter';
import { ThreeSecondDecisionStrip } from '../components/ThreeSecondDecisionStrip';
import { AsiaFlowMap } from '../components/AsiaFlowMap';

const getChangeColor = (changePercent: number) => {
  if (changePercent > 0) return 'text-stock-up';
  if (changePercent < 0) return 'text-stock-down';
  return 'text-stock-ref';
};

const ScreenerPage: React.FC = () => {
  const { data: candidates, isLoading, error, refetch } = useScreener(50);
  const { data: heatmapData } = useHeatmap(50);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [selectedSignal, setSelectedSignal] = useState<string | undefined>(undefined);
  const [viewMode, setViewMode] = useState<'table' | 'decision'>('table');
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; symbol: string } | null>(null);

  const heatmapMap = new Map((heatmapData || []).map((h) => [h.symbol, h.rsHistory]));

  const handleRowClick = (candidate: any) => {
    setSelectedSymbol(candidate.symbol);
    setSelectedSignal(candidate.signalV1);
  };

  const handleRefresh = () => {
    toast.info('Đang làm mới dữ liệu...', { duration: 2000 });
    refetch();
  };

  return (
    <div className="p-8 bg-japandi-oat min-h-screen relative overflow-hidden">
      <div className="mb-8 flex items-center justify-between">
        <div className="flex items-center">
          <Icon icon={Diamond} size="xl" className="text-japandi-moss mr-4" />
          <div>
            <Title className="text-japandi-earth text-3xl font-bold">Bộ lọc Kim cương</Title>
            <Text className="text-japandi-earth/60">
              {candidates?.length ?? 0} mã — {candidates?.[0]?.signalV1 === 'Breakout' ? 'Tín hiệu Đột phá' : 'Xếp hạng RS (Dự phòng Khủng hoảng)'}
            </Text>
            <p className="text-[10px] text-japandi-muted-clay font-mono tracking-wider uppercase mt-0.5">
              CẬP NHẬT: PHIÊN EOD 21/05/2026
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setViewMode(viewMode === 'table' ? 'decision' : 'table')}
            className="flex items-center gap-2 px-3 py-2 bg-japandi-warm-sand text-japandi-earth rounded-lg hover:bg-japandi-earth hover:text-japandi-oat transition-all text-xs"
          >
            {viewMode === 'table' ? <Columns size={14} /> : <List size={14} />}
            {viewMode === 'table' ? 'Xem Quyết định' : 'Xem Bảng'}
          </button>
          <button
            onClick={handleRefresh}
            disabled={isLoading}
            className="flex items-center gap-2 px-4 py-2 bg-japandi-warm-sand text-japandi-earth rounded-lg hover:bg-japandi-earth hover:text-japandi-oat transition-all disabled:opacity-50"
          >
            <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
            Làm mới
          </button>
        </div>
      </div>

      <ErrorBoundary>
        <AsiaFlowMap />
      </ErrorBoundary>

      <ErrorBoundary>
        <Card className={`${swuc('SCREENER', 'screener')} border-none shadow-sm overflow-hidden p-0`}>
          {isLoading ? (
            <div className="p-6">
              <TableSkeleton rows={8} />
            </div>
          ) : error ? (
            <div className="p-8 text-center">
              <p className="text-stock-down font-medium">Lỗi tải dữ liệu: {error.message}</p>
              <button onClick={() => refetch()} className="mt-4 px-4 py-2 bg-japandi-earth text-japandi-oat rounded-lg hover:bg-japandi-earth/90">
                Thử lại
              </button>
            </div>
          ) : !candidates || candidates.length === 0 ? (
            <div className="flex flex-col items-center justify-center p-12 text-japandi-muted-clay">
              <Info className="w-8 h-8 mb-2 text-japandi-muted-clay" />
              <p className="text-sm font-medium text-japandi-earth">Phiên giao dịch hiện tại chưa xuất hiện tín hiệu đột phá đạt tiêu chuẩn Bộ lọc Kim cương.</p>
              <p className="text-xs text-japandi-muted-clay">Hệ thống phòng thủ khuyến nghị tiếp tục duy trì trạng thái quan sát.</p>
            </div>
          ) : (
            <Table>
              <TableHead className="bg-japandi-warm-sand/50">
                <TableRow>
                  <TableHeaderCell className="text-japandi-earth">Mã</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Giá</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Đổi %</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">6 Tháng</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Tín hiệu</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">RS Rating</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">
                    <div className="flex items-center gap-1">
                      RS 10D <span title="Diễn biến RS 10 phiên gần nhất"><Info size={12} className="text-japandi-muted-clay inline" /></span>
                    </div>
                  </TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Ngành</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth text-right">Vol Ratio</TableHeaderCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {candidates?.map((item) => (
                  <TableRow 
                    key={item.symbol} 
                    className="hover:bg-japandi-oat/50 transition-colors cursor-pointer select-none"
                    onClick={() => handleRowClick(item)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      setContextMenu({
                        x: e.clientX,
                        y: e.clientY,
                        symbol: item.symbol
                      });
                    }}
                  >
                    <TableCell className="font-bold text-japandi-earth">{item.symbol}</TableCell>
                    <TableCell>
                      <Text className="text-japandi-earth">{formatPrice(item.price)}</Text>
                    </TableCell>
                    <TableCell>
                      <Text className={getChangeColor(item.changePercent)}>
                        {item.changePercent >= 0 ? '+' : ''}{item.changePercent.toFixed(2)}%
                      </Text>
                    </TableCell>
                    <TableCell>
                      <Text className={item.return6m >= 0 ? "text-stock-up" : "text-stock-down"}>
                        {item.return6m >= 0 ? '+' : ''}{item.return6m.toFixed(1)}%
                      </Text>
                    </TableCell>
                    <TableCell>
                      <Badge 
                        color={item.signalV1 === 'Breakout' ? 'emerald' : 'slate'}
                        className={item.signalV1 === 'Breakout' ? "bg-japandi-moss/20 text-japandi-moss border-none" : ""}
                      >
                        {item.signalV1 === 'Breakout' ? 'Đột phá' : item.signalV1}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge
                        color={item.rsRating && item.rsRating >= 90 ? 'emerald' : item.rsRating && item.rsRating >= 70 ? 'yellow' : 'slate'}
                        className={item.rsRating && item.rsRating >= 90 ? "bg-japandi-moss/20 text-japandi-moss border-none" : ""}
                      >
                        {item.rsRating ?? 'N/A'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <HeatmapMatrix history={heatmapMap.get(item.symbol) || []} />
                    </TableCell>
                    <TableCell>
                      <Text className="text-japandi-earth text-sm">{item.sector ?? 'N/A'}</Text>
                    </TableCell>
                    <TableCell className="text-right">
                      <Text className="text-japandi-earth">{item.volumeRatio.toFixed(2)}x</Text>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      </ErrorBoundary>

      {viewMode === 'decision' && candidates && candidates.length > 0 && (
        <div className="mt-8">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-bold text-japandi-earth uppercase tracking-wider font-mono">
              🧭 TẦNG QUYẾT ĐỊNH 3 GIÂY — TOP {Math.min(5, candidates.length)} MÃ
            </h2>
            <span className="text-[10px] text-japandi-muted-clay font-mono tracking-wider">
              RS + DÒNG TIỀN + CHU KỲ NGÀNH → LỆNH
            </span>
          </div>
          <div className="space-y-2">
            {candidates.slice(0, 5).map((item) => (
              <ThreeSecondDecisionStrip
                key={item.symbol}
                symbol={item.symbol}
                rsRating={item.rsRating ?? 0}
                volRatio={item.volumeRatio}
                sector={item.sector ?? 'N/A'}
              />
            ))}
          </div>
        </div>
      )}

      <div className="mt-6">
        <Text className="text-xs text-japandi-muted-clay italic">
          * Tín hiệu được cập nhật mỗi 15 phút. Trong chế độ khủng hoảng, chuyển sang xếp hạng RS Cao.
        </Text>
      </div>

      <XRayDrawer
        symbol={selectedSymbol}
        onClose={() => setSelectedSymbol(null)}
        signalV1={selectedSignal}
      />

      {contextMenu && (
        <>
          <div
            className="fixed inset-0 z-50 bg-transparent"
            onClick={() => setContextMenu(null)}
            onContextMenu={(e) => {
              e.preventDefault();
              setContextMenu(null);
            }}
          />
          <div
            className="fixed z-50 bg-white border border-stone-200 rounded-lg shadow-lg py-1.5 min-w-[220px] text-left animate-in fade-in zoom-in-95 duration-100 font-sans"
            style={{ top: contextMenu.y, left: contextMenu.x }}
          >
            <button
              onClick={() => {
                setSelectedSymbol(contextMenu.symbol);
                setContextMenu(null);
              }}
              className="w-full text-left px-4 py-2 text-xs text-stone-700 hover:bg-stone-100 transition-colors flex items-center gap-2"
            >
              <span>🔍</span> Xem lý do đột phá của {contextMenu.symbol}
            </button>
            <button
              onClick={async () => {
                try {
                  await api.addWatchlistPin(contextMenu.symbol);
                  toast.success(`Đã thêm ${contextMenu.symbol} vào danh mục theo dõi`);
                } catch (e: any) {
                  toast.error(`Lỗi: ${e.message || e}`);
                }
                setContextMenu(null);
              }}
              className="w-full text-left px-4 py-2 text-xs text-stone-700 hover:bg-stone-100 transition-colors flex items-center gap-2"
            >
              <span>📌</span> Ghim nhanh vào Watchlist
            </button>
            <button
              onClick={() => {
                setSelectedSymbol(contextMenu.symbol);
                setContextMenu(null);
              }}
              className="w-full text-left px-4 py-2 text-xs text-stone-700 hover:bg-stone-100 transition-colors flex items-center gap-2"
            >
              <span>📊</span> Xem Nhật ký Dòng tiền (X-Ray)
            </button>
          </div>
        </>
      )}
    </div>
  );
};

export default ScreenerPage;
