/**
 * RejectedSignalsArchive.tsx — HCI-friendly Counterfactual Decision Display.
 *
 * Shows rejected signals with:
 *   - Reason tooltips explaining why each signal was rejected
 *   - Counterfactual comparison (what would have happened)
 *   - DOC summary with human-readable explanation
 *   - Accepted alternative comparison
 *   - Confidence evolution (prior → posterior)
 *
 * All abbreviations have tooltips. All metrics have human-readable context.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useDictionary } from '../lib/dictionary';
import { AbbrTooltip, ConfidenceBadge, DecisionCard, MetricRow } from './ui-components';
import { useRejectedStore } from '../stores/dashboardStore';

interface RejectedSignal {
  id: number;
  timestamp: string;
  ticker: string;
  signal_type: string;
  rejection_reason: string;
  regime_score: number;
  adx_value: number;
  prior_belief: number;
  posterior_belief: number;
  evaluation_horizon: string;
  status: string;
  simulated_exit_5d: number | null;
  simulated_exit_10d: number | null;
  simulated_exit_20d: number | null;
  alternative_return_5d: number | null;
  alternative_return_10d: number | null;
  alternative_return_20d: number | null;
  accepted_alternative: string | null;
  information_gain: number;
  surprise: number;
  cumulative_ig: number;
}

interface RejectedArchive {
  entries: RejectedSignal[];
  count: number;
  cumulative_information_gain: number;
  doc_summary: {
    doc_index?: number;
    wins?: number;
    losses?: number;
    n_pairs?: number;
  };
  error?: string;
}

const REASON_CONFIG: Record<string, {
  vi: string;
  color: string;
  bgColor: string;
  description_vi: string;
}> = {
  GOVERNOR_LOCK_HDR: {
    vi: 'Khóa HDR',
    color: 'text-red-400',
    bgColor: 'bg-red-900/30 border-red-500/30',
    description_vi: 'Governor phát hiện thị trường không đủ tin cậy để giao dịch. HDR = 0%.',
  },
  ADX_HAIRCUT: {
    vi: 'ADX quá thấp',
    color: 'text-amber-400',
    bgColor: 'bg-amber-900/30 border-amber-500/30',
    description_vi: 'Chỉ số xu hướng (ADX) dưới ngưỡng 20. Thị trường sideway, không có xu hướng rõ ràng.',
  },
  BUYING_POWER_INSUFFICIENT: {
    vi: 'Thiếu vốn',
    color: 'text-blue-400',
    bgColor: 'bg-blue-900/30 border-blue-500/30',
    description_vi: 'Không đủ sức mua (buying power) để vào vị thế này.',
  },
  NO_PRICE_DATA: {
    vi: 'Thiếu dữ liệu giá',
    color: 'text-gray-400',
    bgColor: 'bg-gray-900/30 border-gray-500/30',
    description_vi: 'Không có dữ liệu giá thời gian thực cho mã này.',
  },
};

function formatReturn(val: number | null, suffix = ''): string {
  if (val === null) return '—';
  const sign = val > 0 ? '+' : '';
  return `${sign}${val.toFixed(2)}%${suffix}`;
}

export default function RejectedSignalsArchive() {
  const { t } = useDictionary();
  const { refetchInterval, reasonFilter, setReasonFilter } = useRejectedStore();

  const { data, isLoading } = useQuery<RejectedArchive>({
    queryKey: ['rejected-archive', reasonFilter],
    queryFn: async () => {
      const url = reasonFilter
        ? `/v1/rejected/archive?limit=50&reason=${reasonFilter}`
        : '/v1/rejected/archive?limit=50';
      return await api.get<RejectedArchive>(url);
    },
    refetchInterval,
  });

  if (isLoading) return (
    <DecisionCard title="Lưu trữ Tín hiệu Bị từ chối">
      <div className="space-y-3">
        {[1, 2, 3].map(i => (
          <div key={i} className="h-20 bg-japandi-border rounded animate-pulse" />
        ))}
      </div>
    </DecisionCard>
  );

  if (!data || data.error) return (
    <DecisionCard title="Lưu trữ Tín hiệu Bị từ chối">
      <p className="text-sm text-japandi-text-dim text-center py-4">
        {t('Chưa có dữ liệu tín hiệu bị từ chối')}
      </p>
    </DecisionCard>
  );

  const docIndex = data.doc_summary?.doc_index ?? 0;
  const wins = data.doc_summary?.wins ?? 0;
  const losses = data.doc_summary?.losses ?? 0;

  return (
    <DecisionCard
      title="Lưu trữ Tín hiệu Bị từ chối"
      confidence={undefined}
      action={
        <span className="text-xs text-japandi-text-dim">
          {data.count} {t('tín hiệu')}
        </span>
      }
    >
      <div className="space-y-4">
        {/* DOC Summary — Human-readable */}
        <div className="p-3 bg-japandi-bg rounded-lg border border-japandi-border">
          <div className="grid grid-cols-3 gap-3">
            <div>
              <p className="text-xs text-japandi-text-dim mb-1">
                <AbbrTooltip abbr="DOC" showDetail={false}>
                  {t('Chỉ số Chi phí Cơ hội')}
                </AbbrTooltip>
              </p>
              <p className={`text-lg font-bold font-mono ${
                docIndex > 0 ? 'text-emerald-400' : docIndex < 0 ? 'text-red-400' : 'text-japandi-text-dim'
              }`}>
                {docIndex > 0 ? '+' : ''}{docIndex.toFixed(4)}
              </p>
            </div>
            <div>
              <p className="text-xs text-japandi-text-dim mb-1">
                <AbbrTooltip abbr="IG" showDetail={false}>
                  {t('Tích lũy Thông tin')}
                </AbbrTooltip>
              </p>
              <p className="text-lg font-bold font-mono text-japandi-text">
                {(data.cumulative_information_gain ?? 0).toFixed(4)}
              </p>
            </div>
            <div>
              <p className="text-xs text-japandi-text-dim mb-1">{t('Tỷ lệ thắng/thua')}</p>
              <p className="text-lg font-bold font-mono">
                <span className="text-emerald-400">{wins}W</span>
                {' / '}
                <span className="text-red-400">{losses}L</span>
              </p>
            </div>
          </div>

          {/* DOC explanation */}
          {docIndex < 0 && (
            <div className="mt-2 p-2 bg-red-900/20 rounded border border-red-500/20">
              <p className="text-[11px] text-red-400 leading-relaxed">
                {t('Hệ thống đang từ chối sai tín hiệu. Những tín hiệu bị loại bỏ '
                  + 'đang cho lợi nhuận tốt hơn tín hiệu được chấp nhận. '
                  + 'Cần xem xét lại tiêu chí lọc.')}
              </p>
            </div>
          )}
        </div>

        {/* Reason filters */}
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => setReasonFilter('')}
            className={`px-2 py-1 text-xs rounded border transition-colors ${
              reasonFilter === ''
                ? 'bg-japandi-accent text-white border-japandi-accent'
                : 'bg-japandi-bg text-japandi-text-dim border-japandi-border hover:bg-japandi-surface'
            }`}
          >
            {t('Tất cả')}
          </button>
          {Object.entries(REASON_CONFIG).map(([key, config]) => (
            <button
              key={key}
              onClick={() => setReasonFilter(key)}
              className={`px-2 py-1 text-xs rounded border transition-colors ${
                reasonFilter === key
                  ? `${config.bgColor} ${config.color} border-current`
                  : 'bg-japandi-bg text-japandi-text-dim border-japandi-border hover:bg-japandi-surface'
              }`}
            >
              {t(config.vi)}
            </button>
          ))}
        </div>

        {/* Signal entries */}
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {data.entries.map((e) => {
            const reasonConfig = REASON_CONFIG[e.rejection_reason] ?? {
              vi: e.rejection_reason,
              color: 'text-gray-400',
              bgColor: 'bg-gray-900/30',
              description_vi: '',
            };

            return (
              <div key={e.id} className="p-3 bg-japandi-bg rounded-lg border border-japandi-border">
                {/* Header: Symbol + Reason */}
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-bold text-japandi-text">{e.ticker}</span>
                    <span className={`px-1.5 py-0.5 text-xs rounded border ${reasonConfig.bgColor} ${reasonConfig.color}`}>
                      <AbbrTooltip abbr={e.rejection_reason.split('_')[0]} showDetail={false}>
                        {t(reasonConfig.vi)}
                      </AbbrTooltip>
                    </span>
                    {e.accepted_alternative && (
                      <span className="text-xs text-japandi-text-dim">
                        → {e.accepted_alternative}
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-japandi-text-dim">{e.status}</span>
                </div>

                {/* Reason tooltip */}
                {reasonConfig.description_vi && (
                  <p className="text-[11px] text-japandi-text-dim mb-2 italic">
                    {t(reasonConfig.description_vi)}
                  </p>
                )}

                {/* Metrics row */}
                <div className="grid grid-cols-4 gap-2 text-xs mb-2">
                  <div>
                    <span className="text-japandi-text-dim">
                      <AbbrTooltip abbr="CUSUM" showDetail={false}>
                        {t('Điểm số')}
                      </AbbrTooltip>
                      :{' '}
                    </span>
                    <span className="font-mono">{e.regime_score.toFixed(3)}</span>
                  </div>
                  <div>
                    <span className="text-japandi-text-dim">
                      <AbbrTooltip abbr="ADX" showDetail={false}>
                        {t('ADX')}
                      </AbbrTooltip>
                      :{' '}
                    </span>
                    <span className="font-mono">{e.adx_value.toFixed(1)}</span>
                  </div>
                  <div>
                    <span className="text-japandi-text-dim">
                      <AbbrTooltip abbr="IG" showDetail={false}>
                        {t('IG')}
                      </AbbrTooltip>
                      :{' '}
                    </span>
                    <span className="font-mono">{e.information_gain.toFixed(4)}</span>
                  </div>
                  <div>
                    <span className="text-japandi-text-dim">
                      <AbbrTooltip abbr="HDR" showDetail={false}>
                        {t('Tin')}
                      </AbbrTooltip>
                      :{' '}
                    </span>
                    <span className="font-mono">{e.prior_belief.toFixed(3)}</span>
                  </div>
                </div>

                {/* Counterfactual comparison */}
                {(e.simulated_exit_5d !== null || e.alternative_return_5d !== null) && (
                  <div className="pt-2 border-t border-japandi-border">
                    <p className="text-[10px] text-japandi-text-dim mb-1">
                      {t('So sánh Counterfactual')}:
                    </p>
                    <div className="grid grid-cols-3 gap-2 text-xs">
                      <div className="p-1.5 bg-japandi-surface rounded">
                        <span className="text-[10px] text-japandi-text-dim block">{t('Mô phỏng')}</span>
                        <span className={`font-mono font-bold ${
                          (e.simulated_exit_5d ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400'
                        }`}>
                          {formatReturn(e.simulated_exit_5d)}
                        </span>
                      </div>
                      <div className="p-1.5 bg-japandi-surface rounded">
                        <span className="text-[10px] text-japandi-text-dim block">{t('Thay thế')}</span>
                        <span className={`font-mono font-bold ${
                          (e.alternative_return_5d ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400'
                        }`}>
                          {formatReturn(e.alternative_return_5d)}
                        </span>
                      </div>
                      <div className="p-1.5 bg-japandi-surface rounded">
                        <span className="text-[10px] text-japandi-text-dim block">{t('Hiệu số')}</span>
                        <span className={`font-mono font-bold ${
                          ((e.alternative_return_5d ?? 0) - (e.simulated_exit_5d ?? 0)) > 0
                            ? 'text-emerald-400' : 'text-red-400'
                        }`}>
                          {formatReturn(
                            (e.alternative_return_5d ?? 0) - (e.simulated_exit_5d ?? 0)
                          )}
                        </span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </DecisionCard>
  );
}
