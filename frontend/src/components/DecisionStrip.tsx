import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { AlertTriangle, Shield, TrendingUp, TrendingDown, Pause, Ban, Minus } from 'lucide-react';

interface DecisionVector {
  action: string;
  confidence: number;
  risk_state: string;
  reason: string;
  constraint: string;
  suggested_size_mult: number;
}

const actionIcons: Record<string, React.ReactNode> = {
  ENTER: <TrendingUp size={18} />,
  HOLD: <Pause size={18} />,
  REDUCE: <TrendingDown size={18} />,
  EXIT: <Ban size={18} />,
  STAND_DOWN: <Minus size={18} />,
};

const actionColors: Record<string, string> = {
  ENTER: 'bg-emerald-800 text-emerald-50 border-emerald-700',
  HOLD: 'bg-amber-700 text-amber-50 border-amber-600',
  REDUCE: 'bg-orange-700 text-orange-50 border-orange-600',
  EXIT: 'bg-rose-800 text-rose-50 border-rose-700',
  STAND_DOWN: 'bg-zinc-600 text-zinc-50 border-zinc-500',
};

const riskColors: Record<string, string> = {
  SAFE: 'text-emerald-600',
  CAUTION: 'text-amber-600',
  STRESS: 'text-rose-600',
  LOCKED: 'text-red-700 font-black',
};

const constraintBadge: Record<string, { label: string; color: string }> = {
  ALLOWED: { label: 'Tự do giao dịch', color: 'bg-emerald-100 text-emerald-800 border-emerald-200' },
  PARTIAL: { label: `Giảm 50% size`, color: 'bg-amber-100 text-amber-800 border-amber-200' },
  BLOCKED: { label: 'Không mở mới', color: 'bg-rose-100 text-rose-800 border-rose-200' },
};

const DecisionStrip: React.FC = () => {
  const { data, isLoading } = useQuery<DecisionVector>({
    queryKey: ['decisionTensor'],
    queryFn: () => api.get(`/api/portfolio/observatory/decision`).then(r => r.json()),
    refetchInterval: 30000,
  });

  if (isLoading || !data) {
    return (
      <div className="h-10 bg-japandi-warm-sand/30 border border-japandi-muted-clay/30 rounded-lg flex items-center px-4">
        <span className="text-japandi-muted-clay text-xs font-mono">Đang tổng hợp quyết định...</span>
      </div>
    );
  }

  return (
    <div className="bg-white/60 border border-japandi-muted-clay/40 rounded-lg overflow-hidden">
      <div className="flex items-stretch min-h-[52px] divide-x divide-japandi-muted-clay/20">

        <div className={`flex items-center gap-3 px-5 py-3 ${actionColors[data.action] || 'bg-zinc-100'}`}>
          {actionIcons[data.action] || <Minus size={18} />}
          <div>
            <div className="text-[10px] opacity-70 font-mono">HÀNH ĐỘNG</div>
            <div className="font-bold text-sm tracking-wide">{data.action}</div>
          </div>
        </div>

        <div className="flex items-center gap-3 px-5 py-3">
          <div className="relative w-12 h-12 flex items-center justify-center">
            <svg className="w-12 h-12 -rotate-90" viewBox="0 0 36 36">
              <circle cx="18" cy="18" r="15.5" fill="none" stroke="#e5e7eb" strokeWidth="3" />
              <circle cx="18" cy="18" r="15.5" fill="none"
                stroke={data.confidence >= 65 ? '#059669' : data.confidence >= 40 ? '#d97706' : '#e11d48'}
                strokeWidth="3" strokeDasharray={`${data.confidence * 0.86} 86`} />
            </svg>
            <span className="absolute text-xs font-bold font-mono">{data.confidence}</span>
          </div>
          <div>
            <div className="text-[10px] text-japandi-earth/60 font-mono">TIN CẬY</div>
            <div className={`text-sm font-bold ${riskColors[data.risk_state] || 'text-japandi-earth'}`}>
              {data.risk_state}
            </div>
          </div>
        </div>

        <div className="flex-1 flex items-center px-5 py-3">
          <div>
            <div className="text-[10px] text-japandi-earth/60 font-mono mb-0.5">LÝ DO CHÍNH</div>
            <div className="text-xs text-japandi-earth/80 font-mono leading-relaxed">
              {data.reason || 'Không có dữ liệu'}
            </div>
          </div>
        </div>

        <div className="flex items-center px-5 py-3">
          <span className={`px-2.5 py-1 rounded text-[10px] font-bold font-mono border ${constraintBadge[data.constraint]?.color || 'bg-gray-100'}`}>
            {constraintBadge[data.constraint]?.label || data.constraint}
          </span>
        </div>

      </div>
    </div>
  );
};

export default DecisionStrip;
