"""
Analyze large_tool_results.jsonl to understand what drives big tool call responses
and identify optimization opportunities.

Usage:
    uv run python analysis/analyze_large_tool_results.py [path_or_dir ...]

Arguments can be individual files or directories. Directories are scanned for
files matching large_tool_results.json*. With no arguments, defaults to scanning
the analysis/ directory.
"""

import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DEFAULT_DIR = "analysis"
FILE_PATTERN = "large_tool_results.json*"


def find_input_files(args: list[str]) -> list[Path]:
    """Resolve CLI args into a sorted list of input files."""
    paths: set[Path] = set()
    targets = args if args else [DEFAULT_DIR]

    for target in targets:
        p = Path(target)
        if p.is_file():
            paths.add(p.resolve())
        elif p.is_dir():
            for match in sorted(p.glob(FILE_PATTERN)):
                if match.is_file():
                    paths.add(match.resolve())
        else:
            # Try as a glob pattern itself
            for match in glob.glob(target):
                mp = Path(match)
                if mp.is_file():
                    paths.add(mp.resolve())

    return sorted(paths)


def load_records_from_file(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # skip malformed lines
    return records


def load_records(files: list[Path]) -> list[dict]:
    records = []
    for f in files:
        records.extend(load_records_from_file(f))
    return records


def get_bytes(rec: dict) -> int:
    return rec.get("result_length_bytes", len(rec.get("result", "")))


def categorize_api(url: str) -> str:
    """Extract the API service name from a proxy URL."""
    # /api/gmail-simple/messages -> gmail-simple
    # /api/sheets-raw/v4/spreadsheets/... -> sheets-raw
    path = urlparse(url).path
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "api":
        return parts[1]
    return path


def normalize_url_pattern(url: str) -> str:
    """Collapse IDs to {id} for grouping."""
    parsed = urlparse(url)
    parts = parsed.path.split("/")
    normalized = []
    for p in parts:
        if len(p) > 20 or (p and p.isdigit()):
            normalized.append("{id}")
        else:
            normalized.append(p)
    return "/".join(normalized)


def section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def fmt_bytes(b: int) -> str:
    if b >= 1_000_000:
        return f"{b/1_000_000:.1f}MB"
    if b >= 1_000:
        return f"{b/1_000:.1f}KB"
    return f"{b}B"


def pct(part: int, total: int) -> str:
    return f"{100*part/total:.1f}%" if total else "N/A"


def report_overview(records: list[dict]):
    section("OVERVIEW")
    total_bytes = sum(get_bytes(r) for r in records)
    print(f"Total records:  {len(records):,}")
    print(f"Total bytes:    {fmt_bytes(total_bytes)} ({total_bytes:,})")
    print(f"Avg per record: {fmt_bytes(total_bytes // len(records))}")

    sizes = sorted(get_bytes(r) for r in records)
    print(f"Median:         {fmt_bytes(sizes[len(sizes)//2])}")
    print(f"P90:            {fmt_bytes(sizes[int(len(sizes)*0.9)])}")
    print(f"P99:            {fmt_bytes(sizes[int(len(sizes)*0.99)])}")
    print(f"Max:            {fmt_bytes(sizes[-1])}")


def report_by_tool(records: list[dict]):
    section("BREAKDOWN BY TOOL NAME")
    total_bytes = sum(get_bytes(r) for r in records)

    tool_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0, "max": 0})
    for r in records:
        s = tool_stats[r["tool_name"]]
        b = get_bytes(r)
        s["count"] += 1
        s["bytes"] += b
        s["max"] = max(s["max"], b)

    ranked = sorted(tool_stats.items(), key=lambda x: -x[1]["bytes"])
    print(f"{'Tool':<30} {'Count':>6} {'Total':>10} {'%Total':>7} {'Avg':>10} {'Max':>10}")
    print("-" * 80)
    for name, s in ranked:
        avg = s["bytes"] // s["count"]
        print(
            f"{name:<30} {s['count']:>6} {fmt_bytes(s['bytes']):>10} "
            f"{pct(s['bytes'], total_bytes):>7} {fmt_bytes(avg):>10} {fmt_bytes(s['max']):>10}"
        )


def report_by_api_service(records: list[dict]):
    section("BREAKDOWN BY API SERVICE (curl_proxy_get only)")
    curl_records = [r for r in records if r["tool_name"] in ("curl_proxy_get", "curl_proxy_post")]
    if not curl_records:
        print("No curl_proxy records found.")
        return

    total_bytes = sum(get_bytes(r) for r in curl_records)
    svc_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0, "max": 0})
    for r in curl_records:
        url = r.get("tool_args", {}).get("url", "")
        svc = categorize_api(url)
        b = get_bytes(r)
        s = svc_stats[svc]
        s["count"] += 1
        s["bytes"] += b
        s["max"] = max(s["max"], b)

    ranked = sorted(svc_stats.items(), key=lambda x: -x[1]["bytes"])
    print(f"{'Service':<25} {'Count':>6} {'Total':>10} {'%Curl':>7} {'Avg':>10} {'Max':>10}")
    print("-" * 75)
    for name, s in ranked:
        avg = s["bytes"] // s["count"]
        print(
            f"{name:<25} {s['count']:>6} {fmt_bytes(s['bytes']):>10} "
            f"{pct(s['bytes'], total_bytes):>7} {fmt_bytes(avg):>10} {fmt_bytes(s['max']):>10}"
        )


def report_url_patterns(records: list[dict]):
    section("TOP URL PATTERNS BY TOTAL BYTES")
    curl_records = [r for r in records if r["tool_name"] in ("curl_proxy_get", "curl_proxy_post")]
    total_bytes = sum(get_bytes(r) for r in curl_records)

    pat_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0, "max": 0})
    for r in curl_records:
        url = r.get("tool_args", {}).get("url", "")
        pattern = normalize_url_pattern(url)
        b = get_bytes(r)
        s = pat_stats[pattern]
        s["count"] += 1
        s["bytes"] += b
        s["max"] = max(s["max"], b)

    ranked = sorted(pat_stats.items(), key=lambda x: -x[1]["bytes"])[:20]
    print(f"{'Pattern':<55} {'Count':>5} {'Total':>9} {'%':>6} {'Avg':>9} {'Max':>9}")
    print("-" * 100)
    for pattern, s in ranked:
        avg = s["bytes"] // s["count"]
        print(
            f"{pattern:<55} {s['count']:>5} {fmt_bytes(s['bytes']):>9} "
            f"{pct(s['bytes'], total_bytes):>6} {fmt_bytes(avg):>9} {fmt_bytes(s['max']):>9}"
        )


def report_gmail_batch_analysis(records: list[dict]):
    section("GMAIL BATCH SIZE ANALYSIS")
    gmail_batches = []
    for r in records:
        if r["tool_name"] != "curl_proxy_get":
            continue
        url = r.get("tool_args", {}).get("url", "")
        if "/gmail-simple/messages" not in url:
            continue
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        ids = qs.get("ids", [""])[0]
        num_ids = len(ids.split(",")) if ids else 0
        gmail_batches.append((num_ids, get_bytes(r)))

    if not gmail_batches:
        print("No gmail-simple/messages calls found.")
        return

    # Bucket by batch size
    buckets: dict[str, list] = defaultdict(list)
    for num_ids, b in gmail_batches:
        if num_ids == 0:
            label = "0 (list)"
        elif num_ids <= 5:
            label = "1-5"
        elif num_ids <= 10:
            label = "6-10"
        elif num_ids <= 20:
            label = "11-20"
        elif num_ids <= 50:
            label = "21-50"
        else:
            label = "50+"
        buckets[label].append(b)

    total = sum(b for _, b in gmail_batches)
    order = ["0 (list)", "1-5", "6-10", "11-20", "21-50", "50+"]
    print(f"{'Batch Size':<12} {'Count':>6} {'Total':>10} {'%':>7} {'Avg':>10}")
    print("-" * 50)
    for label in order:
        if label not in buckets:
            continue
        vals = buckets[label]
        print(
            f"{label:<12} {len(vals):>6} {fmt_bytes(sum(vals)):>10} "
            f"{pct(sum(vals), total):>7} {fmt_bytes(sum(vals)//len(vals)):>10}"
        )
    print()
    print(f"Total gmail-simple/messages calls: {len(gmail_batches)}")
    print(f"Total bytes: {fmt_bytes(total)}")

    # Estimate savings from capping batch size
    print()
    print("--- Estimated savings from capping batch size ---")
    for cap in [10, 20, 25]:
        current = sum(b for _, b in gmail_batches)
        # Approximate: if batch > cap, scale down proportionally
        estimated = 0
        for num_ids, b in gmail_batches:
            if num_ids > cap and num_ids > 0:
                estimated += b * cap // num_ids
            else:
                estimated += b
        saved = current - estimated
        print(f"  Cap at {cap} ids/batch: ~{fmt_bytes(saved)} saved ({pct(saved, current)} reduction)")


def report_sheets_analysis(records: list[dict]):
    section("GOOGLE SHEETS RESPONSE ANALYSIS")
    sheet_calls = []
    for r in records:
        url = ""
        # Legacy pattern: curl_proxy_get with /sheets-raw/ proxy path
        if r["tool_name"] == "curl_proxy_get":
            url = r.get("tool_args", {}).get("url", "")
            if "/sheets-raw/" not in url:
                continue
        # New pattern: tool_call with authed_get and sheets.googleapis.com
        elif r["tool_name"] == "tool_call":
            inner_args = r.get("tool_args", {})
            if inner_args.get("tool_name") != "authed_get":
                continue
            inner_url = (inner_args.get("arguments") or {}).get("url", "")
            if "sheets.googleapis.com" not in inner_url:
                continue
            url = inner_url
        else:
            continue
        parsed = urlparse(url)
        path = parsed.path
        b = get_bytes(r)
        # Identify if it fetches full spreadsheet vs specific range
        if "/values/" in path:
            kind = "range-read"
        elif path.rstrip("/").endswith("}") or len(path.split("/")) <= 6:
            kind = "full-spreadsheet"
        else:
            kind = "other"
        sheet_calls.append((kind, b, url))

    if not sheet_calls:
        print("No sheets calls found.")
        return

    kind_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0, "max": 0, "urls": []})
    for kind, b, url in sheet_calls:
        s = kind_stats[kind]
        s["count"] += 1
        s["bytes"] += b
        s["max"] = max(s["max"], b)
        if len(s["urls"]) < 3:
            s["urls"].append((b, url[:100]))

    for kind, s in sorted(kind_stats.items(), key=lambda x: -x[1]["bytes"]):
        print(f"{kind}: count={s['count']}, total={fmt_bytes(s['bytes'])}, avg={fmt_bytes(s['bytes']//s['count'])}, max={fmt_bytes(s['max'])}")
        for b, url in s["urls"]:
            print(f"    {fmt_bytes(b):>8}  {url}")
    print()


def report_optimization_recommendations(records: list[dict]):
    section("OPTIMIZATION RECOMMENDATIONS")
    total_bytes = sum(get_bytes(r) for r in records)

    # 1. Gmail batch
    gmail_batch_bytes = 0
    gmail_batch_50_bytes = 0
    for r in records:
        if r["tool_name"] != "curl_proxy_get":
            continue
        url = r.get("tool_args", {}).get("url", "")
        if "/gmail-simple/messages" not in url:
            continue
        qs = parse_qs(urlparse(url).query)
        ids = qs.get("ids", [""])[0]
        num_ids = len(ids.split(",")) if ids else 0
        b = get_bytes(r)
        gmail_batch_bytes += b
        if num_ids > 20:
            gmail_batch_50_bytes += b

    # 2. Sheets full fetch
    sheets_full_bytes = 0
    for r in records:
        url = ""
        # Legacy pattern: curl_proxy_get with /sheets-raw/ proxy path
        if r["tool_name"] == "curl_proxy_get":
            url = r.get("tool_args", {}).get("url", "")
            if "/sheets-raw/" not in url:
                continue
        # New pattern: tool_call with authed_get and sheets.googleapis.com
        elif r["tool_name"] == "tool_call":
            inner_args = r.get("tool_args", {})
            if inner_args.get("tool_name") != "authed_get":
                continue
            inner_url = (inner_args.get("arguments") or {}).get("url", "")
            if "sheets.googleapis.com" not in inner_url:
                continue
            url = inner_url
        else:
            continue
        if "/values/" not in url:
            sheets_full_bytes += get_bytes(r)

    # 3. Slack search
    slack_search_bytes = 0
    for r in records:
        if r["tool_name"] != "curl_proxy_get":
            continue
        url = r.get("tool_args", {}).get("url", "")
        if "/slack/search.messages" in url:
            slack_search_bytes += get_bytes(r)

    print("Priority-ordered optimization opportunities:\n")

    recs = []

    recs.append((
        gmail_batch_bytes,
        "1. GMAIL BATCH FETCHING (biggest win)",
        f"   Impact: {fmt_bytes(gmail_batch_bytes)} = {pct(gmail_batch_bytes, total_bytes)} of all large results",
        "   Problem: Fetching up to 50 full messages in one call produces multi-MB responses.",
        "   Fix options:",
        "     a) Cap batch size to 10-20 ids per request (simple, ~40-60% reduction)",
        "     b) Fetch message summaries first, full body only when needed",
        "     c) Truncate/summarize message bodies server-side before returning",
        "     d) Strip HTML/quoted-reply chains from email bodies",
    ))

    recs.append((
        sheets_full_bytes,
        "2. SHEETS FULL SPREADSHEET METADATA",
        f"   Impact: {fmt_bytes(sheets_full_bytes)} = {pct(sheets_full_bytes, total_bytes)} of all large results",
        "   Problem: Fetching full spreadsheet object includes all cell data + formatting metadata.",
        "   Fix options:",
        "     a) Use fields parameter to request only needed properties",
        "     b) Prefer /values/ range reads over full spreadsheet fetches",
        "     c) Cache spreadsheet structure, only fetch data ranges",
    ))

    recs.append((
        slack_search_bytes,
        "3. SLACK MESSAGE SEARCH",
        f"   Impact: {fmt_bytes(slack_search_bytes)} = {pct(slack_search_bytes, total_bytes)} of all large results",
        "   Problem: Search results include full message objects with metadata.",
        "   Fix options:",
        "     a) Limit results count in search queries",
        "     b) Return only message text + timestamp + channel, strip attachments/blocks",
        "     c) Paginate with smaller page sizes",
    ))

    # Sort by impact
    recs.sort(key=lambda x: -x[0])
    for rec in recs:
        for line in rec[1:]:
            print(line)
        print()

    # Summary
    addressable = gmail_batch_bytes + sheets_full_bytes + slack_search_bytes
    print(f"Total addressable: ~{fmt_bytes(addressable)} ({pct(addressable, total_bytes)} of large results)")
    print(f"Estimated realistic savings: ~{fmt_bytes(int(addressable * 0.5))} (50% reduction with above fixes)")


def report_by_conversation(records: list[dict]):
    section("TOP CONVERSATIONS BY TOTAL BYTES")
    conv_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0, "tools": Counter()})
    for r in records:
        cid = r.get("conversation_id", "unknown")
        b = get_bytes(r)
        s = conv_stats[cid]
        s["count"] += 1
        s["bytes"] += b
        s["tools"][r["tool_name"]] += 1

    ranked = sorted(conv_stats.items(), key=lambda x: -x[1]["bytes"])[:15]
    for cid, s in ranked:
        top_tools = ", ".join(f"{t}={c}" for t, c in s["tools"].most_common(3))
        print(f"  {cid[:12]}...  calls={s['count']:>3}  bytes={fmt_bytes(s['bytes']):>10}  top: {top_tools}")


def main():
    files = find_input_files(sys.argv[1:])
    if not files:
        print(f"No files matching '{FILE_PATTERN}' found.")
        print(f"Usage: python {sys.argv[0]} [path_or_dir ...]")
        sys.exit(1)

    print(f"Loading {len(files)} file(s):")
    for f in files:
        print(f"  {f} ({f.stat().st_size:,} bytes)")

    records = load_records(files)

    report_overview(records)
    report_by_tool(records)
    report_by_api_service(records)
    report_url_patterns(records)
    report_gmail_batch_analysis(records)
    report_sheets_analysis(records)
    report_by_conversation(records)
    report_optimization_recommendations(records)


if __name__ == "__main__":
    main()
