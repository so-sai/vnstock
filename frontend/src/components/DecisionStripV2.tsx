import React, { useState, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { swuc } from '../lib/swuc';
import { Shield, TrendingUp, TrendingDown, Pause, Ban, Minus, ChevronDown, ChevronRight, Check, X, History } from 'lucide-react';
import type { DecisionVectorV2 } from '../types/interfaces';

const actionIcons: Record<string, React.ReactNode> = {
  ENTER: <TrendingUp size={18} />,
  SCALE_IN: <TrendingUp size={18} />,
  HOLD: <Pause size={18} />,
  REDUCE: <TrendingDown size={18} />,
  EXIT: <Ban size={18} />,
  STAND_DOWN: <Minus size={18} />,
};

const actionColors: Record<string, string> = {
  ENTER: 'bg-emerald-800 text-emerald-50 border-emerald-700',
  SCALE_IN: 'bg-purple-800 text-purple-50 border-purple-700',
  HOLD: 'bg-amber-700 text-amber-50 border-amber-600',
  REDUCE: 'bg-orange-700 text-orange-50 border-orange-600',
  EXIT: 'bg-rose-800 text-rose-50 border-rose-700',
  STAND_DOWN: 'bg-zinc-600 text-zinc-50 border-zinc-500',
};

const actionLabels: Record<string, string> = {
  ENTER:      "Vào lệnh",
  SCALE_IN:   "Tăng vị thế",
  HOLD:       "Giữ lệnh",
  REDUCE:     "Giảm vị thế",
  EXIT:       "Thoát lệnh",
  STAND_DOWN: "Đứng ngoài",
};

const riskLabels: Record<string, string> = {
  SAFE: 'An toàn', CAUTION: 'Thận trọng', STRESS: 'Căng thẳng', LOCKED: 'Khóa',
};
const riskColors: Record<string, string> = {
  SAFE: 'text-emerald-600',
  CAUTION: 'text-amber-600',
  STRESS: 'text-rose-600',
  LOCKED: 'text-red-700 font-black',
};

const constraintBadge: Record<string, { label: string; color: string }> = {
  ALLOWED: { label: 'Tự do giao dịch', color: 'bg-emerald-100 text-emerald-800 border-emerald-200' },
  PARTIAL: { label: 'Giảm 50% size', color: 'bg-amber-100 text-amber-800 border-amber-200' },
  BLOCKED: { label: 'Không mở mới', color: 'bg-rose-100 text-rose-800 border-rose-200' },
};

interface RationaleNodeData {
  label: string;
  detail: string;
  score_contribution: number | null;
  children: RationaleNodeData[];
}

const RationaleTree: React.FC<{ nodes: RationaleNodeData[]; depth?: number }> = ({ nodes, depth = 0 }) => {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  if (!nodes || nodes.length === 0) return null;
  return (
    <div className={`${depth > 0 ? 'ml-4 pl-3 border-l border-japandi-muted-clay/30' : ''}`}>
      {nodes.map((node, i) => {
        const isExpandable = node.children && node.children.length > 0;
        const key = `${depth}-${i}`;
        const isOpen = expanded[key] ?? (depth < 1);
        return (
          <div key={key} className="py-1">
            <div
              className={`flex items-start gap-2 text-xs font-mono cursor-pointer hover:bg-japandi-oat/40 rounded px-1 ${depth === 0 ? 'font-bold text-japandi-earth' : 'text-japandi-earth/70'}`}
              onClick={() => isExpandable && setExpanded(prev => ({ ...prev, [key]: !prev[key] }))}
            >
              {isExpandable && (isOpen ? <ChevronDown size={12} className="mt-0.5 shrink-0" /> : <ChevronRight size={12} className="mt-0.5 shrink-0" />)}
              {!isExpandable && <span className="w-3 shrink-0" />}
              <span className="flex-1">{node.label}</span>
              {node.score_contribution != null && (
                <span className="text-japandi-muted-clay shrink-0 ml-2">
                  {(node.score_contribution as number) > 0 ? '+' : ''}{((node.score_contribution as number) * 100).toFixed(1)}%
                </span>
              )}
            </div>
            {node.detail && isOpen && (
              <div className="text-[10px] text-japandi-muted-clay ml-5 pl-2 py-0.5">{node.detail}</div>
            )}
            {isExpandable && isOpen && node.children && (
              <RationaleTree nodes={node.children} depth={depth + 1} />
            )}
          </div>
        );
      })}
    </div>
  );
};

const DecisionStripV2: React.FC = () => {
  const queryClient = useQueryClient();
  const [showRationale, setShowRationale] = useState(false);
  const [showAlternatives, setShowAlternatives] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const alertStartRef = useRef<number | null>(null);
  const [alertSeverity, setAlertSeverity] = useState<'fresh' | 'stale' | 'muted' | null>(null);

  const { data, isLoading } = useQuery<DecisionVectorV2>({
    queryKey: ['decisionTensorV2'],
    queryFn: () => api.get('/portfolio/observatory/decision-v2'),
    refetchInterval: 30000,
  });

  const confirmMutation = useMutation({
    mutationFn: (decisionId: string) =>
      api.post('/portfolio/observatory/decision-confirm', { body: JSON.stringify({ decision_id: decisionId }) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['decisionTensorV2'] }),
  });

  const overrideMutation = useMutation({
    mutationFn: (params: { decision_id: string; override_action: string; override_reason: string }) =>
      api.post('/portfolio/observatory/decision-override', { body: JSON.stringify(params) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['decisionTensorV2'] }),
  });

  const { data: history } = useQuery<Record<string, any>[]>({
    queryKey: ['decisionHistory'],
    queryFn: () => api.get('/portfolio/observatory/decision-history?limit=10'),
    enabled: showHistory,
  });

  // Adaptive Alert Decay: giảm cấp cảnh báo DDI theo thời gian
  useEffect(() => {
    if (!data?.ddi_data) { alertStartRef.current = null; setAlertSeverity(null); return; }
    const isAlert = data.ddi_data.action_filter === 'block' || data.ddi_data.healing_illusion;
    if (isAlert) {
      if (alertStartRef.current === null) alertStartRef.current = Date.now();
      const elapsed = (Date.now() - alertStartRef.current) / 60000;
      if (elapsed > 120) setAlertSeverity('muted');
      else if (elapsed > 30) setAlertSeverity('stale');
      else setAlertSeverity('fresh');
    } else {
      alertStartRef.current = null;
      setAlertSeverity(null);
    }
  }, [data?.ddi_data]);

  if (isLoading || !data) {
    return (
      <div className="h-10 bg-japandi-warm-sand/30 border border-japandi-muted-clay/30 rounded-lg flex items-center px-4">
        <span className="text-japandi-muted-clay text-xs font-mono">Đang tổng hợp quyết định...</span>
      </div>
    );
  }

  return (
    <div className={`${swuc('DECISION', 'decision')} border border-japandi-muted-clay/40 rounded-lg overflow-hidden`}>
      <div className="flex items-stretch min-h-[52px] divide-x divide-japandi-muted-clay/20">
        <div className={`flex items-center gap-3 px-5 py-3 ${actionColors[data.action] || 'bg-zinc-100'}`}>
          {actionIcons[data.action] || <Minus size={18} />}
          <div>
            <div className="text-[10px] opacity-70 font-mono">HÀNH ĐỘNG</div>
            <div className="font-bold text-sm tracking-wide">{actionLabels[data.action] ?? data.action}</div>
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
              {riskLabels[data.risk_state] || data.risk_state}
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

        {data.ddi_data && (
          <div className={`flex items-center gap-1.5 px-3 py-3 border-l border-japandi-muted-clay/20`}>
            <div className={`px-2 py-1 rounded text-[10px] font-bold font-mono border transition-opacity duration-700 ${
              alertSeverity === 'muted' ? 'bg-zinc-100 text-zinc-400 border-zinc-200 opacity-40' :
              alertSeverity === 'stale' ? 'bg-amber-100 text-amber-800 border-amber-300' :
              data.ddi_data.action_filter === 'block' ? 'bg-rose-100 text-rose-800 border-rose-300' :
              data.ddi_data.action_filter === 'caution' ? 'bg-amber-100 text-amber-800 border-amber-300' :
              'bg-emerald-100 text-emerald-800 border-emerald-300'
            }`}>
              <span className="mr-1">
                {alertSeverity === 'muted' ? '⚪' :
                 alertSeverity === 'stale' ? '🟡' :
                 data.ddi_data.action_filter === 'block' ? '🔴' :
                 data.ddi_data.action_filter === 'caution' ? '🟡' : '🟢'}
              </span>
              Δ<sub>SA</sub> {data.ddi_data.delta_sa.toFixed(4)}
              {data.ddi_data.healing_illusion && alertSeverity !== 'muted' && (
                <span className={`ml-1 ${alertSeverity === 'fresh' ? 'animate-pulse' : ''}`}>⚠</span>
              )}
              {alertSeverity === 'stale' && <span className="ml-1 text-[8px] opacity-60">· 30ph</span>}
              {alertSeverity === 'muted' && <span className="ml-1 text-[8px] opacity-60">· 2h+</span>}
            </div>
          </div>
        )}

        <div className="flex items-center gap-1 px-3 py-3">
          <button
            onClick={() => confirmMutation.mutate(data.decision_id)}
            disabled={data.override_state !== 'PENDING'}
            className={`p-1.5 rounded text-xs font-bold font-mono transition-colors ${data.override_state !== 'PENDING' ? 'bg-japandi-muted-clay/20 text-japandi-muted-clay cursor-not-allowed' : 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200'}`}
            title="Xác nhận quyết định"
          >
            <Check size={14} />
          </button>
          <button
            onClick={() => {
              const alt = prompt('Hành động override (ENTER/HOLD/REDUCE/EXIT/STAND_DOWN):');
              if (alt) overrideMutation.mutate({ decision_id: data.decision_id, override_action: alt.toUpperCase(), override_reason: '' });
            }}
            disabled={data.override_state !== 'PENDING'}
            className={`p-1.5 rounded text-xs font-bold font-mono transition-colors ${data.override_state !== 'PENDING' ? 'bg-japandi-muted-clay/20 text-japandi-muted-clay cursor-not-allowed' : 'bg-rose-100 text-rose-700 hover:bg-rose-200'}`}
            title="Ghi đè quyết định"
          >
            <X size={14} />
          </button>
          <button
            onClick={() => setShowRationale(!showRationale)}
            className={`p-1.5 rounded text-xs font-bold font-mono transition-colors ${showRationale ? 'bg-japandi-earth/10 text-japandi-earth' : 'text-japandi-muted-clay hover:bg-japandi-warm-sand/60'}`}
            title="Chi tiết quyết định"
          >
            <Shield size={14} />
          </button>
          <button
            onClick={() => setShowAlternatives(!showAlternatives)}
            className={`p-1.5 rounded text-xs font-bold font-mono transition-colors ${showAlternatives ? 'bg-japandi-earth/10 text-japandi-earth' : 'text-japandi-muted-clay hover:bg-japandi-warm-sand/60'}`}
            title="Phương án thay thế"
          >
            <TrendingUp size={14} />
          </button>
          <button
            onClick={() => setShowHistory(!showHistory)}
            className={`p-1.5 rounded text-xs font-bold font-mono transition-colors ${showHistory ? 'bg-japandi-earth/10 text-japandi-earth' : 'text-japandi-muted-clay hover:bg-japandi-warm-sand/60'}`}
            title="Lịch sử quyết định"
          >
            <History size={14} />
          </button>
        </div>
      </div>

      {showRationale && (
        <div className="border-t border-japandi-muted-clay/20 px-5 py-3 bg-japandi-oat/30">
          <div className="text-[10px] text-japandi-earth/60 font-mono mb-2 tracking-wider">CÂY QUYẾT ĐỊNH</div>
          <RationaleTree nodes={data.rationale_tree} />
          {data.calibrated_weights && Object.keys(data.calibrated_weights).length > 0 && (
            <div className="mt-3 pt-3 border-t border-japandi-muted-clay/20">
              <div className="text-[10px] text-japandi-earth/60 font-mono mb-1 tracking-wider">TRỌNG SỐ HIỆU CHỈNH</div>
              <div className="flex gap-3 flex-wrap">
                {Object.entries(data.calibrated_weights).map(([k, v]) => (
                  <span key={k} className="text-[10px] font-mono text-japandi-earth/70">
                    {k.toUpperCase()}: <strong>{(v as number * 100).toFixed(0)}%</strong>
                  </span>
                ))}
              </div>
            </div>
          )}
          {data.params_hash && (
            <div className="mt-2 pt-2 border-t border-japandi-muted-clay/10">
              <span className="text-[9px] font-mono text-japandi-muted-clay/50">
                params_hash: {data.params_hash}
              </span>
            </div>
          )}
        </div>
      )}

      {showAlternatives && data.alternatives && data.alternatives.length > 0 && (
        <div className="border-t border-japandi-muted-clay/20 px-5 py-3 bg-japandi-oat/30">
          <div className="text-[10px] text-japandi-earth/60 font-mono mb-2 tracking-wider">PHƯƠNG ÁN THAY THẾ (COUNTERFACTUAL)</div>
          <div className="space-y-1.5">
            {data.alternatives.map((alt, i) => (
              <div key={i} className="flex items-center gap-3 text-xs font-mono">
                <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${actionColors[alt.action]?.split(' ')[0] ? `bg-opacity-20 ${actionColors[alt.action].split(' ')[0]}` : 'bg-gray-100'}`}>
                  {actionLabels[alt.action] || alt.action}
                </span>
                <span className="text-japandi-earth/60 w-8 text-right">{alt.score}</span>
                <span className="text-japandi-muted-clay text-[10px] flex-1">{alt.reason_blocked}</span>
                <span className="text-[9px] text-japandi-muted-clay/50 uppercase">{alt.blocked_by}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {showHistory && (
        <div className="border-t border-japandi-muted-clay/20 px-5 py-3 bg-japandi-oat/30 max-h-48 overflow-y-auto">
          <div className="text-[10px] text-japandi-earth/60 font-mono mb-2 tracking-wider">LỊCH SỬ QUYẾT ĐỊNH (10 GẦN NHẤT)</div>
          {history && history.length > 0 ? (
            <div className="space-y-1">
              {history.slice(-10).reverse().map((entry: Record<string, any>, i: number) => (
                <div key={i} className="flex items-center gap-3 text-[10px] font-mono">
                  <span className={`px-1.5 py-0.5 rounded font-bold ${entry.override_state === 'OVERRIDDEN' ? 'bg-rose-100 text-rose-700' : entry.override_state === 'CONFIRMED' ? 'bg-emerald-100 text-emerald-700' : 'bg-zinc-100 text-zinc-600'}`}>
                    {entry.override_state === 'OVERRIDDEN' ? 'Bị ghi đè' : entry.override_state === 'CONFIRMED' ? 'Xác nhận' : entry.override_state === 'PENDING' ? 'Chờ' : entry.override_state}
                  </span>
                  <span className="text-japandi-earth font-bold">{actionLabels[entry.action] || entry.action}</span>
                  <span className="text-japandi-muted-clay/60">{entry.decision_id}</span>
                  {entry.override_action && (
                    <span className="text-rose-600">→ {entry.override_action}</span>
                  )}
                  <span className="text-japandi-muted-clay/40 ml-auto">{typeof entry.timestamp === 'string' ? entry.timestamp.slice(0, 16) : ''}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[10px] text-japandi-muted-clay font-mono">Chưa có lịch sử</div>
          )}
        </div>
      )}
    </div>
  );
};

export default DecisionStripV2;
