import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Search, X, Loader2, ExternalLink } from 'lucide-react';

interface SearchResult {
  symbol: string;
  icb_name2: string | null;
  icb_name3: string | null;
  icb_name4: string | null;
}

interface SearchResponse {
  query: string;
  count: number;
  results: SearchResult[];
}

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}

const API_BASE = import.meta.env.DEV ? '/api' : 'http://localhost:17039/api';

const CommandPalette: React.FC<CommandPaletteProps> = ({ open, onOpenChange }) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        onOpenChange(!open);
      }
      if (e.key === 'Escape' && open) {
        onOpenChange(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (open && inputRef.current) {
      inputRef.current.focus();
    }
  }, [open]);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setErrorMsg(null);
      return;
    }
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(q.trim())}&limit=8`);
      if (!res.ok) {
        // Bắt lỗi rõ ràng — KHÔNG nuốt — để operator biết sự thật
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          /* body không phải JSON, dùng status code */
        }
        throw new Error(detail);
      }
      const data: SearchResponse = await res.json();
      setResults(data.results);
      setSelectedIndex(0);
    } catch (e) {
      // Phân biệt 2 trạng thái: lỗi kỹ thuật vs không có dữ liệu
      setResults([]);
      setErrorMsg(e instanceof Error ? e.message : 'Lỗi không xác định');
    } finally {
      setLoading(false);
    }
  }, []);

  const handleChange = (val: string) => {
    setQuery(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(val), 120);
  };

  const handleSelect = (symbol: string) => {
    onOpenChange(false);
    setQuery('');
    setResults([]);
    window.dispatchEvent(new CustomEvent('navigate-to-xray', { detail: symbol }));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && results[selectedIndex]) {
      handleSelect(results[selectedIndex].symbol);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]" onClick={() => onOpenChange(false)}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
      <div
        className="relative w-full max-w-xl bg-white/95 backdrop-blur-xl rounded-2xl shadow-2xl border border-japandi-muted-clay/30 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center px-5 py-4 border-b border-japandi-muted-clay/20">
          <Search size={18} className="text-japandi-muted-clay shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => handleChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Tìm mã CK, tên công ty, ngành... (VD: VCB, Ngân hàng)"
            className="flex-1 ml-3 bg-transparent border-none outline-none text-sm text-japandi-earth placeholder:text-japandi-muted-clay/50 font-mono"
          />
          {loading && <Loader2 size={16} className="animate-spin text-japandi-muted-clay shrink-0" />}
          <button onClick={() => onOpenChange(false)} className="ml-2 p-1 hover:bg-japandi-muted-clay/20 rounded-lg text-japandi-muted-clay">
            <X size={16} />
          </button>
        </div>

        {results.length > 0 && (
          <ul className="max-h-72 overflow-y-auto py-2">
            {results.map((r, i) => (
              <li
                key={r.symbol}
                onClick={() => handleSelect(r.symbol)}
                className={`flex items-center px-5 py-3 cursor-pointer transition-colors ${
                  i === selectedIndex ? 'bg-japandi-earth/10' : 'hover:bg-japandi-muted-clay/10'
                }`}
              >
                <span className="font-bold text-japandi-earth text-sm w-20 shrink-0 font-mono">{r.symbol}</span>
                <div className="flex-1 min-w-0 ml-3">
                  <p className="text-xs text-japandi-muted-clay truncate">
                    {[r.icb_name2, r.icb_name3, r.icb_name4].filter(Boolean).join(' / ') || '—'}
                  </p>
                </div>
                <ExternalLink size={14} className="text-japandi-muted-clay/50 shrink-0 ml-2" />
              </li>
            ))}
          </ul>
        )}

        {query && !loading && errorMsg && (
          <div className="px-5 py-6 text-center">
            <p className="text-sm text-rose-600 font-semibold">⚠ Lỗi tra cứu dữ liệu</p>
            <p className="text-xs text-rose-500/80 mt-1 font-mono break-words px-4">{errorMsg}</p>
            <p className="text-[10px] text-japandi-muted-clay/60 mt-3">Backend không phản hồi đúng. Kiểm tra log uv_backend.exe.</p>
          </div>
        )}

        {query && !loading && !errorMsg && results.length === 0 && (
          <div className="px-5 py-8 text-center text-sm text-japandi-muted-clay">
            Không tìm thấy kết quả cho "{query}"
          </div>
        )}

        <div className="px-5 py-2 border-t border-japandi-muted-clay/20 flex items-center justify-between text-[10px] text-japandi-muted-clay/60">
          <span><kbd className="px-1 py-0.5 bg-japandi-muted-clay/10 rounded text-[10px] font-mono">↑↓</kbd> Chọn &nbsp;<kbd className="px-1 py-0.5 bg-japandi-muted-clay/10 rounded text-[10px] font-mono">↵</kbd> Mở X-Ray</span>
          <span><kbd className="px-1 py-0.5 bg-japandi-muted-clay/10 rounded text-[10px] font-mono">Esc</kbd> Đóng</span>
        </div>
      </div>
    </div>
  );
};

export default CommandPalette;
