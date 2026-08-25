import { useMemo } from 'react';
import viJson from '../i18n/vi.json';
import enJson from '../i18n/en.json';

type LangMode = 'full' | 'compact' | 'annotated';

interface I18nResult {
  /** Translate key to current language. Fallback to key if not found. */
  t: (key: string) => string;
  /** Current language mode */
  lang: LangMode;
}

const viMap = viJson as Record<string, string>;
const enMap = enJson as Record<string, string>;

/**
 * useI18n — Bilingual i18n hook for PTCK React Dashboard.
 *
 * Auto-synced from backend CLI_LABEL_MAP via i18n_export.py.
 * Usage:
 *   const { t } = useI18n('full');
 *   <span>{t('Live Sharpe')}</span>
 */
export function useI18n(mode: LangMode = 'full'): I18nResult {
  const t = useMemo(() => {
    const map: Record<string, string> =
      mode === 'compact' ? enMap : mode === 'annotated' ? { ...enMap, ...viMap } : viMap;

    return (key: string): string => {
      const val = map[key];
      if (val !== undefined) return val;
      return key;
    };
  }, [mode]);

  return { t, lang: mode };
}
