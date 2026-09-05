#!/usr/bin/env python3
"""Pure logic (no git) for rolling up knowledge/performance-summaries.jsonl:
weekly -> monthly -> quarterly -> half-yearly -> yearly, aggregating the
plain numeric performance data (spend, orders, order value) and discarding
the finer-grained rows it replaces.

Added 2026-09-05, Suraj's proposal: keeps this file's total size bounded
indefinitely (a 10-year-old account only ever holds ~9 yearly rows plus the
current year's finer-grained ones) so a check against this file never gets
slower as the account ages - exactly the class of problem the daily
heartbeat's due-now sweep hit at 253 log entries (see
scripts/update-open-followups.py). This is safe to discard the source rows
for ONLY because these are plain numeric aggregates - unlike
knowledge/learning-log.jsonl, where an entry's specific reasoning is the
entire point and rolling it up would destroy exactly the detail that log
exists to preserve (see docs/learning-layer-design.md SS8). Never apply this
kind of rollup-and-discard to the learning log itself.

Record schema, one per line in knowledge/performance-summaries.jsonl:
    {
      "id": "PERF-2026-W35" | "PERF-2026-09" | "PERF-2026-Q3" |
            "PERF-2026-H2" | "PERF-2026",
      "granularity": "weekly"|"monthly"|"quarterly"|"half-yearly"|"yearly",
      "period_start": "YYYY-MM-DD", "period_end": "YYYY-MM-DD",
      "spend": float, "orders": int, "order_value": float,
      "blended_cpo": float|null,   # spend / orders
      "blended_roas": float|null,  # order_value / spend
      "generated_at": ISO8601, "source": "weekly-review"|"monthly-review"|"rollup"
    }

A rolled-up row's blended_cpo/blended_roas are always recomputed from its
own summed spend/orders/order_value - never averaged from the sub-periods'
already-blended figures, which would be a real (if subtle) math error.

Grouping rule for which weekly rows belong to which calendar month: by the
calendar month containing that week's `period_start`. A week straddling a
month boundary is a real but rare edge case (a handful of days each month) -
not worth prorating for a P&L-adjacent but ultimately directional metric;
documented here rather than silently assumed.

CLI:
    rollup_performance_summaries.py <path-to-performance-summaries.jsonl>
Prints one JSON object to stdout describing what changed:
    {"changed": true, "added": [...new rows...], "removed": ["id1", "id2", ...]}
    {"changed": false}
Never writes the file itself - the caller (scripts/run-performance-rollup.sh)
owns the git-safe read/write/commit/push cycle, recomputing this fresh on
every retry rather than replaying a stale diff, since this operates on a
rewrite, not a pure append (see that script's own comments for why).
"""
import json
import sys
from collections import defaultdict
from datetime import date, timedelta


def load_records(path):
    records = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except FileNotFoundError:
        pass
    return records


def aggregate(records, new_id, granularity, period_start, period_end, source="rollup"):
    spend = sum(r.get("spend") or 0 for r in records)
    orders = sum(r.get("orders") or 0 for r in records)
    order_value = sum(r.get("order_value") or 0 for r in records)
    return {
        "id": new_id,
        "granularity": granularity,
        "period_start": period_start,
        "period_end": period_end,
        "spend": spend,
        "orders": orders,
        "order_value": order_value,
        "blended_cpo": (spend / orders) if orders else None,
        "blended_roas": (order_value / spend) if spend else None,
        "generated_at": None,  # filled in by the caller with the real current time
        "source": source,
    }


def quarter_of(month):
    return (month - 1) // 3 + 1


def quarter_months(year, q):
    start_month = (q - 1) * 3 + 1
    return [start_month, start_month + 1, start_month + 2]


def half_of(month):
    return 1 if month <= 6 else 2


def month_bounds(year, month):
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def compute_rollup(all_records):
    """Returns (added_rows, removed_ids). Cascades: a fresh monthly rollup
    can immediately make a quarterly rollup possible, which can immediately
    make a half-yearly one possible, and so on - all evaluated in one pass
    against the resulting state, not just the original file's state."""
    records = list(all_records)
    added = []
    removed = []

    # --- weekly -> monthly ---
    weeklies = [r for r in records if r.get("granularity") == "weekly"]
    by_month = defaultdict(list)
    for r in weeklies:
        d = date.fromisoformat(r["period_start"])
        by_month[(d.year, d.month)].append(r)

    existing_monthly_ids = {r["id"] for r in records if r.get("granularity") == "monthly"}
    today = date.today()
    for (year, month), rows in by_month.items():
        new_id = f"PERF-{year}-{month:02d}"
        if new_id in existing_monthly_ids:
            continue
        # Only roll up a month that has fully ended - never a partial/current month.
        _, month_end = month_bounds(year, month)
        if date.fromisoformat(month_end) >= today:
            continue
        start, end = month_bounds(year, month)
        row = aggregate(rows, new_id, "monthly", start, end, source="rollup")
        added.append(row)
        removed.extend(r["id"] for r in rows)
        existing_monthly_ids.add(new_id)

    # Apply weekly->monthly before evaluating monthly->quarterly, so a
    # month that just got created this pass is immediately eligible.
    working = [r for r in records if r["id"] not in removed] + added

    # --- monthly -> quarterly ---
    monthlies = [r for r in working if r.get("granularity") == "monthly"]
    by_quarter = defaultdict(dict)  # (year, q) -> {month: record}
    for r in monthlies:
        d = date.fromisoformat(r["period_start"])
        by_quarter[(d.year, quarter_of(d.month))][d.month] = r

    existing_quarterly_ids = {r["id"] for r in working if r.get("granularity") == "quarterly"}
    for (year, q), months_present in by_quarter.items():
        new_id = f"PERF-{year}-Q{q}"
        if new_id in existing_quarterly_ids:
            continue
        needed = quarter_months(year, q)
        if not all(m in months_present for m in needed):
            continue
        rows = [months_present[m] for m in needed]
        start = rows[0]["period_start"]
        end = rows[-1]["period_end"]
        row = aggregate(rows, new_id, "quarterly", start, end, source="rollup")
        added.append(row)
        removed.extend(r["id"] for r in rows)
        existing_quarterly_ids.add(new_id)

    working = [r for r in working if r["id"] not in removed] + [r for r in added if r["granularity"] == "quarterly"]

    # --- quarterly -> half-yearly ---
    quarterlies = [r for r in working if r.get("granularity") == "quarterly"]
    by_half = defaultdict(dict)
    for r in quarterlies:
        d = date.fromisoformat(r["period_start"])
        by_half[(d.year, half_of(d.month))][quarter_of(d.month)] = r

    existing_half_ids = {r["id"] for r in working if r.get("granularity") == "half-yearly"}
    for (year, h), quarters_present in by_half.items():
        new_id = f"PERF-{year}-H{h}"
        if new_id in existing_half_ids:
            continue
        needed = [1, 2] if h == 1 else [3, 4]
        if not all(q in quarters_present for q in needed):
            continue
        rows = [quarters_present[q] for q in needed]
        start = rows[0]["period_start"]
        end = rows[-1]["period_end"]
        row = aggregate(rows, new_id, "half-yearly", start, end, source="rollup")
        added.append(row)
        removed.extend(r["id"] for r in rows)
        existing_half_ids.add(new_id)

    working = [r for r in working if r["id"] not in removed] + [r for r in added if r["granularity"] == "half-yearly"]

    # --- half-yearly -> yearly ---
    halves = [r for r in working if r.get("granularity") == "half-yearly"]
    by_year = defaultdict(dict)
    for r in halves:
        d = date.fromisoformat(r["period_start"])
        by_year[d.year][half_of(d.month)] = r

    existing_year_ids = {r["id"] for r in working if r.get("granularity") == "yearly"}
    for year, halves_present in by_year.items():
        new_id = f"PERF-{year}"
        if new_id in existing_year_ids:
            continue
        if not all(h in halves_present for h in (1, 2)):
            continue
        rows = [halves_present[1], halves_present[2]]
        start = rows[0]["period_start"]
        end = rows[-1]["period_end"]
        row = aggregate(rows, new_id, "yearly", start, end, source="rollup")
        added.append(row)
        removed.extend(r["id"] for r in rows)

    return added, removed


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"changed": False, "error": "usage: rollup_performance_summaries.py <path>"}))
        sys.exit(2)
    records = load_records(sys.argv[1])
    added, removed = compute_rollup(records)
    if not added and not removed:
        print(json.dumps({"changed": False}))
    else:
        print(json.dumps({"changed": True, "added": added, "removed": removed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
