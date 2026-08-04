"""
T0 preset prober — "is this base_url a real, live OpenAI-compatible endpoint?"

Answers the one question about a preset that needs no API key: does
`GET {base_url}/models` reach something that behaves like an OpenAI-compatible
server. It cannot tell you a provider works — only a real key does that (see
Supportedprovider.md §4 for the T0/T1/T2 tiers) — but it reliably catches the
defect that bulk-imported presets actually have: a base_url that is dead,
misspelled, or was never an OpenAI-compatible path.

Verdicts:
  LIVE      2xx                      — responds, and anonymously (rare, usually a free/open host)
  AUTH      401 / 403                — endpoint exists and demands a key. This is the PASS we expect.
  NOTFOUND  404 / 405                — reachable host, but no /models there. base_url is probably wrong.
  DNS       name does not resolve    — dead domain.
  CONNFAIL  TCP/TLS failure          — host down or blocking.
  TIMEOUT   no answer in time
  OTHER     any other status

Usage:
    python backend/tools/probe_presets.py                 # all presets
    python backend/tools/probe_presets.py --only groq,xai
    python backend/tools/probe_presets.py --json out.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

# Import presets without importing the whole app (which needs a database).
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import presets as presets_mod  # noqa: E402

TIMEOUT = 12
UA = "multi-llm-gateway-preset-prober/1.0"


def classify(base_url: Optional[str], models_url: Optional[str] = None) -> Dict[str, object]:
    """
    `models_url` wins when a preset declares one. A few providers put the model
    catalog on a sibling path rather than `{base_url}/models` (GitHub Models is
    the standing example), and probing the derived URL for those reports a
    failure the real discovery path would never hit.
    """
    if not (base_url or models_url):
        return {"verdict": "NOURL", "detail": "preset carries no base_url"}

    url = models_url or (base_url.rstrip("/") + "/models")
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return {"verdict": "LIVE", "status": r.status, "detail": ""}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"verdict": "AUTH", "status": e.code, "detail": ""}
        if e.code in (404, 405):
            return {"verdict": "NOTFOUND", "status": e.code, "detail": ""}
        return {"verdict": "OTHER", "status": e.code, "detail": e.reason or ""}
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, socket.gaierror):
            return {"verdict": "DNS", "detail": str(reason)}
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return {"verdict": "TIMEOUT", "detail": str(reason)}
        return {"verdict": "CONNFAIL", "detail": str(reason)}
    except TimeoutError:
        return {"verdict": "TIMEOUT", "detail": ""}
    except Exception as e:  # noqa: BLE001 - a prober must never crash the run
        return {"verdict": "CONNFAIL", "detail": f"{type(e).__name__}: {e}"}


def all_presets() -> List[Dict[str, Optional[str]]]:
    rows = []
    for p in presets_mod.PRESETS:
        rows.append({**p, "_list": "PRESETS"})
    for p in presets_mod._KNOWN_NON_PRESET:  # noqa: SLF001 - same module, deliberate
        rows.append({**p, "_list": "KNOWN_NON_PRESET"})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated slugs")
    ap.add_argument("--json", dest="json_out", help="write full results here")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    rows = all_presets()
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        rows = [r for r in rows if r["slug"] in want]

    results: List[Dict[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(classify, r.get("base_url"), r.get("models_url")): r for r in rows
        }
        for fut in concurrent.futures.as_completed(futures):
            r = futures[fut]
            out = fut.result()
            host = urlsplit(r.get("base_url") or "").netloc
            results.append(
                {
                    "slug": r["slug"],
                    "name": r.get("name"),
                    "list": r["_list"],
                    "base_url": r.get("base_url"),
                    "host": host,
                    **out,
                }
            )

    order = ["DNS", "CONNFAIL", "NOTFOUND", "TIMEOUT", "OTHER", "NOURL", "AUTH", "LIVE"]
    results.sort(key=lambda x: (order.index(str(x["verdict"])), str(x["slug"])))

    counts: Dict[str, int] = {}
    for r in results:
        counts[str(r["verdict"])] = counts.get(str(r["verdict"]), 0) + 1

    print(f"{'VERDICT':<9} {'STATUS':<7} {'SLUG':<26} {'HOST':<34} DETAIL")
    print("-" * 110)
    for r in results:
        print(
            f"{str(r['verdict']):<9} {str(r.get('status', '')):<7} {str(r['slug']):<26} "
            f"{str(r['host'])[:33]:<34} {str(r.get('detail', ''))[:40]}"
        )

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    bad = sum(counts.get(k, 0) for k in ("DNS", "CONNFAIL", "NOTFOUND", "NOURL"))
    print(f"Needs attention: {bad} / {len(results)}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"Wrote {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
