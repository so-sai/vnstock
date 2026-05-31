/** VietnameseSemanticBlock — UI Semantic Layer component.

Renders backend-owned meaning.  NEVER interprets, translates, or maps.
Backend sends label_vi + explanation_vi + severity; this component
only renders them with appropriate coloring.

Usage:
  <VietnameseSemanticBlock
    label={data.label_vi}
    explanation={data.explanation_vi}
    severity={data.severity}
  />
*/
import React, { useState } from 'react'

interface Props {
  label?: string
  explanation?: string
  severity?: number
  /** Optional inline mode (no card border, compact) */
  inline?: boolean
}

const severityColor = (s: number | undefined): string => {
  if (s === undefined || s === null) return 'bg-gray-100 border-gray-300 text-gray-700'
  if (s < 0.15) return 'bg-green-50 border-green-300 text-green-800'
  if (s < 0.35) return 'bg-blue-50 border-blue-300 text-blue-800'
  if (s < 0.55) return 'bg-yellow-50 border-yellow-300 text-yellow-800'
  if (s < 0.75) return 'bg-orange-50 border-orange-300 text-orange-800'
  return 'bg-red-50 border-red-400 text-red-900'
}

const severityDot = (s: number | undefined): string => {
  if (s === undefined || s === null) return 'bg-gray-400'
  if (s < 0.15) return 'bg-green-500'
  if (s < 0.35) return 'bg-blue-500'
  if (s < 0.55) return 'bg-yellow-500'
  if (s < 0.75) return 'bg-orange-500'
  return 'bg-red-600'
}

const VietnameseSemanticBlock: React.FC<Props> = ({
  label,
  explanation,
  severity,
  inline = false,
}) => {
  const [expanded, setExpanded] = useState(false)

  if (!label && !explanation) return null

  const colorClass = severityColor(severity)
  const dotClass = severityDot(severity)

  if (inline) {
    return (
      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium border ${colorClass}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${dotClass}`} />
        {label || 'đang tải...'}
      </span>
    )
  }

  return (
    <div className={`rounded-lg border p-3 ${colorClass}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`w-2 h-2 rounded-full shrink-0 mt-0.5 ${dotClass}`} />
          <span className="font-semibold text-sm leading-tight truncate">
            {label || 'đang tải...'}
          </span>
        </div>
        {explanation && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="shrink-0 text-xs underline opacity-60 hover:opacity-100 transition-opacity"
          >
            {expanded ? 'thu gọn' : 'chi tiết'}
          </button>
        )}
      </div>
      {expanded && explanation && (
        <p className="mt-2 text-sm leading-relaxed">{explanation}</p>
      )}
      {severity !== undefined && (
        <div className="mt-2 flex items-center gap-2">
          <div className="flex-1 h-1.5 rounded-full bg-black/10">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${Math.min(100, Math.max(0, severity * 100))}%`,
                backgroundColor: severity < 0.35 ? '#22c55e' : severity < 0.55 ? '#eab308' : severity < 0.75 ? '#f97316' : '#ef4444',
              }}
            />
          </div>
          <span className="text-[10px] opacity-60 font-mono">
            {(severity * 100).toFixed(0)}%
          </span>
        </div>
      )}
    </div>
  )
}

export default VietnameseSemanticBlock
