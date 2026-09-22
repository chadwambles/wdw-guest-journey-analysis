"""
Profile the labeled trip reports before building anything.

Answers:
  - How many reports survive the filters (WDW trip, inside the window)
  - How the personas split, and whether each has enough reports to chart
  - How many reports cover each stage, per persona, so you know which cells
    of the journey map are too thin to trust
  - A first look at the net score by stage (percent good minus percent bad)
  - Whether the peak-end test is viable: how many reports have both a
    departure rating and a verdict

Reads:  labels_wide.csv, labels_long.csv
Writes: journey_long.csv   the filtered long file to build the map from

Usage:
    py profile_labels.py
    py profile_labels.py --min-reports 20      change the thin-cell threshold
"""

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

WINDOW_YEAR, WINDOW_MONTH = 2024, 7  # Lightning Lane Multi Pass, late July 2024

STAGE_ORDER = [
    "planning_booking", "getting_there", "resort_stay", "transportation",
    "park_entry_crowds", "attractions", "lightning_lane_app", "dining",
    "entertainment_characters", "cast_members", "departure",
]
PERSONA_ORDER = ["families_with_kids", "couples_adults", "multigenerational",
                 "passholder_local", "unassigned"]


def read(name):
    if not os.path.exists(name):
        sys.exit("Can't find %s. Run label_reports.py fetch first." % name)
    with open(name, "r", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def in_window(row):
    """
    True if the trip happened on or after the window start. Reports with no
    trip year are kept and counted separately, since dropping them would
    silently bias toward reports that mention dates.
    """
    year, month = row.get("trip_year", ""), row.get("trip_month", "")
    if not year:
        return None
    year = int(year)
    if year != WINDOW_YEAR:
        return year > WINDOW_YEAR
    return int(month) >= WINDOW_MONTH if month else True


def bar(value, width=28):
    """Net score from -100 to 100 drawn around a centre line."""
    half = width // 2
    filled = int(round(abs(value) / 100.0 * half))
    if value >= 0:
        return " " * half + "|" + "#" * filled
    return " " * (half - filled) + "#" * filled + "|"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-reports", type=int, default=20,
                        help="flag cells with fewer reports than this (default 20)")
    args = parser.parse_args()

    wide = read("labels_wide.csv")
    long_rows = read("labels_long.csv")

    # ------------------------------------------------------------ filtering
    total = len(wide)
    not_wdw = [r for r in wide if r["is_wdw_trip"].strip().lower() in ("false", "0")]
    wdw = [r for r in wide if r not in not_wdw]
    no_date = [r for r in wdw if in_window(r) is None]
    before = [r for r in wdw if in_window(r) is False]
    keep = [r for r in wdw if in_window(r) is True]

    print("Reports labeled:                 %4d" % total)
    print("  not a WDW trip:                %4d" % len(not_wdw))
    print("  trip before the window:        %4d" % len(before))
    print("  no trip date found:            %4d" % len(no_date))
    print("  IN SCOPE:                      %4d" % len(keep))
    print("\nWindow starts %d-%02d (Lightning Lane Multi Pass)." % (WINDOW_YEAR, WINDOW_MONTH))
    print("Undated reports are excluded below. If that number is large, consider")
    print("keeping them and noting the caveat instead.")

    keep_ids = {r["id"] for r in keep}

    # ------------------------------------------------------------- personas
    personas = Counter(r["persona"] for r in keep)
    print("\nPersonas in scope")
    for name in PERSONA_ORDER:
        n = personas.get(name, 0)
        flag = "   <-- thin" if 0 < n < args.min_reports else ""
        print("  %-22s %4d  (%2d%%)%s" % (name, n, 100 * n // max(len(keep), 1), flag))

    # -------------------------------------------------------- trip timeline
    months = Counter()
    for r in keep:
        if r["trip_year"] and r["trip_month"]:
            months["%s-%02d" % (r["trip_year"], int(r["trip_month"]))] += 1
    if months:
        print("\nTrips per month (in scope)")
        for month in sorted(months):
            print("  %s  %3d  %s" % (month, months[month], "#" * months[month]))

    # ---------------------------------------------------------- stage stats
    rows = [r for r in long_rows if r["id"] in keep_ids]
    by_stage = defaultdict(Counter)
    by_stage_persona = defaultdict(Counter)
    for r in rows:
        by_stage[r["stage_key"]][r["rating"]] += 1
        by_stage_persona[(r["stage_key"], r["persona"])][r["rating"]] += 1

    def net(counter):
        n = sum(counter.values())
        if not n:
            return None, 0
        return round(100.0 * (counter["good"] - counter["bad"]) / n, 1), n

    print("\nNet score by stage, all personas (percent good minus percent bad)")
    print("%-26s %6s %6s  %s" % ("stage", "net", "n", "bad" + " " * 10 + "0" + " " * 10 + "good"))
    for stage in STAGE_ORDER:
        score, n = net(by_stage[stage])
        if score is None:
            print("  %-24s %6s %6d" % (stage, "-", 0))
            continue
        flag = "  <-- thin" if n < args.min_reports else ""
        print("  %-24s %+6.1f %6d  %s%s" % (stage, score, n, bar(score), flag))

    print("\nReports mentioning each stage, by persona")
    header = "  %-24s" % "stage" + "".join("%12s" % p[:11] for p in PERSONA_ORDER[:4])
    print(header)
    for stage in STAGE_ORDER:
        line = "  %-24s" % stage
        for persona in PERSONA_ORDER[:4]:
            n = sum(by_stage_persona[(stage, persona)].values())
            line += "%12s" % (str(n) + ("*" if 0 < n < args.min_reports else ""))
        print(line)
    print("  * fewer than %d reports: too thin to read much into" % args.min_reports)

    # -------------------------------------------------------- peak and end
    verdicts = Counter(r["verdict"] for r in keep)
    has_departure = {r["id"] for r in rows if r["stage_key"] == "departure"}
    has_verdict = {r["id"] for r in keep if r["verdict"] != "none"}
    has_peak = {r["id"] for r in keep if r["peak_stage"] not in ("none", "")}
    both = has_departure & has_verdict
    all_three = both & has_peak

    print("\nVerdicts (in scope)")
    for name, n in verdicts.most_common():
        print("  %-8s %4d" % (name, n))

    print("\nPeak-end test readiness")
    print("  reports with a verdict:           %4d" % len(has_verdict))
    print("  reports with a peak stage:        %4d" % len(has_peak))
    print("  reports with a departure rating:  %4d" % len(has_departure))
    print("  with verdict AND departure:       %4d" % len(both))
    print("  with verdict, peak AND departure: %4d" % len(all_three))
    if len(all_three) < 60:
        print("  Thin. The test may have to run on verdict plus peak only,")
        print("  treating 'end' as departure where it exists.")

    # ------------------------------------------------------------- output
    with open("journey_long.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=long_rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print("\nWrote journey_long.csv (%d stage rows, %d reports) for Tableau." % (
        len(rows), len(keep_ids)))


if __name__ == "__main__":
    main()
