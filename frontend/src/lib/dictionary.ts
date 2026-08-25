/**
 * dictionary.ts — Auto-sync dictionary hook for PTCK React Dashboard.
 *
 * Architecture:
 *   Backend (canonical_output_adapter.py) → /api/v1/i18n/dictionary → useDictionary()
 *   - Auto-fetch on mount
 *   - Cache in localStorage with TTL (5 min)
 *   - Background refresh when stale
 *   - Fallback to static vi.json if API unavailable
 *
 * Usage:
 *   const { t, abbr, getAbbreviation, isReady } = useDictionary();
 *   <span>{t('HDR')}</span>                    // "Tỷ lệ Giảm thiểu Rủi ro"
 *   <AbbrTooltip abbr="HDR" />                  // Full tooltip with explanation
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import viJson from '../i18n/vi.json';
import enJson from '../i18n/en.json';

// ── Types ──────────────────────────────────────────────────────────

export interface AbbreviationEntry {
  vi: string;
  en: string;
  detail_vi: string;
  detail_en: string;
  category: string;
}

export interface DictionaryData {
  version: number;
  generated_at: string;
  labels: { vi: Record<string, string>; en: Record<string, string> };
  abbreviations: Record<string, AbbreviationEntry>;
  categories: Record<string, string[]>;
  label_count: number;
  abbreviation_count: number;
}

interface UseDictionaryResult {
  /** Translate key to Vietnamese. Fallback to key if not found. */
  t: (key: string) => string;
  /** Get abbreviation entry. Returns null if not found. */
  getAbbreviation: (abbr: string) => AbbreviationEntry | null;
  /** Get detail_vi for an abbreviation. Returns empty string if not found. */
  abbr: (abbr: string) => string;
  /** Get all abbreviations in a category */
  getAbbrsByCategory: (category: string) => string[];
  /** Whether dictionary is loaded and ready */
  isReady: boolean;
  /** Current dictionary version */
  version: number;
  /** Available categories */
  categories: string[];
}

// ── Constants ──────────────────────────────────────────────────────

const STORAGE_KEY = 'ptck_dictionary';
const STORAGE_TS_KEY = 'ptck_dictionary_ts';
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes
const API_BASE = import.meta.env.DEV ? '/api' : 'http://localhost:17039/api';

// ── Static fallback ────────────────────────────────────────────────

const staticViMap = viJson as Record<string, string>;
const staticEnMap = enJson as Record<string, string>;

// ── localStorage helpers ───────────────────────────────────────────

function loadFromStorage(): DictionaryData | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const ts = localStorage.getItem(STORAGE_TS_KEY);
    if (!raw || !ts) return null;
    const age = Date.now() - parseInt(ts, 10);
    if (age > CACHE_TTL_MS * 2) return null; // expired after 2x TTL
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function saveToStorage(data: DictionaryData): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    localStorage.setItem(STORAGE_TS_KEY, String(Date.now()));
  } catch {
    // localStorage full or unavailable
  }
}

// ── Hook ───────────────────────────────────────────────────────────

export function useDictionary(lang: 'vi' | 'en' = 'vi'): UseDictionaryResult {
  const [dict, setDict] = useState<DictionaryData | null>(() => loadFromStorage());
  const [isReady, setIsReady] = useState(() => loadFromStorage() !== null);
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch from API
  const fetchDictionary = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/v1/i18n/dictionary?lang=${lang}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: DictionaryData = await res.json();
      setDict(data);
      saveToStorage(data);
      setIsReady(true);
    } catch {
      // API unavailable — use static fallback
      if (!dict) {
        setDict({
          version: 0,
          generated_at: new Date().toISOString(),
          labels: { vi: staticViMap, en: staticEnMap },
          abbreviations: {},
          categories: {},
          label_count: Object.keys(staticViMap).length,
          abbreviation_count: 0,
        });
        setIsReady(true);
      }
    }
  }, [lang]);

  // Initial fetch + background refresh
  useEffect(() => {
    fetchDictionary();

    // Background refresh every 5 minutes
    refreshTimerRef.current = setInterval(() => {
      fetchDictionary();
    }, CACHE_TTL_MS);

    return () => {
      if (refreshTimerRef.current) clearInterval(refreshTimerRef.current);
    };
  }, [fetchDictionary]);

  // Translation function
  const t = useCallback((key: string): string => {
    if (!dict) return staticViMap[key] ?? key;
    const map = lang === 'en' ? dict.labels.en : dict.labels.vi;
    return map[key] ?? staticViMap[key] ?? key;
  }, [dict, lang]);

  // Abbreviation lookup
  const getAbbreviation = useCallback((abbr: string): AbbreviationEntry | null => {
    if (!dict) return null;
    return dict.abbreviations[abbr] ?? null;
  }, [dict]);

  // Abbreviation detail_vi shorthand
  const abbr = useCallback((abbrKey: string): string => {
    const entry = getAbbreviation(abbrKey);
    if (!entry) return abbrKey;
    return lang === 'en' ? entry.detail_en : entry.detail_vi;
  }, [getAbbreviation, lang]);

  // Get abbreviations by category
  const getAbbrsByCategory = useCallback((category: string): string[] => {
    if (!dict) return [];
    return dict.categories[category] ?? [];
  }, [dict]);

  // Categories list
  const categories = useMemo(() => {
    if (!dict) return [];
    return Object.keys(dict.categories);
  }, [dict]);

  return {
    t,
    getAbbreviation,
    abbr,
    getAbbrsByCategory,
    isReady,
    version: dict?.version ?? 0,
    categories,
  };
}

// ── Utility: detect if text contains abbreviation ──────────────────

export function findAbbreviations(text: string, glossary: Record<string, AbbreviationEntry>): string[] {
  const found: string[] = [];
  const upperText = text.toUpperCase();
  for (const key of Object.keys(glossary)) {
    // Word boundary match
    const regex = new RegExp(`\\b${key}\\b`, 'i');
    if (regex.test(upperText) || regex.test(text)) {
      found.push(key);
    }
  }
  return found;
}
