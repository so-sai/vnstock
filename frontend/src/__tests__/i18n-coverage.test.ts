import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'fs';
import { join } from 'path';
import viJson from '../i18n/vi.json';

const viKeys = new Set(Object.keys(viJson));
const srcDir = join(__dirname, '..');

const NON_ENGLISH_RE = /[ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝàáâãèéêìíòóôõùúýĂăĐđĨĩŨũƠơƯưẠ-ỹ]/;
const TRIVIAL_RE = /^[\s\-_,.]+$/;

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === 'node_modules' || entry.startsWith('.') || entry === '__tests__') continue;
      yield* walk(full);
    } else if (entry.endsWith('.tsx')) yield full;
  }
}

describe('i18n Coverage & Bare Text Audit', () => {
  const T_KEY_RE = /t\(['"](.+?)['"]\)/g;
  const missingKeys: { file: string; key: string }[] = [];

  beforeAll(() => {
    for (const file of walk(srcDir)) {
      const content = readFileSync(file, 'utf-8');
      const rel = file.replace(srcDir + '\\', '');

      let m;
      while ((m = T_KEY_RE.exec(content)) !== null) {
        const key = m[1];

        // skip API paths, URLs, import paths
        if (key.startsWith('/') || key.startsWith('http') || key.startsWith('..')) continue;
        // skip whitespace / punctuation only
        if (TRIVIAL_RE.test(key)) continue;
        // skip Vietnamese-as-key (intentional — fallback t() returns key itself)
        if (NON_ENGLISH_RE.test(key)) continue;
        // skip relative module paths
        if (key.startsWith('.') || key.includes('/')) continue;

        if (!viKeys.has(key)) {
          missingKeys.push({ file: rel, key });
        }
      }
    }
  });

  it('100% English t() keys phải tồn tại trong vi.json', () => {
    expect(missingKeys).toEqual([]);
  });
});
