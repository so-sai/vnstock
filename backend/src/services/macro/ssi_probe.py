import sys
from pathlib import Path


def _hydrate_path():
    if getattr(sys, "frozen", False):
        root_path = Path(sys.executable).resolve().parent
    else:
        current = Path(__file__).resolve().parent
        root_path = current
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                root_path = current
                break
            current = current.parent
    if str(root_path) not in sys.path:
        sys.path.insert(0, str(root_path))
    return root_path


_hydrate_path()

"""
SSI iBoard Macro API Probe — phát hiện endpoint dữ liệu vĩ mô.

Architecture:
- Chạy trong background thread (daemon=True) để không block daily-close.
- Dùng ThreadPoolExecutor để probe song song nhiều endpoint.
- Cache kết quả vào .kit/local_brain.db.
- Log phát hiện qua kit learn --tag friction.
"""

import io
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSI iBoard subdomains discovered from JS bundle analysis
# ---------------------------------------------------------------------------
SSI_DOMAINS = {
    "iboard-query": {
        "base": "https://iboard-query.ssi.com.vn",
        "desc": "Main data query API (custom RPC)",
        "paths": ["/", "/api", "/v1", "/v2", "/query", "/api/query", "/api/v1/query"],
    },
    "iboard-api": {
        "base": "https://iboard-api.ssi.com.vn",
        "desc": "Microservices gateway (statistics, alert, notification)",
        "paths": [
            "/statistics",
            "/statistics/macro",
            "/statistics/interbank",
            "/market/macro",
            "/macro",
            "/data/macro",
            "/data/interbank",
        ],
    },
}

# ---------------------------------------------------------------------------
# Candidate request configurations to discover valid endpoints
# ---------------------------------------------------------------------------
CANDIDATE_REQUESTS: list[dict] = []

for domain, info in SSI_DOMAINS.items():
    for path in info["paths"]:
        CANDIDATE_REQUESTS.append(
            {
                "domain": domain,
                "url": f"{info['base']}{path}",
                "method": "GET",
                "headers": {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"},
                "body": None,
            }
        )
        CANDIDATE_REQUESTS.append(
            {
                "domain": domain,
                "url": f"{info['base']}{path}",
                "method": "POST",
                "headers": {
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                },
                "body": json.dumps({"query": "{interbankRates{rate term date}}"}),
            }
        )

# GraphQL variants
for path in ["/graphql", "/api/graphql"]:
    CANDIDATE_REQUESTS.append(
        {
            "domain": "iboard-query",
            "url": f"https://iboard-query.ssi.com.vn{path}",
            "method": "POST",
            "headers": {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            "body": json.dumps({"query": "{__typename}"}),
        }
    )

# gRPC-web variants
for path in ["/", "/grpc"]:
    CANDIDATE_REQUESTS.append(
        {
            "domain": "iboard-query",
            "url": f"https://iboard-query.ssi.com.vn{path}",
            "method": "POST",
            "headers": {"Content-Type": "application/grpc-web-text", "User-Agent": "Mozilla/5.0"},
            "body": None,
        }
    )


def _try_request(req: dict) -> dict:
    """Execute a single probe request with timeout. Returns result dict."""
    result = {
        "url": req["url"],
        "method": req["method"],
        "domain": req["domain"],
        "status": 0,
        "size": 0,
        "elapsed": 0.0,
        "is_json": False,
        "error": None,
        "match": False,
    }
    try:
        t0 = time.time()
        kwargs = {"headers": req["headers"], "timeout": 8, "allow_redirects": False}
        if req["body"]:
            kwargs["data"] = req["body"]
        r = requests.request(req["method"], req["url"], **kwargs)
        result["elapsed"] = time.time() - t0
        result["status"] = r.status_code
        result["size"] = len(r.content)
        ct = r.headers.get("Content-Type", "")
        if "json" in ct:
            result["is_json"] = True
            body = r.text[:500]
            # Detect macro/interbank content
            lower = body.lower()
            if any(kw in lower for kw in ["interbank", "macro", "overnight", "lai suat", "laisuat"]):
                result["match"] = True
                data = r.json() if r.text.strip() else {}
                result["keys"] = list(data.keys())[:10] if isinstance(data, dict) else []
                result["body_preview"] = body[:200]
    except requests.exceptions.Timeout:
        result["error"] = "TIMEOUT"
    except requests.exceptions.ConnectionError as e:
        result["error"] = f"CONN_ERR: {str(e)[:60]}"
    except Exception as e:
        result["error"] = str(e)[:80]
    return result


def _cache_discovery(results: list[dict], cache_path: Path):
    """Cache probe results to JSON file for future reference."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {"discovered": [], "failed": []}
        if cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                existing = json.load(f)
        # Merge discovered endpoints
        discovered = [r for r in results if r["status"] < 500 and r["status"] > 0]
        existing["discovered"] = discovered
        existing["last_probe"] = datetime.now(UTC).isoformat()
        existing["total_candidates"] = len(results)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.warning(f"SSI probe cache failed: {e}")
        return False


def _log_to_kit(results: list[dict]):
    """Log probe findings via kit learn (if kit is available)."""
    import subprocess

    discovered = [r for r in results if r.get("match")]
    if not discovered:
        # No match — log that probe ran but found nothing
        return
    for r in discovered:
        msg = (
            f"SSI iBoard probe: discovered endpoint {r['url']} "
            f"(status={r['status']}, method={r['method']}, "
            f"keys={r.get('keys', [])})"
        )
        try:
            subprocess.run(
                ["kit", "learn", "--tag", "friction", "--namespace", "ssi_probe", msg],
                capture_output=True,
                timeout=5,
                cwd=Path(__file__).resolve().parent.parent.parent.parent,
            )
        except Exception:
            pass


def probe_ssi_macro_endpoint(
    max_workers: int = 10,
    cache_dir: Path | None = None,
) -> list[dict]:
    """Probe SSI iBoard API for macro data endpoints.

    Chạy đồng bộ, hoàn thành trong ~1-2s.

    Args:
        max_workers: Số lượng thread song song tối đa.
        cache_dir: Thư mục cache kết quả probe (mặc định backend/data/probe_cache/).

    Returns:
        Danh sách kết quả probe (mỗi item là dict).
    """
    if cache_dir is None:
        # Auto-detect: backend/data/probe_cache/
        current = Path(__file__).resolve().parent
        while current != current.parent:
            if (current / "AGENTS.md").exists() and (current / "backend").is_dir():
                cache_dir = current / "backend" / "data" / "probe_cache"
                break
            current = current.parent
        if cache_dir is None:
            cache_dir = Path(".") / "data" / "probe_cache"

    return _run_probe(CANDIDATE_REQUESTS, max_workers, cache_dir)


def _run_probe(candidates: list[dict], max_workers: int, cache_dir: Path) -> list[dict]:
    """Execute all probe requests and cache results."""
    logger.info(f"Bắt đầu probe SSI iBoard ({len(candidates)} candidates)...")
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_try_request, req): req for req in candidates}
        for f in as_completed(futures):
            results.append(f.result())

    results.sort(key=lambda x: x["status"], reverse=True)

    # Cache results
    cache_path = cache_dir / "ssi_probe_cache.json"
    _cache_discovery(results, cache_path)

    # Log matches
    matches = [r for r in results if r.get("match")]
    if matches:
        logger.info(f"SSI probe: {len(matches)} endpoint(s) matched macro content")
        for m in matches:
            logger.info(f"  [{m['status']}] {m['method']} {m['url']} keys={m.get('keys', [])}")
    else:
        logger.info(f"SSI probe complete: 0 macro endpoints found (tried {len(results)})")

    # Non-blocking kit log
    _log_to_kit(results)

    return results


if __name__ == "__main__":
    if sys.platform == "win32":
        if isinstance(sys.stdout, io.TextIOWrapper):
            if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
                try:
                    sys.stdout.reconfigure(encoding="utf-8")
                except Exception:
                    pass
        elif hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    results = probe_ssi_macro_endpoint(background=False)
    print(f"\nProbe complete: {len(results)} candidates, {sum(1 for r in results if r.get('match'))} matches")
