/**
 * GovernorBeliefBridge.tsx — HCI-friendly Bayesian Confidence Dashboard.
 *
 * Shows:
 *   - Calibration Penalty (with tooltip explaining each component)
 *   - Effective Trust (Bayesian posterior)
 *   - DOC Index (Decision Opportunity Cost)
 *   - Sharpe Live Smoothed
 *   - Action badge (NONE/SCALE/ABORT) with color coding
 *   - Reason (human-readable explanation)
 *
 * All metrics have tooltips explaining what they mean for the user's decision.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useDictionary } from '../lib/dictionary';
import { AbbrTooltip, ConfidenceBadge, DecisionCard, MetricRow } from './ui-components';
import { useBeliefStore } from '../stores/dashboardStore';

interface QuantStatsBelief {
  sharpe_live_smoothed: number;
  sharpe_vs_random: number;
  outlier_win_ratio: number;
  calibration_penalty: number;
  action: string;
  reason: string;
  doc_index: number;
  doc_wins: number;
  doc_losses: number;
  cumulative_information_gain: number;
  live: Record<string, number>;
  rejected: Record<string, number>;
}

interface BeliefMeta {
  symbols: Record<string, {
    trust_score: number;
    data_quality: number;
    calibration_penalty: number;
    novelty: number;
    effective_trust: number;
  }>;
}

const ACTION_CONFIG: Record<string, {
  vi: string;
  color: string;
  bgColor: string;
  description_vi: string;
}> = {
  NONE: {
    vi: 'Bình thường',
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-900/30 border-emerald-500/40',
    description_vi: 'Hệ thống hoạt động bình thường, không cần can thiệp.',
  },
  SCALE: {
    vi: 'Giảm quy mô',
    color: 'text-amber-400',
    bgColor: 'bg-amber-900/30 border-amber-500/40',
    description_vi: 'Độ tin cậy đang giảm. Giảm quy mô giao dịch để bảo vệ vốn.',
  },
  ABORT: {
    vi: 'Dừng giao dịch',
    color: 'text-red-400',
    bgColor: 'bg-red-900/30 border-red-500/40',
    description_vi: 'Độ tin cậy quá thấp. Dừng toàn bộ giao dịch mới.',
  },
};

export default function GovernorBeliefBridge() {
  const { t } = useDictionary();
  const { refetchInterval } = useBeliefStore();

  const { data: quantstats, isLoading: qsLoading } = useQuery<QuantStatsBelief>({
    queryKey: ['belief-quantstats'],
    queryFn: async () => api.get<QuantStatsBelief>('/v1/belief/quantstats?window=30'),
    refetchInterval,
  });

  const { data: meta } = useQuery<BeliefMeta>({
    queryKey: ['belief-meta'],
    queryFn: async () => api.get<BeliefMeta>('/v1/belief/meta'),
    refetchInterval,
  });

  if (qsLoading) return (
    <DecisionCard title="Cầu tin cậy Governor">
      <div className="space-y-3">
        {[1, 2, 3].map(i => (
          <div key={i} className="h-8 bg-japandi-border rounded animate-pulse" />
        ))}
      </div>
    </DecisionCard>
  );

  if (!quantstats) return null;

  const penalty = quantstats.calibration_penalty ?? 0;
  const trust = meta?.symbols?.VNINDEX?.effective_trust ?? 0.5;
  const docIndex = quantstats.doc_index ?? 0;
  const cumIG = quantstats.cumulative_information_gain ?? 0;
  const action = quantstats.action ?? 'NONE';
  const actionConfig = ACTION_CONFIG[action] ?? ACTION_CONFIG.NONE;

  return (
    <DecisionCard
      title="Cầu tin cậy Governor"
      confidence={trust}
      action={
        <span className={`px-2 py-0.5 text-xs font-bold rounded border ${actionConfig.bgColor} ${actionConfig.color}`}>
          {t(actionConfig.vi)}
        </span>
      }
    >
      <div className="space-y-3">
        {/* Effective Trust — Primary metric */}
        <div className="p-3 bg-japandi-bg rounded-lg border border-japandi-border">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-japandi-text-dim">
              <AbbrTooltip abbr="HDR" showDetail={false}>
                {t('Độ tin cậy hiệu quả')}
              </AbbrTooltip>
            </span>
            <ConfidenceBadge confidence={trust} size="md" showLabel />
          </div>
          <p className="text-[11px] text-japandi-text-dim leading-relaxed">
            {t('Độ tin cậy sau khi hiệu chỉnh bởi tất cả thành phần calibration. '
              + 'Nếu thấp hơn 30%, hệ thống sẽ tạm ngưng giao dịch.')}
          </p>
        </div>

        {/* Calibration Penalty */}
        <div className="p-3 bg-japandi-bg rounded-lg border border-japandi-border">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-japandi-text-dim">
              <AbbrTooltip abbr="CUSUM" showDetail={false}>
                {t('Phạt hiệu chỉnh')}
              </AbbrTooltip>
            </span>
            <span className={`text-sm font-mono font-bold ${
              penalty >= 0.7 ? 'text-red-400' : penalty >= 0.4 ? 'text-amber-400' : 'text-emerald-400'
            }`}>
              {(penalty * 100).toFixed(1)}%
            </span>
          </div>
          <p className="text-[11px] text-japandi-text-dim leading-relaxed">
            {t('Tổng các hình phạt từ: sai lệch mô hình, chất lượng dữ liệu, '
              + 'độ mới của chiến lược, và thực thi. Phạt cao = mô hình không đáng tin.')}
          </p>
        </div>

        {/* DOC Index */}
        <div className="p-3 bg-japandi-bg rounded-lg border border-japandi-border">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-japandi-text-dim">
              <AbbrTooltip abbr="DOC" showDetail={false}>
                {t('Chỉ số Chi phí Cơ hội')}
              </AbbrTooltip>
            </span>
            <span className={`text-sm font-mono font-bold ${
              docIndex > 0 ? 'text-emerald-400' : docIndex < 0 ? 'text-red-400' : 'text-japandi-text-dim'
            }`}>
              {docIndex > 0 ? '+' : ''}{docIndex.toFixed(4)}
            </span>
          </div>
          <div className="flex items-center gap-3 text-[11px]">
            <span>
              <span className="text-emerald-400">{quantstats.doc_wins ?? 0}W</span>
              {' thắng'}
            </span>
            <span>
              <span className="text-red-400">{quantstats.doc_losses ?? 0}L</span>
              {' thua'}
            </span>
            {docIndex < 0 && (
              <span className="text-red-400 font-medium">
                — {t('Đang bỏ lỡ cơ hội')}
              </span>
            )}
          </div>
          <p className="text-[11px] text-japandi-text-dim leading-relaxed mt-1">
            {t('So sánh lợi nhuận của tín hiệu bị từ chối với tín hiệu được chấp nhận. '
              + 'DOC < 0 nghĩa là hệ thống đang từ chối sai.')}
          </p>
        </div>

        {/* Sharpe + IG row */}
        <div className="grid grid-cols-2 gap-2">
          <div className="p-2 bg-japandi-bg rounded-lg">
            <MetricRow
              label="Sharpe"
              abbrKey="Sharpe"
              value={quantstats.sharpe_live_smoothed ?? 0}
              color={
                (quantstats.sharpe_live_smoothed ?? 0) >= 1.0 ? 'text-emerald-400' :
                (quantstats.sharpe_live_smoothed ?? 0) >= 0.5 ? 'text-amber-400' : 'text-red-400'
              }
            />
          </div>
          <div className="p-2 bg-japandi-bg rounded-lg">
            <MetricRow
              label="Tích lũy IG"
              abbrKey="IG"
              value={cumIG}
              color="text-japandi-text"
            />
          </div>
        </div>

        {/* Action description */}
        {action !== 'NONE' && (
          <div className={`p-2 rounded-lg border ${actionConfig.bgColor}`}>
            <p className={`text-xs ${actionConfig.color} leading-relaxed`}>
              {t(actionConfig.description_vi)}
            </p>
          </div>
        )}

        {/* Reason */}
        {quantstats.reason && (
          <div className="pt-2 border-t border-japandi-border">
            <p className="text-xs text-japandi-text-dim italic leading-relaxed">
              {quantstats.reason}
            </p>
          </div>
        )}
      </div>
    </DecisionCard>
  );
}
