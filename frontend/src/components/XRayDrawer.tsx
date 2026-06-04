import React, { useState } from 'react';
import { X, TrendingUp, ShieldAlert, BarChart3, Activity, CheckCircle, AlertCircle, BookmarkPlus, BookmarkCheck } from 'lucide-react';
import { Card, Text, Badge, Button, Grid } from '@tremor/react';
import { useXRay, useWatchlistPins } from '../hooks/useApi';
import { api } from '../lib/api';
import { swuc } from '../lib/swuc';
import CandlestickChart from './CandlestickChart';

interface XRayDrawerProps {
  symbol: string | null;
  onClose: () => void;
  signalV1?: string;
}

const TIMEFRAMES = [
  { key: 'D', label: 'Ngày' },
  { key: 'W', label: 'Tuần' },
  { key: 'M', label: 'Tháng' },
] as const;

type TF = (typeof TIMEFRAMES)[number]['key'];

const XRayDrawer: React.FC<XRayDrawerProps> = ({ symbol, onClose, signalV1 }) => {
  const [tf, setTf] = useState<TF>('D');
  const { data: xray, isLoading } = useXRay(symbol, tf);
  const { data: pinsData, refetch: refetchPins } = useWatchlistPins();
  const [showForm, setShowForm] = useState(false);
  const [quantity, setQuantity] = useState(100);
  const [entryPrice, setEntryPrice] = useState('');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const [pinFeedback, setPinFeedback] = useState<string | null>(null);

  const isPinned = symbol ? (pinsData?.symbols ?? []).includes(symbol) : false;

  if (!symbol) return null;

  const regimeMap: Record<string, { label: string; color: string }> = {
    CRISIS: { label: 'KHỦNG HOẢNG', color: 'red' },
    TRENDING: { label: 'CÓ XU HƯỚNG', color: 'emerald' },
    RANGING: { label: 'ĐI NGANG', color: 'yellow' },
  };

  const regimeBadge = () => {
    if (!xray) return null;
    const info = regimeMap[xray.regime.status] || { label: xray.regime.status, color: 'slate' };
    return <Badge color={info.color} className="border-none">{info.label}</Badge>;
  };

  const handleTogglePin = async () => {
    if (!symbol) return;
    try {
      if (isPinned) {
        await api.removeWatchlistPin(symbol);
        setPinFeedback('Đã bỏ theo dõi');
      } else {
        await api.addWatchlistPin(symbol);
        setPinFeedback('Đã thêm vào Danh mục Theo dõi');
      }
      refetchPins();
      setTimeout(() => setPinFeedback(null), 2000);
    } catch (e: any) {
      setPinFeedback(e?.message || 'Lỗi');
      setTimeout(() => setPinFeedback(null), 2000);
    }
  };

  const handleAddPosition = async () => {
    if (!symbol || !entryPrice || quantity <= 0) {
      setFeedback({ type: 'error', msg: 'Vui lòng nhập đủ thông tin.' });
      return;
    }
    try {
      const price = parseFloat(entryPrice);
      if (isNaN(price) || price <= 0) {
        setFeedback({ type: 'error', msg: 'Giá không hợp lệ.' });
        return;
      }
      await api.addPosition({ symbol, quantity, entryPrice: price });
      setFeedback({ type: 'success', msg: `Đã thêm ${quantity} cổ phiếu ${symbol} vào danh mục.` });
      setShowForm(false);
      setQuantity(100);
      setEntryPrice('');
    } catch (e: any) {
      setFeedback({ type: 'error', msg: e?.message || 'Lỗi khi thêm vào danh mục.' });
    }
  };

  return (
    <>
      <div
        className="fixed inset-0 bg-black/20 backdrop-blur-sm z-40 transition-opacity"
        onClick={onClose}
      />
      <div className="fixed inset-y-0 right-0 w-full max-w-md bg-japandi-oat/80 backdrop-blur-xl border-l border-japandi-warm-sand/50 p-6 z-50 shadow-2xl transform transition-transform duration-300 ease-in-out overflow-y-auto">
        <div className="flex items-center justify-between border-b border-japandi-warm-sand pb-4 mb-6">
          <div>
            <h2 className="text-2xl font-bold text-japandi-earth flex items-center gap-2">
              Siêu âm <span className="text-stock-up font-mono">{symbol}</span>
            </h2>
            <div className="flex items-center gap-2 mt-1">
              <Text className="text-xs text-japandi-muted-clay">Lớp giám sát</Text>
              {regimeBadge()}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md hover:bg-japandi-warm-sand transition-colors text-japandi-earth"
          >
            <X size={20} />
          </button>
        </div>

        {isLoading ? (
          <div className="text-center py-12 text-japandi-muted-clay">Đang quét tia X...</div>
        ) : xray ? (
          <div className="space-y-6">
            <Card className={`${swuc('METRIC', 'xray')} p-4 rounded-lg border border-japandi-warm-sand/50 shadow-none`}>
              <h3 className="text-sm font-medium text-japandi-muted-clay mb-3 flex items-center gap-2">
                <BarChart3 size={16} /> Chỉ Số Kỹ Thuật Cốt Lõi
              </h3>
              <Grid numItems={2} className="gap-4">
                <div>
                  <Text className="text-xs text-japandi-muted-clay">RS Rating</Text>
                  <Text className="text-lg font-bold text-stock-up font-mono">
                    {xray.rsRating > 0 ? xray.rsRating : 'N/A'}
                  </Text>
                </div>
                <div>
                  <Text className="text-xs text-japandi-muted-clay">RS Raw</Text>
                  <Text className="text-lg font-bold text-japandi-earth font-mono">{xray.rsRaw.toFixed(4)}</Text>
                </div>
                <div>
                  <Text className="text-xs text-japandi-muted-clay">RSI (14)</Text>
                  <Text className={`text-lg font-bold font-mono ${xray.rsi14 ? (xray.rsi14 >= 70 ? 'text-stock-ceil' : xray.rsi14 <= 30 ? 'text-stock-down' : 'text-stock-ref') : ''}`}>
                    {xray.rsi14 ?? 'N/A'}
                  </Text>
                </div>
                <div>
                  <Text className="text-xs text-japandi-muted-clay">Z-Score (20)</Text>
                  <Text className={`text-lg font-bold font-mono ${xray.zScore ? (Math.abs(xray.zScore) > 2 ? 'text-stock-ceil' : 'text-japandi-earth') : ''}`}>
                    {xray.zScore != null ? xray.zScore.toFixed(2) : 'N/A'}
                  </Text>
                </div>
                <div>
                  <Text className="text-xs text-japandi-muted-clay">Đột biến Khối lượng</Text>
                  <Badge color={xray.volumeSpike ? 'emerald' : 'slate'} className="border-none text-xs">
                    {xray.volumeSpike ? `${xray.volumeRatio}x` : 'Bình thường'}
                  </Badge>
                </div>
                <div>
                  <Text className="text-xs text-japandi-muted-clay">RVOL</Text>
                  <Text className="text-lg font-bold text-japandi-earth font-mono">{xray.rvol.toFixed(2)}</Text>
                </div>
              </Grid>
            </Card>

            <Card className={`${swuc('SCREENER', 'xray')} p-4 rounded-lg border border-japandi-warm-sand/50 shadow-none`}>
              <h3 className="text-sm font-medium text-japandi-muted-clay mb-3 flex items-center gap-2">
                <BarChart3 size={16} className="text-japandi-earth" /> Phân loại Mô hình
              </h3>
              <div className="space-y-2 text-xs">
                {(() => {
                  const fitsModelA = xray.rsRating >= 80 && (xray.volumeSpike || xray.rvol > 1.5);
                  const fitsModelB = xray.rvol < 0.5 && xray.price > 0 && !fitsModelA;
                  if (fitsModelA) {
                    return (
                      <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-100">
                        <span className="font-bold text-emerald-700 uppercase text-xs">Mô hình A — Động lượng</span>
                        <p className="text-emerald-600 mt-1 leading-relaxed">
                          RS Rating {xray.rsRating}, RVOL {xray.rvol.toFixed(2)}x, Volume{' '}
                          {xray.volumeSpike ? 'bùng nổ' : 'xác nhận'}. Cổ phiếu đang trong giai đoạn
                          gia tốc dòng tiền, phù hợp chiến lược mua đuổi breakout.
                        </p>
                      </div>
                    );
                  }
                  if (fitsModelB) {
                    return (
                      <div className="p-3 rounded-lg bg-amber-50 border border-amber-100">
                        <span className="font-bold text-amber-700 uppercase text-xs">Mô hình B — Hồi quy</span>
                        <p className="text-amber-600 mt-1 leading-relaxed">
                          RVOL chỉ {xray.rvol.toFixed(2)}x — thanh khoản kiệt quệ. Cổ phiếu đang ở vùng
                          nén giá, chờ dòng tiền kích hoạt. Phù hợp chiến lược gom rải đinh tại vùng hỗ trợ.
                        </p>
                      </div>
                    );
                  }
                  return (
                    <div className="p-3 rounded-lg bg-gray-50 border border-gray-100">
                      <span className="font-bold text-gray-500 uppercase text-xs">KHÔNG ĐẠT MÔ HÌNH</span>
                      <p className="text-gray-400 mt-1 leading-relaxed">
                        Chỉ số hiện tại không khớp tiêu chuẩn đầu vào của Mô hình A (RS ≥ 80 + Volume spike)
                        hay Mô hình B (RVOL kiệt quệ &lt; 0.5). Chưa đủ điều kiện giải ngân.
                      </p>
                    </div>
                  );
                })()}
              </div>
            </Card>

            {xray.ohlcvHistory && xray.ohlcvHistory.length > 0 && (
              <Card className={`${swuc('CHART', 'xray')} p-4 rounded-lg border border-japandi-warm-sand/50 shadow-none`}>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-medium text-japandi-muted-clay flex items-center gap-2">
                    <Activity size={16} className="text-japandi-earth" /> Biểu đồ Nến
                  </h3>
                  <div className="flex gap-1 bg-japandi-warm-sand/50 rounded-md p-0.5">
                    {TIMEFRAMES.map((item) => (
                      <button
                        key={item.key}
                        onClick={() => setTf(item.key)}
                        className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
                          tf === item.key
                            ? 'bg-white/70 backdrop-blur-sm text-japandi-earth font-semibold shadow-sm'
                            : 'text-japandi-muted-clay hover:text-japandi-earth'
                        }`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
                <CandlestickChart data={xray.ohlcvHistory} height={180} />
              </Card>
            )}

            <Card className={`${swuc('VOLUME_SPIKE', 'xray')} p-4 rounded-lg border border-japandi-warm-sand/50 shadow-none`}>
              <h3 className="text-sm font-medium text-japandi-muted-clay mb-3 flex items-center gap-2">
                <TrendingUp size={16} className="text-stock-up" /> Chẩn Đoán Xu Hướng
              </h3>
              <ul className="text-xs space-y-2 text-japandi-earth">
                <li className="flex items-start gap-2">
                  <span className={xray.aboveMa50 ? 'text-stock-up' : 'text-stock-down'}>
                    {xray.aboveMa50 ? '▲' : '▼'}
                  </span>
                  {xray.aboveMa50 != null
                    ? `Giá ${xray.aboveMa50 ? 'trên' : 'dưới'} MA50 — ${xray.aboveMa50 ? 'Xu hướng ngắn hạn tích cực' : 'Áp lực ngắn hạn'}`
                    : 'MA50: Chưa đủ dữ liệu'}
                </li>
                <li className="flex items-start gap-2">
                  <span className={xray.aboveMa200 ? 'text-stock-up' : 'text-stock-down'}>
                    {xray.aboveMa200 ? '▲' : '▼'}
                  </span>
                  {xray.aboveMa200 != null
                    ? `Giá ${xray.aboveMa200 ? 'trên' : 'dưới'} MA200 — ${xray.aboveMa200 ? 'Xu hướng dài hạn tích cực' : 'Xu hướng dài hạn tiêu cực'}`
                    : 'MA200: Chưa đủ dữ liệu'}
                </li>
                <li className="flex items-start gap-2">
                  <span className={xray.volumeSpike ? 'text-stock-up' : 'text-japandi-muted-clay'}>
                    {xray.volumeSpike ? '⚡' : '○'}
                  </span>
                  {xray.volumeSpike
                    ? `Volume đột biến gấp ${xray.volumeRatio}x trung bình 20 phiên — Xác nhận dòng tiền`
                    : 'Volume bình thường, chưa có đột biến'}
                </li>
                {xray.foreign10dAcc !== 0 && (
                  <li className="flex items-start gap-2">
                    <span className={xray.foreign10dAcc > 0 ? 'text-stock-up' : 'text-stock-down'}>
                      {xray.foreign10dAcc > 0 ? '▲' : '▼'}
                    </span>
                    Khối ngoại {xray.foreign10dAcc > 0 ? 'mua ròng' : 'bán ròng'}{' '}
                    {Math.abs(xray.foreign10dAcc).toFixed(1)} tỷ (10 ngày)
                  </li>
                )}
              </ul>
            </Card>

            <Card className={`${swuc('REGIME_SHIFT', 'xray')} p-4 rounded-lg border border-japandi-warm-sand/50 shadow-none`}>
              <h3 className="text-sm font-medium text-japandi-muted-clay mb-3 flex items-center gap-2">
                <ShieldAlert size={16} className="text-stock-down" /> Cảnh Báo Rủi Ro
              </h3>
              <Text className="text-xs text-japandi-earth leading-relaxed">
                {xray.regime.status === 'CRISIS' ? (
                  <>Thị trường đang ở trạng thái <span className="text-stock-down font-bold">KHỦNG HOẢNG</span> (điểm số: {xray.regime.score.toFixed(2)}).
                  Tỷ lệ chiến thắng giảm. Ưu tiên quản trị rủi ro, tuân thủ mức dừng lỗ <span className="text-stock-down font-bold">-5%</span>.</>
                ) : xray.regime.status === 'TRENDING' ? (
                  <>Thị trường đang ở trạng thái <span className="text-stock-up font-bold">CÓ XU HƯỚNG</span> (điểm số: {xray.regime.score.toFixed(2)}).
                  Môi trường thuận lợi cho các chiến lược Động lượng (Mô hình A).</>
                ) : (
                  <>Thị trường đang ở trạng thái <span className="text-stock-ref font-bold">{xray.regime.status}</span>.
                  Giao dịch thận trọng, ưu tiên các mã có nền tảng cơ bản vững chắc.</>
                )}
              </Text>
            </Card>

            <Card className={`${swuc('FOREIGN_FLOW', 'xray')} p-4 rounded-lg border border-japandi-warm-sand/50 shadow-none`}>
              <Text className="text-xs text-japandi-muted-clay mb-2">Tín hiệu gốc</Text>
              <div className="flex flex-wrap gap-2">
                {xray.rsRating > 0 && (
                  <Badge color="emerald" className="bg-japandi-moss/20 text-japandi-moss border-none text-xs">
                    RS {xray.rsRating}
                  </Badge>
                )}
                {signalV1 && (
                  <Badge color="slate" className="border-none text-xs">{signalV1}</Badge>
                )}
                <Badge color={xray.volumeSpike ? 'emerald' : 'slate'} className="border-none text-xs">
                  Đột biến Vol: {xray.volumeSpike ? 'Có' : 'Không'}
                </Badge>
              </div>
            </Card>

            {showForm && (
              <Card className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand/50 shadow-none">
                <h3 className="text-sm font-medium text-japandi-earth mb-3">Thêm vào Danh mục</h3>
                <div className="space-y-3">
                  <div>
                    <label className="text-xs text-japandi-muted-clay block mb-1">Số lượng</label>
                    <input
                      type="number"
                      value={quantity}
                      onChange={(e) => setQuantity(parseInt(e.target.value) || 0)}
                      className="w-full px-3 py-2 border border-japandi-warm-sand rounded-md text-japandi-earth text-sm focus:outline-none focus:ring-2 focus:ring-japandi-moss"
                      min={1}
                      step={100}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-japandi-muted-clay block mb-1">Giá mua (VND)</label>
                    <input
                      type="number"
                      value={entryPrice}
                      onChange={(e) => setEntryPrice(e.target.value)}
                      placeholder={xray.price?.toLocaleString('vi-VN')}
                      className="w-full px-3 py-2 border border-japandi-warm-sand rounded-md text-japandi-earth text-sm focus:outline-none focus:ring-2 focus:ring-japandi-moss"
                    />
                  </div>
                  {feedback && (
                    <div className={`flex items-center gap-2 text-xs ${feedback.type === 'success' ? 'text-stock-up' : 'text-stock-down'}`}>
                      {feedback.type === 'success' ? <CheckCircle size={14} /> : <AlertCircle size={14} />}
                      {feedback.msg}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <Button onClick={handleAddPosition} className="flex-1 bg-japandi-moss hover:bg-japandi-moss/90 border-none py-2 text-white text-sm rounded-md">
                      Xác nhận
                    </Button>
                    <Button onClick={() => { setShowForm(false); setFeedback(null); }} className="flex-1 bg-japandi-warm-sand hover:bg-japandi-warm-sand/90 border-none py-2 text-japandi-earth text-sm rounded-md">
                      Hủy
                    </Button>
                  </div>
                </div>
              </Card>
            )}
          </div>
        ) : (
          <div className="text-center py-12 text-stock-down">Không thể tải dữ liệu X-Ray</div>
        )}

        <div className="sticky bottom-0 pt-4 mt-4 bg-japandi-oat border-t border-japandi-warm-sand -mx-6 px-6 space-y-2">
          <div className="flex gap-2">
            <Button
              onClick={handleTogglePin}
              className={`flex-1 border-none py-2.5 font-medium rounded-md transition-colors text-sm flex items-center justify-center gap-2 ${
                isPinned
                  ? 'bg-japandi-moss/20 text-japandi-moss hover:bg-japandi-moss/30'
                  : 'bg-japandi-warm-sand text-japandi-earth hover:bg-japandi-warm-sand/80'
              }`}
              icon={isPinned ? BookmarkCheck : BookmarkPlus}
            >
              {isPinned ? 'Đang theo dõi' : 'Theo dõi'}
            </Button>
            <Button
              onClick={() => {
                setShowForm(true);
                setFeedback(null);
                if (xray?.price) setEntryPrice(xray.price.toString());
              }}
              className="flex-1 bg-japandi-earth hover:bg-japandi-earth/90 border-none py-2.5 text-japandi-oat font-medium rounded-md transition-colors text-sm flex items-center justify-center gap-2"
              icon={Activity}
            >
              Thêm Vị thế
            </Button>
          </div>
          {pinFeedback && (
            <Text className="text-xs text-center text-japandi-moss">{pinFeedback}</Text>
          )}
          {showForm && (
            <Text className="text-xs text-japandi-muted-clay text-center italic">
              Điền thông tin mua ở form bên trên
            </Text>
          )}
        </div>
      </div>
    </>
  );
};

export default XRayDrawer;
