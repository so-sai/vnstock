import React, { useState, useRef, useEffect } from 'react';
import { toast } from 'sonner';
import { 
  Card, 
  Text, 
  Metric, 
  Grid, 
  Title, 
  Badge,
  Table,
  TableHead,
  TableRow,
  TableHeaderCell,
  TableBody,
  TableCell,
  Flex,
} from '@tremor/react';
import { usePortfolio, useWatchlistPins, useAIRecommendations } from '../hooks/useApi';
import { api } from '../lib/api';
import { AlertTriangle, ShieldCheck, TrendingUp, TrendingDown, Trash2, RefreshCw, Pencil, Check, X, BookmarkCheck, BookmarkPlus } from 'lucide-react';
import ErrorBoundary from '../components/ErrorBoundary';
import { TableSkeleton, CardSkeleton } from '../components/Skeletons';
import { formatPrice, formatVND } from '../utils/formatter';

interface InlineCellProps {
  value: number;
  symbol: string;
  field: 'quantity' | 'entry_price';
  onSave: () => void;
}

const InlineCell: React.FC<InlineCellProps> = ({ value, symbol, field, onSave }) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value.toString());
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const startEdit = () => {
    setDraft(value.toString());
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setDraft(value.toString());
  };

  const saveEdit = async () => {
    const num = parseFloat(draft);
    if (isNaN(num) || num <= 0) {
      toast.error('Giá trị không hợp lệ');
      return;
    }
    try {
      await api.updatePosition(symbol, { [field]: num });
      toast.success(`Đã cập nhật ${field === 'quantity' ? 'số lượng' : 'giá vốn'} ${symbol}`);
      setEditing(false);
      onSave();
    } catch (e) {
      toast.error(`Lỗi: ${(e as Error).message}`);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') saveEdit();
    if (e.key === 'Escape') cancelEdit();
  };

  if (editing) {
    return (
      <div className="flex items-center gap-1">
        <input
          ref={inputRef}
          type="number"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={saveEdit}
          className="w-24 px-1.5 py-0.5 text-sm bg-japandi-oat border border-japandi-moss rounded text-japandi-earth focus:outline-none focus:ring-1 focus:ring-japandi-moss"
          min={field === 'quantity' ? 100 : 0.01}
          step={field === 'quantity' ? 100 : 0.01}
        />
        <button onClick={saveEdit} className="p-0.5 text-japandi-moss hover:text-japandi-moss/80">
          <Check size={14} />
        </button>
        <button onClick={cancelEdit} className="p-0.5 text-stock-down hover:text-stock-down/80">
          <X size={14} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1 group/cell">
      <Text className="text-japandi-earth">{field === 'entry_price' ? formatPrice(value) : value.toLocaleString('vi-VN')}</Text>
      <button
        onClick={startEdit}
        className="p-0.5 text-japandi-muted-clay opacity-0 group-hover/cell:opacity-100 hover:text-japandi-earth transition-all"
        title="Chỉnh sửa"
      >
        <Pencil size={12} />
      </button>
    </div>
  );
};

const PortfolioPage: React.FC = () => {
  const { data: portfolio, isLoading, error, refetch } = usePortfolio();
  const { data: pinsData, refetch: refetchPins } = useWatchlistPins();
  const { data: aiData } = useAIRecommendations();
  const [deleting, setDeleting] = useState<string | null>(null);

  const handleRemovePin = async (symbol: string) => {
    try {
      await api.removeWatchlistPin(symbol);
      refetchPins();
      toast.success(`Đã bỏ theo dõi ${symbol}`);
    } catch (e) {
      toast.error(`Lỗi: ${(e as Error).message}`);
    }
  };

  const handleDelete = async (symbol: string) => {
    if (!confirm(`Xác nhận xóa vị thế ${symbol}?`)) return;
    setDeleting(symbol);
    try {
      await api.removePosition(symbol);
      toast.success(`Đã xóa ${symbol} khỏi danh mục`);
      refetch();
    } catch (e) {
      toast.error(`Không thể xóa ${symbol}: ${(e as Error).message}`);
    } finally {
      setDeleting(null);
    }
  };

  const handleRefresh = () => {
    toast.info('Đang làm mới danh mục...', { duration: 2000 });
    refetch();
  };

  return (
    <div className="p-8 bg-japandi-oat min-h-screen">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <Title className="text-japandi-earth text-3xl font-bold">Sổ tay Danh mục</Title>
          <Text className="text-japandi-earth/60">Theo dõi Lỗ/Lãi thực tế</Text>
        </div>
        <button
          onClick={handleRefresh}
          disabled={isLoading}
          className="flex items-center gap-2 px-4 py-2 bg-japandi-warm-sand text-japandi-earth rounded-lg hover:bg-japandi-earth hover:text-japandi-oat transition-all disabled:opacity-50"
        >
          <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
          Làm mới
        </button>
      </div>

      <ErrorBoundary>
        {portfolio?.alerts && portfolio.alerts.length > 0 && (
          <Card className="mb-8 bg-stock-down/10 border border-stock-down/30 shadow-sm">
            <Flex>
              <div className="flex items-center">
                <AlertTriangle className="text-stock-down mr-2" size={20} />
                <Title className="text-stock-down">Cảnh báo Cắt lỗ</Title>
              </div>
            </Flex>
            {portfolio.alerts.map((alert) => (
              <Text key={alert.symbol} className="text-stock-down mt-2">
                {alert.symbol}: {alert.message}
              </Text>
            ))}
          </Card>
        )}
      </ErrorBoundary>

      <ErrorBoundary>
        <Grid numItemsLg={4} className="gap-6 mb-8">
          {isLoading ? (
            <CardSkeleton count={4} />
          ) : portfolio ? (
            <>
              <Card className="bg-japandi-warm-sand border-none shadow-sm">
                <Text className="text-japandi-muted-clay">Tổng NAV</Text>
                <Metric className="text-japandi-earth">{formatVND(portfolio.totalNav)}</Metric>
              </Card>

              <Card className="bg-japandi-warm-sand border-none shadow-sm">
                <Flex>
                  <Text className="text-japandi-muted-clay">Lãi/Lỗ</Text>
                  {portfolio.totalPnlPercent >= 0 ? (
                    <TrendingUp className="text-stock-up" size={20} />
                  ) : (
                    <TrendingDown className="text-stock-down" size={20} />
                  )}
                </Flex>
                <Metric className={portfolio.totalPnlPercent >= 0 ? 'text-stock-up' : 'text-stock-down'}>
                  {portfolio.totalPnlPercent >= 0 ? '+' : ''}{portfolio.totalPnlPercent.toFixed(2)}%
                </Metric>
              </Card>

              <Card className="bg-japandi-warm-sand border-none shadow-sm">
                <Text className="text-japandi-muted-clay">Tiền mặt</Text>
                <Metric className="text-japandi-earth">{portfolio.cashPercent.toFixed(1)}%</Metric>
                <Text className="text-xs text-japandi-muted-clay mt-1">{formatVND(portfolio.cash)}</Text>
              </Card>

              <Card className="bg-japandi-warm-sand border-none shadow-sm">
                <Text className="text-japandi-muted-clay">Vị thế</Text>
                <Metric className="text-japandi-earth">{portfolio.positions.length}</Metric>
              </Card>
            </>
          ) : null}
        </Grid>
      </ErrorBoundary>

      {/* Layer 1: DANH MỤC THEO DÕI — user pins */}
      <ErrorBoundary>
        <Card className="bg-white border-none shadow-sm mb-6">
          <Flex className="border-b border-japandi-warm-sand pb-3 mb-3">
            <div className="flex items-center gap-2">
              <BookmarkCheck size={18} className="text-japandi-moss" />
              <Title className="text-japandi-earth text-lg">Danh mục Theo dõi</Title>
            </div>
            <Badge color="emerald" className="border-none">{pinsData?.count ?? 0} mã</Badge>
          </Flex>
          {pinsData && pinsData.symbols.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {pinsData.symbols.map((sym) => (
                <div key={sym} className="flex items-center gap-1 px-3 py-1.5 bg-japandi-warm-sand/50 rounded-lg">
                  <Text className="font-bold text-japandi-earth text-sm">{sym}</Text>
                  <button
                    onClick={() => handleRemovePin(sym)}
                    className="text-japandi-muted-clay hover:text-stock-down transition-colors"
                    title="Bỏ theo dõi"
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <Text className="text-japandi-muted-clay text-sm italic">
              Chưa có mã nào. Dùng X-Ray để thêm mã theo dõi.
            </Text>
          )}
        </Card>
      </ErrorBoundary>

      {/* Layer 2: ĐỀ XUẤT HÔM NAY — AI dynamic 3-tier */}
      {aiData && (
        <ErrorBoundary>
          <Card className="bg-white border-none shadow-sm mb-6">
            <Flex className="border-b border-japandi-warm-sand pb-3 mb-3">
              <div className="flex items-center gap-2">
                <TrendingUp size={18} className="text-japandi-earth" />
                <Title className="text-japandi-earth text-lg">Đề xuất Hôm nay</Title>
              </div>
              <div className="flex items-center gap-2">
                <Badge className="border-none">{aiData.regime.status}</Badge>
                <Badge color="slate" className="border-none">{aiData.summary.total_scanned} mã quét</Badge>
              </div>
            </Flex>
            <Grid numItemsLg={3} className="gap-4">
              {['core', 'rotation', 'opportunity'].map((tierKey) => {
                const tier = aiData.recommendations[tierKey];
                if (!tier) return null;
                const colors: Record<string, string> = {
                  core: 'bg-emerald-50 border-emerald-200',
                  rotation: 'bg-amber-50 border-amber-200',
                  opportunity: 'bg-sky-50 border-sky-200',
                };
                const textColors: Record<string, string> = {
                  core: 'text-emerald-700',
                  rotation: 'text-amber-700',
                  opportunity: 'text-sky-700',
                };
                return (
                  <Card key={tierKey} className={`${colors[tierKey] || 'bg-gray-50'} border rounded-lg shadow-none`}>
                    <Text className={`font-bold uppercase text-xs mb-2 ${textColors[tierKey] || 'text-gray-700'}`}>
                      {tier.label}
                    </Text>
                    <div className="space-y-1">
                      {tier.symbols.slice(0, 5).map((sym: string) => (
                        <Text key={sym} className="text-japandi-earth font-mono text-sm">{sym}</Text>
                      ))}
                      {tier.symbols.length === 0 && (
                        <Text className="text-japandi-muted-clay text-xs italic">Không có</Text>
                      )}
                    </div>
                    <Text className="text-xs text-japandi-muted-clay mt-2">{tier.symbols.length} mã</Text>
                  </Card>
                );
              })}
            </Grid>
          </Card>
        </ErrorBoundary>
      )}

      <ErrorBoundary>
        <Card className="bg-white border-none shadow-sm overflow-hidden p-0">
          {isLoading ? (
            <div className="p-6">
              <TableSkeleton rows={5} />
            </div>
          ) : error ? (
            <div className="p-8 text-center">
              <p className="text-stock-down font-medium">Lỗi: {error.message}</p>
              <button onClick={() => refetch()} className="mt-4 px-4 py-2 bg-japandi-earth text-japandi-oat rounded-lg hover:bg-japandi-earth/90">
                Thử lại
              </button>
            </div>
          ) : portfolio ? (
            <Table>
              <TableHead className="bg-japandi-warm-sand/50">
                <TableRow>
                  <TableHeaderCell className="text-japandi-earth">Mã</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">SL</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Giá vốn</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Giá hiện tại</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Lãi/Lỗ %</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Giá trị</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth">Trạng thái</TableHeaderCell>
                  <TableHeaderCell className="text-japandi-earth" />
                </TableRow>
              </TableHead>
              <TableBody>
                {portfolio.positions.map((pos) => {
                  const isAlert = pos.alert !== null;
                  return (
                    <TableRow key={pos.symbol} className={`hover:bg-japandi-oat/50 transition-colors ${isAlert ? 'bg-stock-down/5' : ''}`}>
                      <TableCell className="font-bold text-japandi-earth">{pos.symbol}</TableCell>
                      <TableCell>
                        <InlineCell
                          value={pos.quantity}
                          symbol={pos.symbol}
                          field="quantity"
                          onSave={refetch}
                        />
                      </TableCell>
                      <TableCell>
                        <InlineCell
                          value={pos.entryPrice}
                          symbol={pos.symbol}
                          field="entry_price"
                          onSave={refetch}
                        />
                      </TableCell>
                      <TableCell>
                        <Text className="text-japandi-earth">
                          {pos.currentPrice !== null ? formatPrice(pos.currentPrice) : 'N/A'}
                        </Text>
                      </TableCell>
                      <TableCell>
                        {pos.pnlPercent !== null ? (
                          <Text className={pos.pnlPercent >= 0 ? "text-stock-up" : "text-stock-down"}>
                            {pos.pnlPercent >= 0 ? '+' : ''}{pos.pnlPercent.toFixed(2)}%
                          </Text>
                        ) : (
                          <Text className="text-japandi-muted-clay">N/A</Text>
                        )}
                      </TableCell>
                      <TableCell>
                        <Text className="text-japandi-earth">
                          {pos.marketValue !== null ? formatVND(pos.marketValue) : 'N/A'}
                        </Text>
                      </TableCell>
                      <TableCell>
                        {isAlert ? (
                          <Badge color="rose" className="bg-stock-down/20 text-stock-down border-none">
                            <AlertTriangle size={12} className="inline mr-1" />
                            {pos.alert}
                          </Badge>
                        ) : pos.pnlPercent !== null && pos.pnlPercent >= 0 ? (
                          <Badge color="emerald" className="bg-stock-up/20 text-stock-up border-none">
                            <ShieldCheck size={12} className="inline mr-1" />
                            An toàn
                          </Badge>
                        ) : (
                          <Badge color="slate">Đang theo dõi</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <button
                          onClick={() => handleDelete(pos.symbol)}
                          disabled={deleting === pos.symbol}
                          className="p-1 text-japandi-muted-clay hover:text-stock-down transition-colors disabled:opacity-50"
                          title="Xóa vị thế"
                        >
                          <Trash2 size={16} />
                        </button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          ) : (
            <div className="p-8 text-center text-japandi-muted-clay">Không có dữ liệu danh mục</div>
          )}
        </Card>
      </ErrorBoundary>

      <div className="mt-6">
        <Text className="text-xs text-japandi-muted-clay italic">
          * Bộ theo dõi dùng giá từ SQLite Vault để tính toán chính xác. Ngưỡng cắt lỗ: {portfolio?.stopLossThreshold}%.
        </Text>
        <Text className="text-xs text-japandi-muted-clay italic">
          Cập nhật lần cuối: {portfolio?.updatedAt}
        </Text>
      </div>
    </div>
  );
};

export default PortfolioPage;
