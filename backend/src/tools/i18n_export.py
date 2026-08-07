"""i18n_export.py — Auto-export CLI_LABEL_MAP + ABBREVIATION_GLOSSARY → JSON.

Đọc từ canonical_output_adapter.py, xuất 3 file JSON cho React frontend:
  - vi.json       — key: Vietnamese label
  - en.json       — key: English label (key = key)
  - abbreviations.json — abbreviation glossary (static fallback)

Chạy:
    python -m src.tools.i18n_export

Output:
    frontend/src/i18n/vi.json              — key: Vietnamese label
    frontend/src/i18n/en.json              — key: English label (key = key)
    frontend/src/i18n/abbreviations.json   — abbreviation glossary
"""

import io
import json
import sys
from pathlib import Path


def _hydrate_path():
    current = Path(__file__).resolve().parent
    root = current
    while current != current.parent:
        if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
            root = current
            break
        current = current.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    backend_dir = root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return root


PROJECT_ROOT = _hydrate_path()
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_I18N_DIR = PROJECT_ROOT / "frontend" / "src" / "i18n"


def export_i18n() -> dict:
    """Trích xuất CLI_LABEL_MAP + ABBREVIATION_GLOSSARY → 3 file JSON cho React."""
    from src.core.canonical_output_adapter import ABBREVIATION_GLOSSARY, CLI_LABEL_MAP

    # vi.json: key → Vietnamese label
    vi_data = {}
    for key, vi in CLI_LABEL_MAP.items():
        vi_data[key] = vi

    # en.json: key → key (pass-through, cho compact mode)
    en_data = {k: k for k in CLI_LABEL_MAP}

    # abbreviations.json: abbreviation glossary (static fallback cho frontend)
    abbr_data = {}
    for abbr, info in ABBREVIATION_GLOSSARY.items():
        abbr_data[abbr] = {
            "vi": info.get("vi", abbr),
            "en": info.get("en", abbr),
            "detail_vi": info.get("detail_vi", ""),
            "detail_en": info.get("detail_en", ""),
            "category": info.get("category", "general"),
        }

    FRONTEND_I18N_DIR.mkdir(parents=True, exist_ok=True)

    with open(FRONTEND_I18N_DIR / "vi.json", "w", encoding="utf-8") as f:
        json.dump(vi_data, f, ensure_ascii=False, indent=2, sort_keys=True)

    with open(FRONTEND_I18N_DIR / "en.json", "w", encoding="utf-8") as f:
        json.dump(en_data, f, ensure_ascii=False, indent=2, sort_keys=True)

    with open(FRONTEND_I18N_DIR / "abbreviations.json", "w", encoding="utf-8") as f:
        json.dump(abbr_data, f, ensure_ascii=False, indent=2, sort_keys=True)

    return {
        "vi_count": len(vi_data),
        "en_count": len(en_data),
        "abbr_count": len(abbr_data),
        "output_dir": str(FRONTEND_I18N_DIR),
    }


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    result = export_i18n()
    print(f"Exported {result['vi_count']} labels + {result['abbr_count']} abbreviations -> {result['output_dir']}")
