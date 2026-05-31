import React from 'react';

export const TableSkeleton: React.FC<{ rows?: number }> = ({ rows = 8 }) => (
  <div className="animate-pulse space-y-3">
    <div className="h-8 bg-japandi-warm-sand/50 rounded w-1/3" />
    {Array.from({ length: rows }).map((_, i) => (
      <div key={i} className="grid grid-cols-10 gap-2">
        {Array.from({ length: 10 }).map((_, j) => (
          <div key={j} className={`h-8 bg-japandi-warm-sand/30 rounded ${j === 0 ? 'col-span-1' : j < 6 ? 'col-span-1' : 'col-span-4'}`} />
        ))}
      </div>
    ))}
  </div>
);

export const CardSkeleton: React.FC<{ count?: number }> = ({ count = 4 }) => (
  <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))' }}>
    {Array.from({ length: count }).map((_, i) => (
      <div key={i} className="animate-pulse h-24 bg-japandi-warm-sand/50 rounded-xl" />
    ))}
  </div>
);

export const ChartSkeleton: React.FC = () => (
  <div className="animate-pulse h-64 bg-japandi-warm-sand/50 rounded-xl" />
);
