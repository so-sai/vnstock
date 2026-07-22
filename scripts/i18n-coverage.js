/**
 * i18n-coverage.js — Scans .tsx files for t('...') calls and checks
 * every key exists in vi.json.
 *
 * Modes:
 *   node scripts/i18n-coverage.js              — full tree scan
 *   node scripts/i18n-coverage.js --staged     — Git-staged files only
 *
 * Staged mode is designed for pre-commit hooks (<1s).
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const root = path.resolve(__dirname, '..');
const isStaged = process.argv.includes('--staged');

// ── Load vi.json ──────────────────────────────────────────────────
const viJsonPath = path.join(root, 'frontend', 'src', 'i18n', 'vi.json');
let viKeys;
try {
  viKeys = new Set(Object.keys(JSON.parse(fs.readFileSync(viJsonPath, 'utf-8'))));
} catch (e) {
  console.error('FAIL: Cannot read vi.json \u2014', e.message);
  process.exit(1);
}

const T_KEY_RE = /t\(['"](.+?)['"]\)/g;
const missingKeys = [];
const usedKeys = new Set();
let totalFiles = 0;

// ── Scan single file ──────────────────────────────────────────────
function scanFile(filePath) {
  const resolved = path.resolve(root, filePath);
  if (!fs.existsSync(resolved)) return;
  const content = fs.readFileSync(resolved, 'utf-8');
  const rel = path.relative(root, resolved);

  let match;
  while ((match = T_KEY_RE.exec(content)) !== null) {
    usedKeys.add(match[1]);
    if (!viKeys.has(match[1])) {
      missingKeys.push({ file: rel, key: match[1] });
    }
  }
  totalFiles++;
}

// ── Full tree walk ────────────────────────────────────────────────
function walkDir(dir) {
  for (const entry of fs.readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (fs.statSync(full).isDirectory()) {
      if (entry === 'node_modules' || entry.startsWith('.')) continue;
      walkDir(full);
    } else if (entry.endsWith('.tsx') || entry.endsWith('.ts')) {
      scanFile(full);
    }
  }
}

// ── Main ──────────────────────────────────────────────────────────
if (isStaged) {
  // Only scan files staged for commit (Git index)
  const staged = execSync(
    'git diff --cached --name-only --diff-filter=ACM',
    { cwd: root, encoding: 'utf-8' }
  ).split('\n').map(s => s.trim()).filter(Boolean);

  const targetFiles = staged.filter(f =>
    f.startsWith('frontend/src') &&
    (f.endsWith('.tsx') || f.endsWith('.ts'))
  );

  for (const f of targetFiles) scanFile(f);
} else {
  // Full tree scan (CI / manual)
  walkDir(path.join(root, 'frontend', 'src'));
}

// ── Report ────────────────────────────────────────────────────────
let exitCode = 0;

if (missingKeys.length > 0) {
  console.error('MISSING KEYS (used in t() but not in vi.json):');
  for (const { file, key } of missingKeys) {
    console.error('  ' + file + '  \u2192  "' + key + '"');
  }
  console.error('\nTotal: ' + missingKeys.length + ' missing key(s)');
  exitCode = 1;
} else {
  console.log('OK: All ' + usedKeys.size + ' used key(s) exist in vi.json');
}

// Only report unused keys in full mode (staged mode has incomplete view)
if (!isStaged) {
  const unused = [...viKeys].filter(k => !usedKeys.has(k));
  if (unused.length > 0) {
    console.warn('\nUNUSED KEYS (in vi.json but never called via t()):');
    for (const k of unused) {
      console.warn('  "' + k + '"');
    }
    console.warn('Total: ' + unused.length + ' unused key(s)');
  } else {
    console.log('OK: No unused keys in vi.json');
  }
}

console.log('\nScanned ' + totalFiles + ' file(s)'
  + (isStaged ? ' (staged mode)' : ' (full mode)'));
process.exit(exitCode);
