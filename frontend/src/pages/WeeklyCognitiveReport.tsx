import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import {
  BarChart3, TrendingUp, Shield, AlertTriangle,
  Loader2, RefreshCw,
} from 'lucide-react'
import VietnameseSemanticBlock from '../components/VietnameseSemanticBlock'

const regimeColors: Record<string, string> = {
  TRENDING: 'text-emerald-600 bg-emerald-50 border-emerald-200',
  RANGING: 'text-amber-600 bg-amber-50 border-amber-200',
  CRISIS: 'text-red-600 bg-red-50 border-red-200',
}

function Card({ title, icon: Icon, children, className = '' }: {
  title: string
  icon: React.ElementType
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`bg-white rounded-2xl border border-gray-100 shadow-sm p-6 ${className}`}>
      <div className="flex items-center gap-2 mb-4">
        <Icon size={20} className="text-gray-600" />
        <h3 className="font-semibold text-gray-800 tracking-tight">{title}</h3>
      </div>
      {children}
    </div>
  )
}

function Badge({ label, color }: { label: string; color?: string }) {
  return (
    <span className={`inline-block px-3 py-1 rounded-full text-xs font-medium border ${color || 'text-gray-600 bg-gray-50 border-gray-200'}`}>
      {label}
    </span>
  )
}

function StatRow({ label, value, valueClass = '' }: { label: string; value: string | number; valueClass?: string }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-gray-50 last:border-0">
      <span className="text-sm text-gray-500">{label}</span>
      <span className={`text-sm font-medium ${valueClass}`}>{value}</span>
    </div>
  )
}

export default function WeeklyCognitiveReport() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['weekly-report'],
    queryFn: () => api.get<any>('/v1/weekly/'),
    refetchInterval: 300_000,
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="animate-spin text-gray-400" size={32} />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <AlertTriangle size={32} className="text-red-400" />
        <p className="text-gray-500">Không thể tải báo cáo tuần</p>
        <button onClick={() => refetch()} className="text-sm text-indigo-600 hover:underline">
          Thử lại
        </button>
      </div>
    )
  }

  const { market = {}, gold = {}, trust = {}, summary_vi = '' } = data

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 tracking-tight">Báo cáo Tuần</h1>
          <p className="text-sm text-gray-500 mt-1">
            {data.timestamp ? new Date(data.timestamp).toLocaleDateString('vi-VN', {
              weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
            }) : ''}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-white border border-gray-200 rounded-xl hover:bg-gray-50 transition-colors"
        >
          <RefreshCw size={16} />
          Làm mới
        </button>
      </div>

      <VietnameseSemanticBlock
        label={data.label_vi}
        explanation={data.explanation_vi}
        severity={data.severity}
      />
      <div className="bg-gradient-to-r from-indigo-50 to-blue-50 border border-indigo-100 rounded-2xl p-6">
        <p className="text-lg font-medium text-gray-800 leading-relaxed">
          {summary_vi || 'Chưa có nhận định tổng hợp.'}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card title="Thị trường" icon={BarChart3}>
          <div className="space-y-3">
            <VietnameseSemanticBlock
              label={market.label_vi}
              explanation={market.explanation_vi}
              severity={market.severity}
              inline
            />
            <div className="flex items-center gap-2">
              <Badge
                label={market.regime || 'N/A'}
                color={regimeColors[market.regime as string] || ''}
              />
              {market.regime_score != null && (
                <span className="text-xs text-gray-400">
                  {typeof market.regime_score === 'number' ? market.regime_score.toFixed(2) : market.regime_score}
                </span>
              )}
            </div>
            <StatRow label="Thanh khoản" value={market.lci || 'N/A'} />
            <StatRow label="Khẩu vị rủi ro" value={market.risk_appetite || 'N/A'} />
            <StatRow label="Pha thị trường" value={market.market_phase || 'N/A'} />
            <StatRow label="Dòng tiền chính" value={market.dominant_flow || 'N/A'} />
            {market.top_sectors?.length > 0 && (
              <div className="pt-2">
                <span className="text-xs text-gray-400">Ngành nổi bật:</span>
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {market.top_sectors.map((s: string) => (
                    <span key={s} className="px-2 py-0.5 bg-gray-100 rounded text-xs text-gray-600">
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>

        <Card title="Vàng" icon={TrendingUp}>
          <div className="space-y-3">
            <VietnameseSemanticBlock
              label={gold.label_vi}
              explanation={gold.explanation_vi}
              severity={gold.severity}
              inline
            />
            <Badge
              label={gold.regime || 'N/A'}
              color={gold.regime === 'BULLISH' ? 'text-emerald-600 bg-emerald-50 border-emerald-200' :
                     gold.regime === 'BEARISH' ? 'text-red-600 bg-red-50 border-red-200' :
                     'text-amber-600 bg-amber-50 border-amber-200'}
            />
            <StatRow label="Driver" value={gold.driver || 'N/A'} />
            <StatRow label="Premium regime" value={gold.premium_regime || 'N/A'} />
            {gold.premium_pct != null && (
              <StatRow label="Premium %" value={`${(gold.premium_pct * 100).toFixed(1)}%`} />
            )}
            {gold.xau_usd != null && (
              <StatRow
                label="XAU/USD"
                value={`$${Number(gold.xau_usd).toLocaleString()}`}
              />
            )}
            {gold.xau_change_pct != null && (
              <StatRow
                label="XAU biến động"
                value={`${(gold.xau_change_pct >= 0 ? '+' : '')}${(gold.xau_change_pct * 100).toFixed(2)}%`}
                valueClass={gold.xau_change_pct >= 0 ? 'text-emerald-600' : 'text-red-600'}
              />
            )}
            <div className="flex items-center gap-2 pt-1">
              <Shield size={14} className={gold.stress_signal ? 'text-red-500' : 'text-emerald-500'} />
              <span className={`text-xs font-medium ${gold.stress_signal ? 'text-red-600' : 'text-emerald-600'}`}>
                {gold.stress_signal ? 'Căng thẳng vàng nội địa' : 'Bình thường'}
              </span>
            </div>
          </div>
        </Card>

        <Card title="CAO Trust" icon={Shield}>
          <div className="space-y-3">
            <VietnameseSemanticBlock
              label={trust.label_vi}
              explanation={trust.explanation_vi}
              severity={trust.severity}
              inline
            />
            <Badge
              label={trust.status || 'NO_DATA'}
              color={trust.status === 'PROMOTABLE' ? 'text-emerald-600 bg-emerald-50 border-emerald-200' :
                     trust.status === 'BLOCKED' ? 'text-amber-600 bg-amber-50 border-amber-200' :
                     'text-gray-600 bg-gray-50 border-gray-200'}
            />
            <StatRow label="Consistency score" value={trust.consistency_score?.toFixed(4) || '0.0'} />
            <StatRow label="Số decision" value={trust.decision_count || 0} />
            {trust.regime_states && Object.entries(trust.regime_states).map(([regime, state]: [string, any]) => (
              <div key={regime} className="bg-gray-50 rounded-lg p-2.5 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-gray-600">{regime}</span>
                  <span className={`text-xs font-medium ${state.can_promote ?? state.promotable ? 'text-emerald-600' : 'text-amber-600'}`}>
                    {state.can_promote ?? state.promotable ? 'SẴN SÀNG' : 'CHỜ'}
                  </span>
                </div>
                {state.confidence != null && (
                  <div className="flex justify-between text-xs text-gray-400">
                    <span>Confidence</span>
                    <span>{(state.confidence * 100).toFixed(0)}%</span>
                  </div>
                )}
                {state.consistency != null && (
                  <div className="flex justify-between text-xs text-gray-400">
                    <span>Consistency</span>
                    <span>{state.consistency.toFixed(3)}</span>
                  </div>
                )}
                {state.samples != null && (
                  <div className="flex justify-between text-xs text-gray-400">
                    <span>Samples</span>
                    <span>{state.samples}</span>
                  </div>
                )}
                {state.failures?.length > 0 && (
                  <div className="text-xs text-red-400 mt-1">
                    {state.failures.join(', ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  )
}
