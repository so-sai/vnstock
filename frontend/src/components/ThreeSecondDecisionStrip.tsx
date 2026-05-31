import React from 'react';

const RS_LABEL: Record<string, string> = {
  elite: 'SIÊU KHỎE',
  strong: 'MẠNH',
  weak: 'YẾU ỚT',
};

const LIQ_LABEL: Record<string, string> = {
  expanding: 'TIỀN LỚN VÀO',
  contracting: 'TIỀN KIỆT QUỆ',
};

const SECTOR_LABEL: Record<string, string> = {
  early: 'NGÀNH HÚT TIỀN',
  late: 'NGÀNH ĐANG SỤT',
};

function computeRsLevel(rs: number): 'elite' | 'strong' | 'weak' {
  if (rs >= 90) return 'elite';
  if (rs >= 70) return 'strong';
  return 'weak';
}

function computeLiqState(volRatio: number): 'expanding' | 'contracting' {
  return volRatio >= 1.5 ? 'expanding' : 'contracting';
}

function computeSectorState(rsRating: number, volRatio: number): 'early' | 'late' {
  return rsRating >= 80 && volRatio >= 1.2 ? 'early' : 'late';
}

function computeAction(
  rsRating: number,
  volRatio: number,
  sectorState: 'early' | 'late',
): 'BUY' | 'HOLD' | 'REDUCE' | 'STAND_DOWN' {
  if (rsRating >= 90 && volRatio >= 1.5 && sectorState === 'early') return 'BUY';
  if (rsRating >= 70 && volRatio >= 1.2) return 'HOLD';
  if (rsRating < 50 && volRatio < 0.7) return 'REDUCE';
  return 'STAND_DOWN';
}

interface DecisionStripProps {
  symbol: string;
  rsRating: number;
  volRatio: number;
  sector: string;
}

export const ThreeSecondDecisionStrip: React.FC<DecisionStripProps> = ({
  symbol,
  rsRating,
  volRatio,
  sector,
}) => {
  const rsLevel = computeRsLevel(rsRating);
  const liqState = computeLiqState(volRatio);
  const sectorState = computeSectorState(rsRating, volRatio);
  const action = computeAction(rsRating, volRatio, sectorState);

  const actionColors: Record<string, string> = {
    BUY: 'bg-purple-950/40 text-purple-400 border-purple-900/50',
    HOLD: 'bg-zinc-900 text-zinc-400 border-zinc-800',
    REDUCE: 'bg-amber-950/30 text-amber-500 border-amber-900/50',
    STAND_DOWN: 'bg-red-950/30 text-red-400 border-red-900/50',
  };

  const rsColors: Record<string, string> = {
    elite: 'text-emerald-400',
    strong: 'text-amber-400',
    weak: 'text-red-400',
  };

  const liqColors: Record<string, string> = {
    expanding: 'text-purple-400',
    contracting: 'text-zinc-500',
  };

  const actionIcons: Record<string, string> = {
    BUY: '🔥 MUA',
    HOLD: '🔒 GIỮ',
    REDUCE: '⚠️ HẠ',
    STAND_DOWN: '🚫 ĐỨNG NGOÀI',
  };

  return (
    <div className="flex items-center gap-3 p-3 bg-zinc-950/40 border border-zinc-800 rounded-sm font-mono text-[11px] text-zinc-300">
      <div className="w-12 font-black text-zinc-100">{symbol}</div>

      <div className="flex-1 bg-zinc-900/40 p-2 border border-zinc-900/60 rounded-sm">
        <span className="text-zinc-500 block text-[9px] mb-0.5">SỨC MẠNH GIÁ (RS)</span>
        <span className={rsColors[rsLevel]}>{rsRating} - {RS_LABEL[rsLevel]}</span>
      </div>

      <div className="flex-1 bg-zinc-900/40 p-2 border border-zinc-900/60 rounded-sm">
        <span className="text-zinc-500 block text-[9px] mb-0.5">DÒNG TIỀN (LIQ)</span>
        <span className={liqColors[liqState]}>{LIQ_LABEL[liqState]}</span>
      </div>

      <div className="flex-1 bg-zinc-900/40 p-2 border border-zinc-900/60 rounded-sm">
        <span className="text-zinc-500 block text-[9px] mb-0.5">CHU KỲ NGÀNH</span>
        <span className={sectorState === 'early' ? 'text-amber-400' : 'text-zinc-500'}>
          {SECTOR_LABEL[sectorState]}
        </span>
      </div>

      <div className={`px-4 py-2 border rounded-sm font-black tracking-widest text-center min-w-[90px] ${actionColors[action]}`}>
        {actionIcons[action]}
      </div>
    </div>
  );
};
