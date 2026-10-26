#!/usr/bin/env python3
"""Summarise a BrewCart Nginx access log: top IPs, status codes, slowest endpoints, error rate per hour."""
import argparse
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

log = logging.getLogger("log_report")

# Matches the 'brewcart' log_format: combined format + $request_time at the end
LINE_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" '
    r'(?P<status>\d{3}) (?P<size>\d+|-) '
    r'"[^"]*" "[^"]*" (?P<rt>\d+(?:\.\d+)?)$'
)
TIME_FMT = "%d/%b/%Y:%H:%M:%S %z"
ARG_TIME_FMT = "%Y-%m-%d %H:%M"


def parse_line(line):
    """Return a dict for one log line, or None if the line is malformed."""
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    try:
        when = datetime.strptime(m["time"], TIME_FMT)
    except ValueError:
        return None
    return {
        "ip": m["ip"],
        "time": when,
        "method": m["method"],
        "path": normalise_path(m["path"]),
        "status": int(m["status"]),
        "request_time": float(m["rt"]),
    }


def normalise_path(path):
    """Drop the query string and replace numeric IDs so /api/orders/42 and /api/orders/7 group together."""
    path = path.split("?", 1)[0]
    return re.sub(r"/\d+(?=/|$)", "/{id}", path)


def in_window(entry, since=None, until=None):
    """since/until are naive datetimes in the log's own timezone."""
    local = entry["time"].replace(tzinfo=None)
    if since and local < since:
        return False
    if until and local >= until:
        return False
    return True


def summarise(entries, top=10, min_requests=20):
    ips = Counter()
    statuses = Counter()
    times = defaultdict(list)
    hours = defaultdict(lambda: [0, 0])  # hour -> [total, 5xx]
    for e in entries:
        ips[e["ip"]] += 1
        statuses[e["status"]] += 1
        times[f'{e["method"]} {e["path"]}'].append(e["request_time"])
        bucket = hours[e["time"].strftime("%Y-%m-%d %H:00")]
        bucket[0] += 1
        if e["status"] >= 500:
            bucket[1] += 1
    slowest = sorted(
        ((ep, sum(t) / len(t), max(t), len(t)) for ep, t in times.items() if len(t) >= min_requests),
        key=lambda row: row[1],
        reverse=True,
    )[:top]
    total = sum(statuses.values())
    errors = sum(n for code, n in statuses.items() if code >= 500)
    return {
        "total": total,
        "top_ips": ips.most_common(top),
        "statuses": sorted(statuses.items()),
        "slowest": slowest,
        "error_rate": (errors / total * 100) if total else 0.0,
        "hourly": {h: (t, e, e / t * 100) for h, (t, e) in sorted(hours.items())},
    }


def print_report(s, threshold):
    print(f"Requests analysed: {s['total']}    overall 5xx error rate: {s['error_rate']:.2f}%\n")
    print("Top IPs")
    for ip, n in s["top_ips"]:
        print(f"  {ip:<18} {n:>7}")
    print("\nStatus codes")
    for code, n in s["statuses"]:
        print(f"  {code:<6} {n:>7}  ({n / s['total'] * 100:5.1f}%)")
    print("\nSlowest endpoints (average seconds)")
    for ep, avg, worst, n in s["slowest"]:
        print(f"  {ep:<28} avg {avg:7.3f}  max {worst:7.3f}  ({n} requests)")
    print(f"\nHours with 5xx error rate >= {threshold}%")
    bad = [(h, v) for h, v in s["hourly"].items() if v[2] >= threshold]
    if not bad:
        print("  none")
    for hour, (total, errors, rate) in bad:
        print(f"  {hour}  {errors:>5}/{total:<5} {rate:6.1f}%")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Summarise a BrewCart Nginx access log.")
    p.add_argument("logfile", help="path to the access log")
    p.add_argument("--since", type=lambda v: datetime.strptime(v, ARG_TIME_FMT), help='start, e.g. "2026-10-23 14:00" (log time zone)')
    p.add_argument("--until", type=lambda v: datetime.strptime(v, ARG_TIME_FMT), help='end (exclusive), same format')
    p.add_argument("--top", type=int, default=10, help="how many IPs and endpoints to show (default 10)")
    p.add_argument("--error-threshold", type=float, default=5.0, help="show hours at or above this 5xx %% (default 5.0)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    entries, bad = [], 0
    try:
        with open(args.logfile, encoding="utf-8", errors="replace") as fh:
            for number, line in enumerate(fh, start=1):
                entry = parse_line(line)
                if entry is None:
                    bad += 1
                    log.debug("Skipping malformed line %d: %r", number, line[:80])
                elif in_window(entry, args.since, args.until):
                    entries.append(entry)
    except FileNotFoundError:
        log.error("Log file not found: %s", args.logfile)
        return 2
    except PermissionError:
        log.error("No permission to read: %s", args.logfile)
        return 2
    if bad:
        log.warning("Skipped %d malformed line(s)", bad)
    if not entries:
        log.warning("No requests in the selected window")
        return 1
    log.info("Parsed %d requests from %s", len(entries), args.logfile)
    print_report(summarise(entries, top=args.top), args.error_threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())