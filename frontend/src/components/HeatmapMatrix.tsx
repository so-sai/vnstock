import React from 'react';

interface HeatmapMatrixProps {
  history: number[];
  maxCells?: number;
  size?: 'sm' | 'md';
}

const getHeatColor = (score: number): string => {
  if (score >= 90) return 'bg-stock-ceil';
  if (score >= 75) return 'bg-stock-up';
  if (score >= 60) return 'bg-emerald-400';
  if (score >= 40) return 'bg-emerald-300';
  if (score >= 25) return 'bg-amber-300';
  return 'bg-gray-200';
};

const HeatmapMatrix: React.FC<HeatmapMatrixProps> = ({ history, maxCells = 10, size = 'sm' }) => {
  const cells = history.slice(-maxCells);

  if (cells.length === 0) return <span className="text-gray-400 text-xs">N/A</span>;

  const cellClass = size === 'sm'
    ? 'w-5 h-5 rounded-sm'
    : 'w-8 h-8 rounded';

  return (
    <div className="flex gap-0.5 items-center">
      {cells.map((val, i) => (
        <div
          key={i}
          className={`${cellClass} ${getHeatColor(val)}`}
          title={`T-${cells.length - 1 - i}: ${val}`}
        />
      ))}
    </div>
  );
};

export default HeatmapMatrix;
