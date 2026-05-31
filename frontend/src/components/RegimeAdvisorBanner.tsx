import React from 'react';
import type { SystemMessage } from '../types/interfaces';

interface RegimeAdvisorBannerProps {
  message?: SystemMessage | null;
}

const colorMap: Record<string, { bg: string; border: string; icon: string }> = {
  rose:    { bg: 'bg-rose-50',     border: 'border-rose-200',   icon: '🏛️' },
  emerald: { bg: 'bg-emerald-50',  border: 'border-emerald-200', icon: '🚀' },
  amber:   { bg: 'bg-amber-50',    border: 'border-amber-200',  icon: '⚖️' },
  gray:    { bg: 'bg-gray-50',     border: 'border-gray-200',    icon: '⏱️' },
};

const RegimeAdvisorBanner: React.FC<RegimeAdvisorBannerProps> = ({ message }) => {
  if (!message) return null;

  const config = colorMap[message.color] || colorMap.gray;

  return (
    <div className={`p-4 rounded-lg ${config.bg} border ${config.border} flex items-start gap-3 transition-all my-4`}>
      <span className="text-xl mt-0.5">{config.icon}</span>
      <div>
        <h3 className={`text-sm font-bold uppercase tracking-wide`}>
          {message.title}
        </h3>
        <p className="text-xs text-gray-700 mt-1 leading-relaxed font-sans">
          <span className="font-bold">Khuyến nghị phân bổ:</span> {message.advise}
        </p>
      </div>
    </div>
  );
};

export default RegimeAdvisorBanner;
