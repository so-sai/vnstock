import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Text, Metric, Badge, Button, Title,
} from '@tremor/react';
import {
  Play, Pause, SkipForward, AlertTriangle, RefreshCw, TrendingDown, TrendingUp,
  Clock, Activity, Shield,
} from 'lucide-react';
import { useI18n } from '../lib/i18n';

const API_BASE = '/api';

interface OrderBookLevel {
  price: number;
  volume: number;
}

interface OrderBookState {
  symbol: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  mid_price: number;
  spread: number;
  last_price: number;
  volatility: number;
  is_halted: boolean;
}

interface TWAPSlice {
  index: number;
  amount: number;
  status: string;
  fill_price: number | null;
  filled_qty: number | null;
  slippage: number | null;
  broker_order_id: string | null;
}

interface TWAPPlan {
  stale_campaign_id: string;
  total_amount: number;
  n_slices: number;
  slices: TWAPSlice[];
  status: string;
}

interface SlippageReport {
  n: number;
  max: number;
  mean: number;
  total_qty: number;
}

interface SandboxState {
  orderbook: OrderBookState | null;
  twap: TWAPPlan | null;
  slippage: SlippageReport | null;
  portfolio: {
    total_capital: number;
    stale_pct: number;
    escrow_balance: number;
    is_locked: boolean;
  };
  halt_active: boolean;
  halt_duration: number;
  book_thinning_pct: number;
  remaining_bid_depth: number;
}

const statusColors: Record<string, string> = {
  PENDING: 'bg-japandi-muted-clay/30 text-japandi-earth',
  SUBMITTED: 'bg-blue-100 text-blue-800',
  FILLED: 'bg-emerald-100 text-emerald-800',
  PARTIAL_FILLED: 'bg-amber-100 text-amber-800',
  CANCELED: 'bg-gray-100 text-gray-500',
  DEFERRED: 'bg-rose-100 text-rose-800',
  HALTED: 'bg-red-100 text-red-800',
};

const statusIcons: Record<string, React.ReactNode> = {
  PENDING: <Clock size={12} />,
  SUBMITTED: <Activity size={12} />,
  FILLED: <TrendingUp size={12} />,
  PARTIAL_FILLED: <TrendingDown size={12} />,
  CANCELED: <AlertTriangle size={12} />,
  DEFERRED: <AlertTriangle size={12} />,
  HALTED: <Shield size={12} />,
};

const PaperTradingPage: React.FC = () => {
  const { t } = useI18n('full');
  const [state, setState] = useState<SandboxState>({
    orderbook: null,
    twap: null,
    slippage: null,
    portfolio: { total_capital: 1_000_000, stale_pct: 0.13, escrow_balance: 0, is_locked: false },
    halt_active: false,
    halt_duration: 0,
    book_thinning_pct: 0,
    remaining_bid_depth: 0,
  });
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [slices, setSlices] = useState(5);
  const [depth, setDepth] = useState(5000);
  const [price, setPrice] = useState(100);
  const [capital, setCapital] = useState(1_000_000);

  const fetchState = useCallback(async () => {
    try {
      const [obRes, twapRes, slipRes, portRes] = await Promise.all([
        fetch(`${API_BASE}/sandbox/orderbook`).then(r => r.json()).catch(() => null),
        fetch(`${API_BASE}/sandbox/twap-status`).then(r => r.json()).catch(() => null),
        fetch(`${API_BASE}/sandbox/slippage`).then(r => r.json()).catch(() => null),
        fetch(`${API_BASE}/sandbox/portfolio`).then(r => r.json()).catch(() => null),
      ]);
      const haltRes = await fetch(`${API_BASE}/sandbox/halt-status`).then(r => r.json()).catch(() => null);
      const thinRes = await fetch(`${API_BASE}/sandbox/thinning`).then(r => r.json()).catch(() => null);

      setState({
        orderbook: obRes,
        twap: twapRes,
        slippage: slipRes,
        portfolio: portRes || state.portfolio,
        halt_active: haltRes?.is_halted || false,
        halt_duration: haltRes?.duration || 0,
        book_thinning_pct: thinRes?.thinning_pct || 0,
        remaining_bid_depth: thinRes?.remaining || 0,
      });
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchState();
    if (!autoRefresh) return;
    const interval = setInterval(fetchState, 2000);
    return () => clearInterval(interval);
  }, [fetchState, autoRefresh]);

  const handleStart = async () => {
    await fetch(`${API_BASE}/sandbox/start?n_slices=${slices}&depth=${depth}&price=${price}&capital=${capital}`, { method: 'POST' });
    fetchState();
  };

  const handleHalt = async () => {
    await fetch(`${API_BASE}/sandbox/halt`, { method: 'POST' });
    fetchState();
  };

  const handleResume = async () => {
    await fetch(`${API_BASE}/sandbox/resume`, { method: 'POST' });
    fetchState();
  };

  const handleExecuteSlice = async () => {
    await fetch(`${API_BASE}/sandbox/execute-next`, { method: 'POST' });
    fetchState();
  };

  const ob = state.orderbook;
  const twap = state.twap;
  const slip = state.slippage;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <Title className="text-japandi-earth text-xl font-bold">{t('Paper Trading Sandbox')}</Title>
          <Text className="text-japandi-muted-clay text-sm">
            {t('Phase 5 UAT — Depth-weighted fill + Almgren-Chriss slippage + Order Book Thinning')}
          </Text>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            icon={autoRefresh ? Pause : Play}
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            {autoRefresh ? 'Tạm dừng' : 'Tự động'}
          </Button>
          <Button size="sm" variant="secondary" icon={RefreshCw} onClick={fetchState}>
            Làm mới
          </Button>
        </div>
      </div>

      {/* Config + Controls */}
      <Card className="bg-white/60 backdrop-blur-sm border-japandi-muted-clay/30">
        <div className="flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-japandi-muted-clay">{t('Slices:')}</label>
            <input type="number" value={slices} onChange={e => setSlices(+e.target.value)}
              className="w-16 bg-white/80 border border-japandi-muted-clay/30 rounded px-2 py-1 text-xs font-mono" />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-japandi-muted-clay">{t('Depth:')}</label>
            <input type="number" value={depth} onChange={e => setDepth(+e.target.value)}
              className="w-20 bg-white/80 border border-japandi-muted-clay/30 rounded px-2 py-1 text-xs font-mono" />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-japandi-muted-clay">{t('Price:')}</label>
            <input type="number" value={price} step={0.5} onChange={e => setPrice(+e.target.value)}
              className="w-20 bg-white/80 border border-japandi-muted-clay/30 rounded px-2 py-1 text-xs font-mono" />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-japandi-muted-clay">{t('Capital:')}</label>
            <input type="number" value={capital} onChange={e => setCapital(+e.target.value)}
              className="w-28 bg-white/80 border border-japandi-muted-clay/30 rounded px-2 py-1 text-xs font-mono" />
          </div>
          <Button size="sm" icon={Play} onClick={handleStart} className="bg-japandi-earth text-white">
            Khởi chạy
          </Button>
          <Button size="sm" icon={SkipForward} onClick={handleExecuteSlice} className="bg-japandi-moss text-white">
            {t('Execute Slice')}
          </Button>
          <Button
            size="sm"
            variant={state.halt_active ? 'primary' : 'secondary'}
            icon={state.halt_active ? Play : Pause}
            onClick={state.halt_active ? handleResume : handleHalt}
            className={state.halt_active ? 'bg-emerald-600 text-white' : 'bg-rose-50 text-rose-700 border-rose-300'}
          >
            {state.halt_active ? `${t('Resume')} (${state.halt_duration.toFixed(0)}s)` : t('Trading Halt')}
          </Button>
        </div>
      </Card>

      {/* Status Bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="bg-white/60 border-japandi-muted-clay/30 p-4">
          <Text className="text-japandi-muted-clay text-xs">Trạng thái</Text>
          <div className="flex items-center gap-2 mt-1">
            {state.halt_active ? (
              <Badge color="red" icon={Shield} className="bg-red-100">{t('HALT')}</Badge>
            ) : (
              <Badge color="emerald" icon={Play} className="bg-emerald-100">{t('LIVE')}</Badge>
            )}
          </div>
        </Card>
        <Card className="bg-white/60 border-japandi-muted-clay/30 p-4">
          <Text className="text-japandi-muted-clay text-xs">{t('Plan')}</Text>
          <Metric className="text-japandi-earth">{twap?.status || 'NO_PLAN'}</Metric>
        </Card>
        <Card className="bg-white/60 border-japandi-muted-clay/30 p-4">
          <Text className="text-japandi-muted-clay text-xs">{t('Slices')}</Text>
          <Metric className="text-japandi-earth">
            {twap ? `${twap.slices.filter(s => s.status === 'FILLED').length}/${twap.n_slices}` : '0/0'}
          </Metric>
        </Card>
        <Card className="bg-white/60 border-japandi-muted-clay/30 p-4">
          <Text className="text-japandi-muted-clay text-xs">{t('Book Thinning')}</Text>
          <Metric className="text-japandi-earth">{state.book_thinning_pct.toFixed(1)}%</Metric>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Order Book */}
        <Card className="bg-white/60 backdrop-blur-sm border-japandi-muted-clay/30">
          <Title className="text-sm font-semibold text-japandi-earth mb-4">{t('Order Book')}</Title>
          <div className="grid grid-cols-2 gap-4">
            {/* Bids */}
            <div>
              <Text className="text-xs text-japandi-muted-clay mb-2">{t('BIDS')}</Text>
              <div className="space-y-1">
                {ob?.bids.map((l, i) => (
                  <div key={i} className="flex justify-between items-center text-xs font-mono bg-emerald-50/50 rounded px-2 py-1">
                    <span className="text-emerald-700">{l.price.toFixed(2)}</span>
                    <span className="text-emerald-600">{l.volume.toFixed(0)}</span>
                  </div>
                ))}
                {(!ob || ob.bids.length === 0) && (
                  <div className="text-xs text-japandi-muted-clay/50 italic py-2">{t('Empty')}</div>
                )}
              </div>
            </div>
            {/* Asks */}
            <div>
              <Text className="text-xs text-japandi-muted-clay mb-2">{t('ASKS')}</Text>
              <div className="space-y-1">
                {ob?.asks.map((l, i) => (
                  <div key={i} className="flex justify-between items-center text-xs font-mono bg-rose-50/50 rounded px-2 py-1">
                    <span className="text-rose-700">{l.price.toFixed(2)}</span>
                    <span className="text-rose-600">{l.volume.toFixed(0)}</span>
                  </div>
                ))}
                {(!ob || ob.asks.length === 0) && (
                  <div className="text-xs text-japandi-muted-clay/50 italic py-2">{t('Empty')}</div>
                )}
              </div>
            </div>
          </div>
          <div className="mt-3 pt-3 border-t border-japandi-muted-clay/20 flex justify-between text-xs font-mono">
            <span className="text-japandi-muted-clay">{t('Mid:')} <span className="text-japandi-earth font-semibold">{ob?.mid_price?.toFixed(2) || '—'}</span></span>
            <span className="text-japandi-muted-clay">{t('Spread:')} <span className="text-japandi-earth font-semibold">{ob?.spread?.toFixed(2) || '—'}</span></span>
          </div>
        </Card>

        {/* Slippage */}
        <Card className="bg-white/60 backdrop-blur-sm border-japandi-muted-clay/30">
          <Title className="text-sm font-semibold text-japandi-earth mb-4">{t('Slippage Report')}</Title>
          <div className="space-y-3">
            <div className="flex justify-between text-xs">
              <span className="text-japandi-muted-clay">{t('Total trades')}</span>
              <span className="font-mono text-japandi-earth">{slip?.n || 0}</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-japandi-muted-clay">{t('Mean slippage')}</span>
              <span className="font-mono text-japandi-earth">{((slip?.mean || 0) * 100).toFixed(4)}%</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-japandi-muted-clay">{t('Max slippage')}</span>
              <span className="font-mono text-japandi-earth">{((slip?.max || 0) * 100).toFixed(4)}%</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-japandi-muted-clay">{t('Total volume')}</span>
              <span className="font-mono text-japandi-earth">{(slip?.total_qty || 0).toFixed(0)}</span>
            </div>
          </div>
        </Card>
      </div>

      {/* TWAP Slices Table */}
      <Card className="bg-white/60 backdrop-blur-sm border-japandi-muted-clay/30">
        <Title className="text-sm font-semibold text-japandi-earth mb-4">{t('TWAP Slices')}</Title>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-japandi-muted-clay border-b border-japandi-muted-clay/20">
                <th className="text-left py-2 px-2">{t('#')}</th>
                <th className="text-right py-2 px-2">{t('Amount')}</th>
                <th className="text-center py-2 px-2">{t('Status')}</th>
                <th className="text-right py-2 px-2">{t('Fill Price')}</th>
                <th className="text-right py-2 px-2">{t('Filled Qty')}</th>
                <th className="text-right py-2 px-2">{t('Slippage')}</th>
              </tr>
            </thead>
            <tbody>
              {twap?.slices.map(s => (
                <tr key={s.index} className="border-b border-japandi-muted-clay/10 hover:bg-japandi-warm-sand/20">
                  <td className="py-2 px-2 text-japandi-earth font-semibold">{s.index}</td>
                  <td className="py-2 px-2 text-right">{s.amount.toFixed(2)}</td>
                  <td className="py-2 px-2 text-center">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold ${statusColors[s.status] || 'bg-gray-100'}`}>
                      {statusIcons[s.status]}
                      {s.status}
                    </span>
                  </td>
                  <td className="py-2 px-2 text-right">{s.fill_price?.toFixed(2) || '—'}</td>
                  <td className="py-2 px-2 text-right">{s.filled_qty?.toFixed(0) || '0'}</td>
                  <td className="py-2 px-2 text-right">{s.slippage != null ? `${(s.slippage * 100).toFixed(4)}%` : '—'}</td>
                </tr>
              ))}
              {(!twap || twap.slices.length === 0) && (
                <tr>
                  <td colSpan={6} className="py-4 text-center text-japandi-muted-clay/50 italic">{t('Chưa có TWAP plan')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};

export default PaperTradingPage;
