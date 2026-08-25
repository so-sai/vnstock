import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  LayoutGrid,
  Activity,
  AlertTriangle,
  RefreshCw,
  Search,
  ChevronDown,
  ChevronUp,
  Gauge,
  Zap,
  PieChart,
  Clock,
  XCircle,
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const API_BASE = import.meta.env.DEV ? '/api' : 'http://localhost:17039/api';

interface VN20Constituent {
  symbol: string;
  sector: string;
  epistemic_score: number;
  structural_score: number;
  behavioural_score: number;
  outcome_score: number;
  coverage: number;
  coherence: number;
  target_alloc_pct: number;
  is_selected: boolean;
}

interface SilentThrottleStatus {
  probe_symbol: string;
  is_throttled: boolean;
  consecutive_empty_payloads: number;
  backoff_factor_sec: number;
}

interface VN20Response {
  status: string;
  index: string;
  policy: string;
  count: number;
  built_at: string;
  generated_at: string;
  data_density_avg: number;
  fallback_active_source: string;
  silent_throttle_status: SilentThrottleStatus;
  sector_distribution: Record<string, number>;
  index_constituents: VN20Constituent[];
  timestamp: string;
}

function EpistemicBadge({ score }: { score: number }) {
  const color =
    score >= 0.75
      ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
      : score >= 0.50
        ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
        : 'bg-red-500/15 text-red-400 border-red-500/30';
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border',
        color,
      )}
    >
      {score.toFixed(2)}
    </span>
  );
}

function SectorBar({ sector, count, max }: { sector: string; count: number; max: number }) {
  const pct = max > 0 ? (count / max) * 100 : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 text-right text-japandi-earth/75 truncate">{sector}</span>
      <div className="flex-1 h-2 bg-japandi-oat rounded-full overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-japandi-oat to-warm-sand rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-6 text-right text-japandi-earth font-mono">{count}</span>
    </div>
  );
}

export default function VN20IndexDashboard() {
  const [search, setSearch] = useState('');
  const [sectorFilter, setSectorFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState<keyof VN20Constituent>('epistemic_score');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [liveThrottleStatus, setLiveThrottleStatus] = useState<SilentThrottleStatus | null>(null);
  const [sseConnected, setSseConnected] = useState(false);

  const { data, isLoading, error, refetch } = useQuery<VN20Response>({
    queryKey: ['vn20-index'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/v1/epistemic/vn20`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    refetchInterval: sseConnected ? 300_000 : 60_000,
    staleTime: sseConnected ? 150_000 : 30_000,
  });

  // SSE listener for real-time throttle alerts
  useEffect(() => {
    const eventSource = new EventSource(`${API_BASE}/v1/epistemic/vn20/stream`);

    eventSource.onopen = () => {
      setSseConnected(true);
    };

    eventSource.addEventListener("throttle_update", (event) => {
      try {
        const data = JSON.parse(event.data);
        setLiveThrottleStatus(data);
      } catch {
        // Ignore parse errors from malformed SSE events
      }
    });

    eventSource.onerror = () => {
      // SSE auto-reconnects by default; revert to faster polling while disconnected
      setSseConnected(false);
    };

    return () => {
      setSseConnected(false);
      eventSource.close();
    };
  }, []);

  const filtered = useMemo(() => {
    if (!data?.index_constituents) return [];
    let list = [...data.index_constituents];
    if (sectorFilter !== 'all') {
      list = list.filter((c) => c.sector === sectorFilter);
    }
    if (search.trim()) {
      const q = search.trim().toUpperCase();
      list = list.filter((c) => c.symbol.includes(q) || c.sector.includes(q));
    }
    list.sort((a, b) => {
      const av = a[sortBy];
      const bv = b[sortBy];
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av;
      }
      const as = String(av ?? '').localeCompare(String(bv ?? ''));
      return sortDir === 'asc' ? as : -as;
    });
    return list;
  }, [data, search, sectorFilter, sortBy, sortDir]);

  const sectors = useMemo(() => {
    if (!data?.sector_distribution) return [];
    return Object.entries(data.sector_distribution).sort((a, b) => b[1] - a[1]);
  }, [data]);

  const maxSector = useMemo(() => {
    if (!data?.sector_distribution) return 1;
    return Math.max(1, ...Object.values(data.sector_distribution));
  }, [data]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-japandi-earth/75">
        <RefreshCw className="w-6 h-6 animate-spin mr-2" />
        Loading PTCK_VN20...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-400">
        <XCircle className="w-5 h-5 mr-2" />
        Failed to load VN20 data — {error.message}
      </div>
    );
  }

  if (!data) return null;

  const activeThrottle = liveThrottleStatus ?? data.silent_throttle_status;

  return (
    <div className="space-y-4 p-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-japandi-oat tracking-tight">
            {data.index}
          </h1>
          <p className="text-sm text-japandi-earth/75 mt-1">
            {data.policy} · {data.count} constituents · Built {data.built_at}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border',
              sseConnected
                ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                : 'bg-amber-500/10 text-amber-400 border-amber-500/30',
            )}
            title={sseConnected ? 'SSE live · poll every 300s' : 'SSE down · poll every 60s'}
          >
            <span
              className={cn(
                'w-1.5 h-1.5 rounded-full',
                sseConnected ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400',
              )}
            />
            {sseConnected ? 'LIVE SSE' : 'POLL 60s'}
          </span>
          <button
            onClick={() => refetch()}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white border border-japandi-warm-sand text-japandi-earth text-sm hover:bg-japandi-oat transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-white/70 border border-japandi-warm-sand rounded-xl p-3">
          <div className="flex items-center gap-2 text-japandi-earth/75 text-xs mb-1">
            <Gauge className="w-3.5 h-3.5" /> Data Density
          </div>
          <div className="text-xl font-bold text-japandi-oat">
            {data.data_density_avg}%
          </div>
        </div>
        <div className="bg-white/70 border border-japandi-warm-sand rounded-xl p-3">
          <div className="flex items-center gap-2 text-japandi-earth/75 text-xs mb-1">
            <Zap className="w-3.5 h-3.5" /> Fallback Source
          </div>
          <div className="text-xl font-bold text-warm-sand uppercase">
            {data.fallback_active_source}
          </div>
        </div>
        <div className="bg-white/70 border border-japandi-warm-sand rounded-xl p-3">
          <div className="flex items-center gap-2 text-japandi-earth/75 text-xs mb-1">
            <Activity className="w-3.5 h-3.5" /> Silent Throttle
          </div>
          <div
            className={cn(
              'text-xl font-bold',
              activeThrottle?.is_throttled
                ? 'text-red-400'
                : 'text-emerald-400',
            )}
          >
            {activeThrottle?.is_throttled ? 'ACTIVE' : 'CLEAR'}
          </div>
        </div>
        <div className="bg-white/70 border border-japandi-warm-sand rounded-xl p-3">
          <div className="flex items-center gap-2 text-japandi-earth/75 text-xs mb-1">
            <Clock className="w-3.5 h-3.5" /> Generated
          </div>
          <div className="text-sm font-mono text-japandi-earth mt-1">
            {data.generated_at ? new Date(data.generated_at).toLocaleTimeString('vi-VN') : '—'}
          </div>
        </div>
      </div>

      {/* Sector Distribution */}
      <div className="bg-white/70 border border-japandi-warm-sand rounded-xl p-4">
        <div className="flex items-center gap-2 text-japandi-earth/75 text-sm mb-3">
          <PieChart className="w-4 h-4" /> Sector Distribution
        </div>
        <div className="space-y-1.5">
          {sectors.map(([sector, count]) => (
            <SectorBar key={sector} sector={sector} count={count} max={maxSector} />
          ))}
        </div>
      </div>

      {/* Silent Throttle Alert */}
      {activeThrottle?.is_throttled && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-3 flex items-center gap-3 text-red-300 text-sm">
          <AlertTriangle className="w-5 h-5 shrink-0" />
          <span>
            Silent Throttle detected on <strong>{activeThrottle.probe_symbol}</strong> —{' '}
            {activeThrottle.consecutive_empty_payloads} consecutive empty payloads.
            Backoff factor: {activeThrottle.backoff_factor_sec}s.
          </span>
        </div>
      )}

      {/* Constituent Table */}
      <div className="bg-white/70 border border-japandi-warm-sand rounded-xl overflow-hidden">
        {/* Toolbar */}
        <div className="flex items-center gap-3 p-3 border-b border-japandi-muted-clay flex-wrap">
          <div className="flex items-center gap-2 text-japandi-earth/75 text-sm">
            <LayoutGrid className="w-4 h-4" /> Constituents
            <span className="text-japandi-earth/60">({filtered.length})</span>
          </div>
          <div className="flex-1" />
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-japandi-earth/60" />
            <input
              type="text"
              placeholder="Search symbol or sector..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-8 pr-3 py-1.5 bg-white border border-japandi-muted-clay rounded-lg text-sm text-japandi-earth placeholder-japandi-earth/50 focus:outline-none focus:border-japandi-oat/50 w-48"
            />
          </div>
          <select
            value={sectorFilter}
            onChange={(e) => setSectorFilter(e.target.value)}
            className="px-2 py-1.5 bg-white border border-japandi-muted-clay rounded-lg text-sm text-japandi-earth focus:outline-none focus:border-japandi-oat/50"
          >
            <option value="all">All Sectors</option>
            {sectors.map(([s]) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-japandi-earth/75 text-xs uppercase tracking-wider border-b border-japandi-muted-clay">
                {[
                  { key: 'symbol', label: 'Symbol' },
                  { key: 'sector', label: 'Sector' },
                  { key: 'epistemic_score', label: 'Epistemic' },
                  { key: 'structural_score', label: 'Structural' },
                  { key: 'behavioural_score', label: 'Behavioural' },
                  { key: 'outcome_score', label: 'Outcome' },
                  { key: 'coverage', label: 'Coverage' },
                  { key: 'coherence', label: 'Coherence' },
                  { key: 'target_alloc_pct', label: 'Alloc %' },
                ].map((col) => (
                  <th
                    key={col.key}
                    className="px-3 py-2 text-left cursor-pointer hover:text-japandi-oat transition-colors select-none"
                    onClick={() => {
                      const k = col.key as keyof VN20Constituent;
                      if (sortBy === k) {
                        setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
                      } else {
                        setSortBy(k);
                        setSortDir('desc');
                      }
                    }}
                  >
                    <div className="flex items-center gap-1">
                      {col.label}
                      {sortBy === col.key && (
                        sortDir === 'desc' ? (
                          <ChevronDown className="w-3 h-3" />
                        ) : (
                          <ChevronUp className="w-3 h-3" />
                        )
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr
                  key={c.symbol}
                  className="border-b border-japandi-muted-clay/50 hover:bg-japandi-oat transition-colors"
                >
                  <td className="px-3 py-2 font-mono font-semibold text-japandi-oat">
                    {c.symbol}
                  </td>
                  <td className="px-3 py-2 text-japandi-earth">{c.sector}</td>
                  <td className="px-3 py-2">
                    <EpistemicBadge score={c.epistemic_score} />
                  </td>
                  <td className="px-3 py-2 text-japandi-earth font-mono">
                    {c.structural_score.toFixed(3)}
                  </td>
                  <td className="px-3 py-2 text-japandi-earth font-mono">
                    {c.behavioural_score.toFixed(3)}
                  </td>
                  <td className="px-3 py-2 text-japandi-earth font-mono">
                    {c.outcome_score.toFixed(3)}
                  </td>
                  <td className="px-3 py-2 text-japandi-earth font-mono">
                    {(c.coverage * 100).toFixed(1)}%
                  </td>
                  <td className="px-3 py-2 text-japandi-earth font-mono">
                    {(c.coherence * 100).toFixed(1)}%
                  </td>
                  <td className="px-3 py-2 text-japandi-earth font-mono">
                    {c.target_alloc_pct}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}