/**
 * ui-components.tsx — HCI-friendly UI components for PTCK Dashboard.
 *
 * Components:
 *   AbbrTooltip        — Hover tooltip giải nghĩa abbreviation
 *   ConfidenceBadge    — Hiển thị mức tin cậy Bayes với màu + icon
 *   DecisionCard       — Card wrapping nội dung decision-support
 *   MetricRow          — Hàng metric với label + value + optional tooltip
 *   StatusIndicator    — Dot indicator với color + pulse animation
 *
 * All components use useDictionary() for auto-sync labels.
 */
import { useState, useRef, useEffect, type ReactNode } from 'react';
import { useDictionary, type AbbreviationEntry } from '../lib/dictionary';

// ── AbbrTooltip ────────────────────────────────────────────────────

interface AbbrTooltipProps {
  /** Abbronym key (e.g., "HDR", "DOC", "IG") */
  abbr: string;
  /** Optional custom children to render instead of abbr text */
  children?: ReactNode;
  /** Tooltip position */
  position?: 'top' | 'bottom' | 'left' | 'right';
  /** Show full detail (detail_vi) or just short name */
  showDetail?: boolean;
}

export function AbbrTooltip({
  abbr,
  children,
  position = 'top',
  showDetail = true,
}: AbbrTooltipProps) {
  const { getAbbreviation } = useDictionary();
  const [isVisible, setIsVisible] = useState(false);
  const [actualPos, setActualPos] = useState<'top' | 'bottom' | 'left' | 'right'>(position);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);

  const entry = getAbbreviation(abbr);

  // Auto-reposition if tooltip would overflow viewport
  useEffect(() => {
    if (!isVisible || !triggerRef.current || !tooltipRef.current) return;
    const trigger = triggerRef.current.getBoundingClientRect();
    const tooltip = tooltipRef.current.getBoundingClientRect();

    let newPos = position;
    if (position === 'top' && trigger.top - tooltip.height < 8) newPos = 'bottom';
    if (position === 'bottom' && trigger.bottom + tooltip.height > window.innerHeight - 8) newPos = 'top';
    if (position === 'left' && trigger.left - tooltip.width < 8) newPos = 'right';
    if (position === 'right' && trigger.right + tooltip.width > window.innerWidth - 8) newPos = 'left';

    setActualPos(newPos);
  }, [isVisible, position]);

  if (!entry) {
    return <span className="font-mono text-japandi-text">{children ?? abbr}</span>;
  }

  const posClasses: Record<string, string> = {
    top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
    bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
    left: 'right-full top-1/2 -translate-y-1/2 mr-2',
    right: 'left-full top-1/2 -translate-y-1/2 ml-2',
  };

  const categoryColors: Record<string, string> = {
    governance: 'border-emerald-500/40 bg-emerald-900/20',
    engine: 'border-blue-500/40 bg-blue-900/20',
    execution: 'border-amber-500/40 bg-amber-900/20',
    quant: 'border-purple-500/40 bg-purple-900/20',
    pipeline: 'border-cyan-500/40 bg-cyan-900/20',
    market: 'border-rose-500/40 bg-rose-900/20',
    macro: 'border-orange-500/40 bg-orange-900/20',
    portfolio: 'border-indigo-500/40 bg-indigo-900/20',
    data: 'border-teal-500/40 bg-teal-900/20',
    general: 'border-gray-500/40 bg-gray-900/20',
  };

  return (
    <span
      ref={triggerRef}
      className="relative inline-flex items-center cursor-help"
      onMouseEnter={() => setIsVisible(true)}
      onMouseLeave={() => setIsVisible(false)}
      onFocus={() => setIsVisible(true)}
      onBlur={() => setIsVisible(false)}
      tabIndex={0}
      role="button"
      aria-describedby={`tooltip-${abbr}`}
    >
      {children ?? (
        <span className="font-mono text-japandi-accent font-semibold border-b border-dashed border-japandi-accent/50">
          {abbr}
        </span>
      )}

      {isVisible && (
        <div
          ref={tooltipRef}
          id={`tooltip-${abbr}`}
          role="tooltip"
          className={`absolute z-50 w-72 p-3 rounded-lg border shadow-xl backdrop-blur-sm
            ${posClasses[actualPos]}
            ${categoryColors[entry.category] ?? categoryColors.general}
            border-japandi-border bg-japandi-surface/95`}
        >
          {/* Header */}
          <div className="flex items-center gap-2 mb-1.5">
            <span className="font-mono font-bold text-sm text-japandi-text">{abbr}</span>
            <span className="text-xs text-japandi-text-dim">—</span>
            <span className="text-xs text-japandi-text">{entry.vi}</span>
          </div>

          {/* English name */}
          <div className="text-xs text-japandi-text-dim italic mb-2">{entry.en}</div>

          {/* Detail */}
          {showDetail && entry.detail_vi && (
            <p className="text-xs text-japandi-text leading-relaxed">
              {entry.detail_vi}
            </p>
          )}

          {/* Category badge */}
          <div className="mt-2 flex items-center gap-2">
            <span className={`px-1.5 py-0.5 text-[10px] rounded font-medium ${
              entry.category === 'governance' ? 'bg-emerald-900/40 text-emerald-400' :
              entry.category === 'engine' ? 'bg-blue-900/40 text-blue-400' :
              entry.category === 'quant' ? 'bg-purple-900/40 text-purple-400' :
              entry.category === 'pipeline' ? 'bg-cyan-900/40 text-cyan-400' :
              'bg-gray-900/40 text-gray-400'
            }`}>
              {entry.category}
            </span>
          </div>

          {/* Arrow */}
          <div className={`absolute w-2 h-2 rotate-45 bg-japandi-surface border-japandi-border
            ${actualPos === 'top' ? 'bottom-[-5px] left-1/2 -translate-x-1/2 border-r border-b' :
              actualPos === 'bottom' ? 'top-[-5px] left-1/2 -translate-x-1/2 border-l border-t' :
              actualPos === 'left' ? 'right-[-5px] top-1/2 -translate-y-1/2 border-t border-r' :
              'left-[-5px] top-1/2 -translate-y-1/2 border-b border-l'
            }`}
          />
        </div>
      )}
    </span>
  );
}

// ── ConfidenceBadge ────────────────────────────────────────────────

interface ConfidenceBadgeProps {
  /** Confidence value 0.0 - 1.0 */
  confidence: number;
  /** Display size */
  size?: 'sm' | 'md' | 'lg';
  /** Show numeric value */
  showValue?: boolean;
  /** Show label text */
  showLabel?: boolean;
  /** Optional custom label */
  label?: string;
}

function getConfidenceLevel(c: number): {
  label_vi: string;
  label_en: string;
  color: string;
  bgColor: string;
  emoji: string;
} {
  if (c >= 0.80) return {
    label_vi: 'Rất tin cậy',
    label_en: 'Very reliable',
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-900/30 border-emerald-500/40',
    emoji: '●',
  };
  if (c >= 0.60) return {
    label_vi: 'Tin cậy',
    label_en: 'Reliable',
    color: 'text-green-400',
    bgColor: 'bg-green-900/30 border-green-500/40',
    emoji: '●',
  };
  if (c >= 0.40) return {
    label_vi: 'Trung bình',
    label_en: 'Moderate',
    color: 'text-amber-400',
    bgColor: 'bg-amber-900/30 border-amber-500/40',
    emoji: '◐',
  };
  if (c >= 0.20) return {
    label_vi: 'Thấp',
    label_en: 'Low',
    color: 'text-orange-400',
    bgColor: 'bg-orange-900/30 border-orange-500/40',
    emoji: '◑',
  };
  return {
    label_vi: 'Rất thấp — Tạm ngưng',
    label_en: 'Very low — Suspend',
    color: 'text-red-400',
    bgColor: 'bg-red-900/30 border-red-500/40',
    emoji: '○',
  };
}

export function ConfidenceBadge({
  confidence,
  size = 'md',
  showValue = true,
  showLabel = true,
  label,
}: ConfidenceBadgeProps) {
  const { t } = useDictionary();
  const level = getConfidenceLevel(confidence);

  const sizeClasses = {
    sm: 'px-1.5 py-0.5 text-xs gap-1',
    md: 'px-2 py-1 text-sm gap-1.5',
    lg: 'px-3 py-1.5 text-base gap-2',
  };

  return (
    <div
      className={`inline-flex items-center rounded-md border font-medium
        ${sizeClasses[size]} ${level.bgColor}`}
      role="status"
      aria-label={`${t('Độ tin cậy')}: ${(confidence * 100).toFixed(0)}% — ${level.label_vi}`}
    >
      <span className={level.color}>{level.emoji}</span>
      {showValue && (
        <span className={`font-mono font-bold ${level.color}`}>
          {(confidence * 100).toFixed(0)}%
        </span>
      )}
      {showLabel && (
        <span className={`${level.color} opacity-80`}>
          {label ?? level.label_vi}
        </span>
      )}
    </div>
  );
}

// ── DecisionCard ───────────────────────────────────────────────────

interface DecisionCardProps {
  /** Card title */
  title: string;
  /** Confidence level for border color */
  confidence?: number;
  /** Children content */
  children: ReactNode;
  /** Optional action button */
  action?: ReactNode;
  /** Additional CSS classes */
  className?: string;
}

export function DecisionCard({
  title,
  confidence,
  children,
  action,
  className = '',
}: DecisionCardProps) {
  const { t } = useDictionary();

  const borderClass = confidence === undefined ? 'border-japandi-border' :
    confidence >= 0.60 ? 'border-emerald-500/30' :
    confidence >= 0.40 ? 'border-amber-500/30' :
    'border-red-500/30';

  return (
    <div className={`p-4 bg-japandi-surface rounded-xl border ${borderClass} ${className}`}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-japandi-text">{t(title)}</h3>
        {action}
      </div>
      {children}
    </div>
  );
}

// ── MetricRow ──────────────────────────────────────────────────────

interface MetricRowProps {
  /** Label text (will be translated) */
  label: string;
  /** Value to display */
  value: string | number;
  /** Optional abbreviation key for tooltip */
  abbrKey?: string;
  /** Value color class */
  color?: string;
  /** Whether value is monospace */
  mono?: boolean;
}

export function MetricRow({
  label,
  value,
  abbrKey,
  color = 'text-japandi-text',
  mono = true,
}: MetricRowProps) {
  const { t } = useDictionary();

  return (
    <div className="flex items-center justify-between py-1">
      <span className="text-xs text-japandi-text-dim flex items-center gap-1">
        {t(label)}
        {abbrKey && <AbbrTooltip abbr={abbrKey} showDetail={false}>?</AbbrTooltip>}
      </span>
      <span className={`text-sm ${mono ? 'font-mono' : ''} font-bold ${color}`}>
        {typeof value === 'number' ? (
          value > 0 ? `+${value.toFixed(4)}` : value.toFixed(4)
        ) : value}
      </span>
    </div>
  );
}

// ── StatusIndicator ────────────────────────────────────────────────

interface StatusIndicatorProps {
  /** Status level */
  status: 'healthy' | 'warning' | 'critical' | 'unknown';
  /** Optional label */
  label?: string;
  /** Show pulse animation */
  pulse?: boolean;
  /** Size */
  size?: 'sm' | 'md';
}

const statusConfig = {
  healthy: { color: 'bg-emerald-500', text: 'text-emerald-400' },
  warning: { color: 'bg-amber-500', text: 'text-amber-400' },
  critical: { color: 'bg-red-500', text: 'text-red-400' },
  unknown: { color: 'bg-gray-500', text: 'text-gray-400' },
};

export function StatusIndicator({
  status,
  label,
  pulse = false,
  size = 'sm',
}: StatusIndicatorProps) {
  const config = statusConfig[status];
  const sizeClass = size === 'sm' ? 'w-2 h-2' : 'w-3 h-3';

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`${sizeClass} rounded-full ${config.color} ${pulse ? 'animate-pulse' : ''}`} />
      {label && (
        <span className={`text-xs ${config.text}`}>{label}</span>
      )}
    </span>
  );
}
