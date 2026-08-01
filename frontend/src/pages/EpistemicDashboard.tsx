import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Cpu,
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  CheckCircle2,
  TrendingUp,
  TrendingDown,
  Database,
  Activity,
  RefreshCw,
  Search,
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const API_BASE = import.meta.env.DEV ? '/api' : 'http://localhost:17039/api';
const DEFAULT_TARGETS = [
  'FPT', 'ACB', 'HDB', 'MBB', 'VCB',
  'HPG', 'VHM', 'DGC', 'MWG', 'GAS', 'IJC', 'BCM', 'VPB',
];

// ── Định nghĩa Interface khớp 100% response thực tế /api/v1/epistemic/composite ──
interface EpistemicRow {
  symbol: string;
  macro_score: number;
  internal_score: number;
  market_score: number;
  raw_score: number;
  final_score: number;
  coverage: number;
  coherence: number;
  buy_gap: number;
  target_alloc_pct: number;
  action_delta_pct: number;
  margin_status: string;
  governor_mandate: string;
  why_drivers: string;
  veto_flag: string;
  action: string;
  recommendation: string;
}

interface CompositeResponse {
  status: string;
  policy: string;
  count: number;
  generated_at: string;
  data: EpistemicRow[];
}

interface DensityRow {
  symbol: string;
  required_quarters: number;
  available_quarters: number;
  density_pct: number;
  missing_quarters: string[];
  status: string;
  healed: boolean;
}

interface DensityResponse {
  status: string;
  count: number;
  generated_at: string;
  data: DensityRow[];
}

const fetchComposite = async (symbols: string[]): Promise<CompositeResponse> => {
  const qs = symbols.map((s) => `symbols=${encodeURIComponent(s)}`).join('&');
  const res = await fetch(`${API_BASE}/v1/epistemic/composite?${qs}`);
  if (!res.ok) throw new Error(`API lỗi: HTTP ${res.status}`);
  const json = await res.json();
  if (json.status !== 'success') throw new Error(json.message || 'API trả status không success');
  return json;
};

const fetchDataDensity = async (symbols: string[]): Promise<DensityResponse> => {
  const qs = symbols.map((s) => `symbols=${encodeURIComponent(s)}`).join('&');
  const res = await fetch(`${API_BASE}/v1/epistemic/data-density?${qs}`);
  if (!res.ok) throw new Error(`API lỗi: HTTP ${res.status}`);
  const json = await res.json();
  if (json.status !== 'success') throw new Error(json.message || 'API trả status không success');
  return json;
};

// ── Theme theo Governor Mandate ──
function mandateTheme(mandate: string) {
  switch (mandate) {
    case 'AGGRESSIVE_DEPLOYMENT':
      return { badge: 'bg-emerald-600 text-emerald-50', label: '🚀 Tấn công tối đa (Aggressive)' };
    case 'NORMAL_OPERATION':
      return { badge: 'bg-cyan-700 text-cyan-50', label: '💵 Vận hành chuẩn (Normal)' };
    case 'NEUTRAL_DEFENSIVE':
      return { badge: 'bg-amber-600 text-amber-50', label: '⚠️ Phòng thủ (Defensive)' };
    case 'CAPITAL_PRESERVATION':
    default:
      return { badge: 'bg-rose-700 text-rose-50', label: '🛡️ Bảo toàn vốn (Preservation)' };
  }
}

function vetoBadge(veto: string) {
  if (veto === 'NONE') return { cls: 'bg-emerald-100 text-emerald-800', icon: <CheckCircle2 size={13} />, text: '🟢 OK' };
  if (veto === 'MACRO_STRESS') return { cls: 'bg-amber-100 text-amber-800', icon: <AlertTriangle size={13} />, text: '⚠️ MACRO_STRESS' };
  return { cls: 'bg-rose-100 text-rose-800', icon: <ShieldAlert size={13} />, text: `⛔ ${veto}` };
}

function marginBadge(margin: string) {
  if (margin.includes('FULL_MARGIN')) return { cls: 'bg-emerald-600 text-emerald-50', text: '🚀 FULL MARGIN' };
  if (margin.includes('MAX_CASH')) return { cls: 'bg-cyan-600 text-cyan-50', text: '💵 MAX CASH' };
  return { cls: 'bg-neutral-200 text-neutral-700', text: '🔒 NO MARGIN' };
}

export default function EpistemicDashboard() {
  const [tickerFilter, setTickerFilter] = useState('');
  const [policy, setPolicy] = useState('BALANCED');

  const { data, isLoading, isError, refetch, isFetching } = useQuery<CompositeResponse>({
    queryKey: ['epistemicComposite', policy],
    queryFn: () => fetchComposite(DEFAULT_TARGETS),
    refetchInterval: 30_000,
    staleTime: 10_000,
  });

  const { data: densityData } = useQuery<DensityResponse>({
    queryKey: ['epistemicDensity'],
    queryFn: () => fetchDataDensity(DEFAULT_TARGETS),
    refetchInterval: 120_000,
    staleTime: 60_000,
  });

  const densitySummary = useMemo(() => {
    if (!densityData?.data || densityData.data.length === 0) return null;
    const n = densityData.data.length;
    const severe = densityData.data.filter((r) => r.status === 'SEVERE_GAP').length;
    const gap = densityData.data.filter((r) => r.status === 'GAP_FOUND').length;
    const healed = densityData.data.filter((r) => r.healed).length;
    const avgDensity = densityData.data.reduce((s, r) => s + r.density_pct, 0) / n;
    return { n, severe, gap, healed, avgDensity };
  }, [densityData]);

  const rows = useMemo(() => {
    if (!data?.data) return [];
    const f = tickerFilter.trim().toUpperCase();
    const filtered = f ? data.data.filter((r) => r.symbol.includes(f)) : data.data;
    return [...filtered].sort((a, b) => b.final_score - a.final_score);
  }, [data, tickerFilter]);

  const kpi = useMemo(() => {
    if (!data?.data || data.data.length === 0) return null;
    const n = data.data.length;
    const avgScore = data.data.reduce((s, r) => s + r.final_score, 0) / n;
    const inBuyZone = data.data.filter((r) => r.buy_gap <= 0).length;
    const vetoed = data.data.filter((r) => r.veto_flag !== 'NONE').length;
    return { n, avgScore, inBuyZone, vetoed };
  }, [data]);

  return (
    <div className="p-6 max-w-[1400px] mx-auto">
      {/* HEADER */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 pb-6 border-b border-japandi-muted-clay/30">
        <div className="flex items-center gap-3">
          <span className="p-2.5 rounded-lg bg-japandi-earth text-japandi-oat">
            <Cpu className="w-6 h-6" />
          </span>
          <div>
            <h1 className="text-xl font-bold text-japandi-earth tracking-tight">
              EPISTEMIC COMPOSITE SCORE &amp; ACTION DASHBOARD
            </h1>
            <p className="text-xs text-japandi-muted-clay mt-0.5">
              Hệ Điều Hành Phân Bổ Vốn Siêu Nhận Thức • Bayesian DAG • LAW-004/006/008/009/010
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <select
            value={policy}
            onChange={(e) => setPolicy(e.target.value)}
            className="px-3 py-2 text-xs font-semibold bg-white/70 border border-japandi-muted-clay/40 rounded-lg text-japandi-earth focus:outline-none focus:border-japandi-earth"
          >
            <option value="CONSERVATIVE">CONSERVATIVE</option>
            <option value="BALANCED">BALANCED</option>
            <option value="AGGRESSIVE">AGGRESSIVE</option>
          </select>
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-japandi-muted-clay" />
            <input
              value={tickerFilter}
              onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
              placeholder="Lọc mã CK..."
              className="w-40 pl-8 pr-3 py-2 text-xs bg-white/70 border border-japandi-muted-clay/40 rounded-lg text-japandi-earth placeholder:text-japandi-muted-clay/70 focus:outline-none focus:border-japandi-earth font-mono uppercase"
            />
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-2 px-3 py-2 text-xs font-semibold bg-white/70 border border-japandi-muted-clay/40 hover:border-japandi-earth rounded-lg text-japandi-earth transition disabled:opacity-50"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', isFetching && 'animate-spin')} />
            {isFetching ? 'SYNC...' : 'REFRESH'}
          </button>
        </div>
      </header>

      {/* ERROR */}
      {isError && (
        <div className="mt-6 p-4 rounded-lg bg-rose-50 border border-rose-300 text-rose-800 flex items-start gap-3">
          <ShieldAlert className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <div>
            <h4 className="font-bold text-sm">Lỗi kết nối REST API Gateway</h4>
            <p className="text-xs text-rose-700/80 mt-1">
              Không thể truy cập `/api/v1/epistemic/composite`. Kiểm tra backend đang chạy và cửa sổ Gate đã mở.
            </p>
          </div>
        </div>
      )}

      {/* KPI ROW */}
      {kpi && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mt-6">
          <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-widest text-japandi-muted-clay uppercase">Mã phân tích</span>
              <Database className="w-4 h-4 text-japandi-moss" />
            </div>
            <h3 className="text-2xl font-mono font-extrabold text-japandi-earth mt-2">{kpi.n}</h3>
          </div>
          <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-widest text-japandi-muted-clay uppercase">Điểm trung bình</span>
              <Activity className="w-4 h-4 text-japandi-moss" />
            </div>
            <h3 className="text-2xl font-mono font-extrabold text-japandi-earth mt-2">{kpi.avgScore.toFixed(1)}<span className="text-xs text-japandi-muted-clay">/100</span></h3>
          </div>
          <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-widest text-japandi-muted-clay uppercase">Trong vùng MUA</span>
              <TrendingUp className="w-4 h-4 text-emerald-600" />
            </div>
            <h3 className="text-2xl font-mono font-extrabold text-emerald-700 mt-2">{kpi.inBuyZone}</h3>
          </div>
          <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-widest text-japandi-muted-clay uppercase">Bị Veto</span>
              <TrendingDown className="w-4 h-4 text-rose-600" />
            </div>
            <h3 className="text-2xl font-mono font-extrabold text-rose-700 mt-2">{kpi.vetoed}</h3>
          </div>
        </div>
      )}

      {/* DATA DENSITY ROW */}
      {densitySummary && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
          <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-widest text-japandi-muted-clay uppercase">Mật độ BCTC TB</span>
              <Database className="w-4 h-4 text-japandi-moss" />
            </div>
            <h3 className={cn('text-2xl font-mono font-extrabold mt-2', densitySummary.avgDensity >= 80 ? 'text-emerald-700' : densitySummary.avgDensity >= 60 ? 'text-amber-600' : 'text-rose-700')}>
              {densitySummary.avgDensity.toFixed(1)}%
            </h3>
          </div>
          <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-widest text-japandi-muted-clay uppercase">SEVERE_GAP</span>
              <AlertTriangle className="w-4 h-4 text-rose-600" />
            </div>
            <h3 className="text-2xl font-mono font-extrabold text-rose-700 mt-2">{densitySummary.severe}</h3>
          </div>
          <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-widest text-japandi-muted-clay uppercase">GAP_FOUND</span>
              <AlertTriangle className="w-4 h-4 text-amber-600" />
            </div>
            <h3 className="text-2xl font-mono font-extrabold text-amber-700 mt-2">{densitySummary.gap}</h3>
          </div>
          <div className="bg-white/60 backdrop-blur-md p-4 rounded-lg border border-japandi-warm-sand">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-widest text-japandi-muted-clay uppercase">Đã tự vá (Healed)</span>
              <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            </div>
            <h3 className="text-2xl font-mono font-extrabold text-emerald-700 mt-2">{densitySummary.healed}</h3>
          </div>
        </div>
      )}

      {/* TABLE */}
      <div className="bg-white/60 backdrop-blur-md rounded-lg border border-japandi-warm-sand mt-6 overflow-x-auto">
        {isLoading ? (
          <div className="p-8 text-center text-sm text-japandi-muted-clay animate-pulse">Đang tải dữ liệu Epistemic...</div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-sm text-japandi-muted-clay">Không có dữ liệu cho bộ lọc hiện tại.</div>
        ) : (
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-japandi-muted-clay/40 text-[10px] font-bold text-japandi-muted-clay uppercase tracking-wider font-mono">
                <th className="py-3 px-4">Mã (Symbol)</th>
                <th className="py-3 px-2 text-center">Macro(20)</th>
                <th className="py-3 px-2 text-center">Internal(30)</th>
                <th className="py-3 px-2 text-center">Market(50)</th>
                <th className="py-3 px-2 text-center">Score(100)</th>
                <th className="py-3 px-2 text-center">Coverage</th>
                <th className="py-3 px-2 text-center">Coherence</th>
                <th className="py-3 px-2 text-center">Vốn %</th>
                <th className="py-3 px-2 text-center">Delta %</th>
                <th className="py-3 px-2 text-center">Gap Mua</th>
                <th className="py-3 px-2 text-center">Margin</th>
                <th className="py-3 px-2">Mandate</th>
                <th className="py-3 px-2">Trạng thái</th>
                <th className="py-3 px-4">Khuyến nghị</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-japandi-muted-clay/20">
              {rows.map((r) => {
                const mt = mandateTheme(r.governor_mandate);
                const vb = vetoBadge(r.veto_flag);
                const mb = marginBadge(r.margin_status);
                const inBuy = r.buy_gap <= 0;
                const scoreColor = r.final_score >= 85 ? 'text-emerald-700' : r.final_score >= 50 ? 'text-japandi-earth' : 'text-rose-700';
                return (
                  <tr key={r.symbol} className="hover:bg-japandi-warm-sand/30">
                    <td className="py-3 px-4 font-mono font-bold text-japandi-earth">{r.symbol}</td>
                    <td className="py-3 px-2 text-center font-mono text-xs">{r.macro_score.toFixed(1)}</td>
                    <td className="py-3 px-2 text-center font-mono text-xs">{r.internal_score.toFixed(1)}</td>
                    <td className="py-3 px-2 text-center font-mono text-xs">{r.market_score.toFixed(1)}</td>
                    <td className={cn('py-3 px-2 text-center font-mono font-extrabold text-sm', scoreColor)}>{r.final_score.toFixed(1)}</td>
                    <td className="py-3 px-2 text-center font-mono text-xs">{(r.coverage * 100).toFixed(0)}%</td>
                    <td className="py-3 px-2 text-center font-mono text-xs">{(r.coherence * 100).toFixed(0)}%</td>
                    <td className="py-3 px-2 text-center font-mono text-xs">{r.target_alloc_pct.toFixed(1)}%</td>
                    <td className={cn('py-3 px-2 text-center font-mono text-xs font-bold', r.action_delta_pct >= 0 ? 'text-emerald-700' : 'text-rose-700')}>
                      {r.action_delta_pct >= 0 ? '+' : ''}{r.action_delta_pct.toFixed(1)}%
                    </td>
                    <td className={cn('py-3 px-2 text-center font-mono text-xs font-bold', inBuy ? 'text-emerald-700' : 'text-japandi-muted-clay')}>
                      {inBuy ? 'IN BUY ZONE' : `+${r.buy_gap.toFixed(1)} pt`}
                    </td>
                    <td className="py-3 px-2 text-center">
                      <span className={cn('px-2 py-0.5 rounded text-[10px] font-bold', mb.cls)}>{mb.text}</span>
                    </td>
                    <td className="py-3 px-2">
                      <span className={cn('px-2 py-0.5 rounded text-[10px] font-bold whitespace-nowrap', mt.badge)} title={mt.label}>{r.governor_mandate}</span>
                    </td>
                    <td className="py-3 px-2">
                      <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold', vb.cls)}>
                        {vb.icon} {vb.text}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-xs text-japandi-earth" title={r.why_drivers}>{r.recommendation}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* FOOTER NOTE */}
      <p className="mt-4 text-[11px] text-japandi-muted-clay flex items-center gap-1.5">
        <ShieldCheck className="w-3.5 h-3.5" />
        Dữ liệu Local-First (SQLite) — phản hồi &lt; 15ms. Policy hiện tại: <strong>{policy}</strong>. Tự làm mới mỗi 30s.
      </p>
    </div>
  );
}
