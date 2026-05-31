import React from 'react';

interface AlphaToggleProps {
  mode: 'data' | 'action';
  setMode: (mode: 'data' | 'action') => void;
}

const AlphaToggle: React.FC<AlphaToggleProps> = ({ mode, setMode }) => {
  return (
    <div className="inline-flex p-0.5 bg-japandi-warm-sand/40 rounded-lg border border-japandi-warm-sand/60 select-none">
      <button
        onClick={() => setMode('data')}
        className={`px-3 py-1 text-xs font-medium rounded-md transition-all duration-200 flex items-center gap-1.5 ${
          mode === 'data'
            ? 'bg-white text-japandi-earth shadow-sm font-semibold'
            : 'text-gray-400 hover:text-gray-600'
        }`}
      >
        <span>📊</span> Số liệu
      </button>
      <button
        onClick={() => setMode('action')}
        className={`px-3 py-1 text-xs font-medium rounded-md transition-all duration-200 flex items-center gap-1.5 ${
          mode === 'action'
            ? 'bg-white text-japandi-earth shadow-sm font-semibold'
            : 'text-gray-400 hover:text-gray-600'
        }`}
      >
        <span>⚡</span> Tác chiến
      </button>
    </div>
  );
};

export default AlphaToggle;
