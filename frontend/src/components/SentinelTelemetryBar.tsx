/**
 * SentinelTelemetryBar.tsx — HCI-friendly Data Pipeline Health Bar.
 *
 * Shows:
 *   - Tier indicator (color-coded, with tooltip explaining each tier)
 *   - Trust score (Bayesian confidence badge)
 *   - Staleness with human-readable time
 *   - Force HDR lock (with explanation tooltip)
 *   - Provider attribution
 *
 * Auto-synced labels from Backend CLI_LABEL_MAP via useDictionary().
 * Refetch interval from Zustand dashboardStore (selector subscription).
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useDictionary } from '../lib/dictionary';
import { AbbrTooltip, ConfidenceBadge, StatusIndicator } from './ui-components';
import { useSentinelStore } from '../stores/dashboardStore';

interface SentinelSymbol {
  symbol: string;
  source: string;
  provider: string;
  fallback: boolean;
  is_synthetic: boolean;
  staleness_hours: number;
  api_status: string;
  trust_score: number;
  force_hdr: number | null;
}

interface SentinelStatus {
  timestamp: string;
  date: string;
  symbols: SentinelSymbol[];
  overall_synthetic: boolean;
  overall_fallback: boolean;
  error?: string;
}

const TIER_INFO: Record<string, { vi: string; en: string; status: 'healthy' | 'warning' | 'critical' }> = {
  PRIMARY: { vi: 'Nguồn Chính', en: 'Primary Source', status: 'healthy' },
  BACKUP_PROVIDER: { vi: 'Nguồn Dự phòng', en: 'Backup Provider', status: 'warning' },
  CACHE: { vi: 'Bộ nhớ đệm', en: 'Cache', status: 'warning' },
  CACHE_STALE: { vi: 'Đệm cũ', en: 'Stale Cache', status: 'warning' },
  BOOTSTRAP: { vi: 'Dữ liệu Seed', en: 'Bootstrap Data', status: 'critical' },
  SYNTHETIC: { vi: 'Dữ liệu Tổng hợp', en: 'Synthetic Data', status: 'critical' },
};

function formatStaleness(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)} phút`;
  if (hours < 24) return `${hours.toFixed(1)} giờ`;
  return `${(hours / 24).toFixed(1)} ngày`;
}

export default function SentinelTelemetryBar() {
  const { t } = useDictionary();
  const { refetchInterval } = useSentinelStore();

  const { data, isLoading, error } = useQuery<SentinelStatus>({
    queryKey: ['sentinel-status'],
    queryFn: async () => api.get<SentinelStatus>('/v1/sentinel/status'),
    refetchInterval,
  });

  // Loading state
  if (isLoading) return (
    <div className="flex items-center gap-2 px-3 py-2 bg-japandi-surface rounded-lg border border-japandi-border">
      <StatusIndicator status="unknown" pulse />
      <span className="text-xs text-japandi-text-dim">{t('Loading')}</span>
    </div>
  );

  // Error state
  if (error || !data) return (
    <div className="flex items-center gap-2 px-3 py-2 bg-red-900/20 rounded-lg border border-red-500/30">
      <StatusIndicator status="critical" />
      <span className="text-xs text-red-400">{t('Sentinel unavailable')}</span>
    </div>
  );

  const isSynthetic = data.overall_synthetic;
  const isFallback = data.overall_fallback;
  const firstSymbol = data.symbols[0];
  const tier = firstSymbol?.source || 'UNKNOWN';
  const trustScore = firstSymbol?.trust_score ?? 0.5;
  const tierInfo = TIER_INFO[tier] ?? { vi: tier, en: tier, status: 'unknown' as const };

  // Determine overall health
  const overallHealth = isSynthetic ? 'critical' : isFallback ? 'warning' : 'healthy';

  return (
    <div className={`flex items-center gap-3 px-3 py-2 rounded-lg border transition-colors ${
      isSynthetic
        ? 'bg-red-900/20 border-red-500/40'
        : isFallback
          ? 'bg-amber-900/20 border-amber-500/40'
          : 'bg-japandi-surface border-japandi-border'
    }`}>
      {/* Status Indicator */}
      <StatusIndicator status={overallHealth} pulse={isSynthetic} />

      {/* Tier with Tooltip */}
      <div className="flex items-center gap-1.5">
        <span className={`text-xs font-medium ${
          isSynthetic ? 'text-red-400' : isFallback ? 'text-amber-400' : 'text-japandi-text'
        }`}>
          <AbbrTooltip abbr={tier} showDetail={false}>
            {t(tierInfo.vi)}
          </AbbrTooltip>
        </span>
      </div>

      {/* Trust Score — HCI Badge */}
      <ConfidenceBadge
        confidence={trustScore}
        size="sm"
        showValue
        showLabel={false}
      />

      {/* Staleness — Human-readable */}
      {firstSymbol && firstSymbol.staleness_hours > 0 && (
        <div className="flex items-center gap-1">
          <span className="text-xs text-japandi-text-dim">
            {t('Cũ')}: {formatStaleness(firstSymbol.staleness_hours)}
          </span>
        </div>
      )}

      {/* Force HDR Lock — with explanation */}
      {isSynthetic && (
        <div className="flex items-center gap-1.5 px-2 py-0.5 bg-red-900/30 rounded border border-red-500/30">
          <span className="text-xs font-bold text-red-400 animate-pulse">
            <AbbrTooltip abbr="HDR" showDetail>
              {t('Khóa HDR')}
            </AbbrTooltip>
          </span>
          <span className="text-[10px] text-red-400/70">— {t('Không giao dịch')}</span>
        </div>
      )}

      {/* Provider attribution */}
      {firstSymbol?.provider && firstSymbol.provider !== 'KBS' && (
        <span className="text-[10px] text-japandi-text-dim">
          {t('qua')} {firstSymbol.provider}
        </span>
      )}

      {/* Fallback indicator */}
      {isFallback && !isSynthetic && (
        <span className="text-[10px] px-1.5 py-0.5 bg-amber-900/30 rounded text-amber-400">
          {t('Dự phòng')} — {t('Giảm tin cậy')} 5%
        </span>
      )}
    </div>
  );
}
