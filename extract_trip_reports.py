"""
Pull the Trip Report posts out of posts_merged.jsonl and profile them.

This is a look-before-you-build pass. It answers:
  - How many Trip Report posts have usable text
  - How long they run
  - How many are written day by day (good for the journey map)
  - How many end with a verdict (needed for the peak-end test)
  - A rough first read on persona signals

Also checks Passholder-flaired posts, since some annual passholder trip
reports may have been filed there instead of under Trip Report.

Reads:  posts_merged.jsonl
Writes: trip_reports.jsonl      usable reports, trimmed to the fields needed
        trip_reports_index.csv  one row per report, no body text, for
                                browsing in Excel

Both output files contain other people's writing. Keep them on your machine
and out of GitHub: add them to .gitignore.

Usage:
    py extract_trip_reports.py
    py extract_trip_reports.py --min-words 200
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone

SOURCE = "posts_merged.jsonl"
MIN_WORDS_DEFAULT = 150

# Day by day structure: "Day 1", "Day One", "**Day 3**", "## Day 2", "DAY 4:"
DAY_RE = re.compile(
    r"\bday\s*(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I
)
# Closing verdict or wrap-up language, used for the peak-end test later.
VERDICT_RE = re.compile(
    r"\b(overall|final thoughts|in conclusion|to sum up|all in all|takeaways?|"
    r"tl;?dr|would (we|i) go back|will (we|i) (be back|return|go back)|"
    r"never again|worth it|not worth it|best trip|worst trip|can'?t wait to go back)\b",
    re.I,
)

# Rough persona signals. Overlapping on purpose: this is a preview, not labels.
PERSONA_SIGNALS = {
    "families_young_kids": re.compile(
        r"\b(toddler|stroller|nap|baby|infant|preschool|"
        r"(my|our) (son|daughter|kids?|little ones?)|"
        r"\d{1,2}\s*(yo|y/o|year[- ]old|years? old))\b",
        re.I,
    ),
    "couples_adults": re.compile(
        r"\b(my (wife|husband|partner|boyfriend|girlfriend|fianc[eé]e?) and i|"
        r"(husband|wife|partner) and i|honeymoon|anniversary|"
        r"adults? only|no kids|just the two of us|solo trip|by myself)\b",
        re.I,
    ),
    "multigenerational": re.compile(
        r"\b(grandparents?|grandkids?|grandchildren|grandma|grandpa|"
        r"my (mom|dad|parents|mother|father|in[- ]laws)|"
        r"three generations|multi[- ]?gen(erational)?)\b",
        re.I,
    ),
    "passholder_local": re.compile(
        r"\b(annual pass(holder)?|passholder|\bAP\b|locals?|"
        r"floridians?|live (nearby|close|in (orlando|florida|central florida))|"
        r"day trip|drove over after work)\b",
        re.I,
    ),
}


def to_dt(value):
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def load(path):
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        sys.exit("Can't find %s. Run merge_posts.py first." % path)
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def usability(text, min_words):
    stripped = (text or "").strip()
    if stripped in ("[removed]", "[deleted]"):
        return "removed"
    if not stripped:
        return "no_text"
    if len(stripped.split()) < min_words:
        return "too_short"
    return "usable"


def percentile(sorted_values, p):
    if not sorted_values:
        return 0
    k = (len(sorted_values) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return int(round(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-words", type=int, default=MIN_WORDS_DEFAULT,
                        help="shortest body counted as usable (default %d)" % MIN_WORDS_DEFAULT)
    args = parser.parse_args()

    status_counts = {"usable": 0, "too_short": 0, "no_text": 0, "removed": 0}
    usable = []
    passholder_candidates = 0
    total_trip = 0

    for post in load(SOURCE):
        flair = (post.get("link_flair_text") or "").lower()
        text = post.get("selftext") or ""
        words = len(text.split())

        if flair == "passholder":
            if words >= 400 and re.search(r"\b(trip|visit|day \d|we did)\b", text, re.I):
                passholder_candidates += 1
            continue

        if "trip report" not in flair:
            continue

        total_trip += 1
        status = usability(text, args.min_words)
        status_counts[status] += 1
        if status != "usable":
            continue

        created = to_dt(post.get("created_utc"))
        body = post.get("title", "") + "\n" + text
        record = {
            "id": post.get("id"),
            "created_utc": post.get("created_utc"),
            "date": created.date().isoformat() if created else "",
            "title": post.get("title", ""),
            "author": post.get("author", ""),
            "score": post.get("score"),
            "num_comments": post.get("num_comments"),
            "permalink": post.get("permalink", ""),
            "word_count": words,
            "day_markers": len(set(m.group(1).lower() for m in DAY_RE.finditer(text))),
            "has_verdict": int(bool(VERDICT_RE.search(text[-1500:]))),
            "selftext": text,
        }
        for name, pattern in PERSONA_SIGNALS.items():
            record["sig_" + name] = int(bool(pattern.search(body)))
        usable.append(record)

    if not total_trip:
        sys.exit("No Trip Report posts found. Check the flair name in posts_merged.jsonl.")

    usable.sort(key=lambda r: float(r["created_utc"] or 0))

    with open("trip_reports.jsonl", "w", encoding="utf-8") as out:
        for record in usable:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    index_fields = ["id", "date", "title", "word_count", "day_markers", "has_verdict",
                    "sig_families_young_kids", "sig_couples_adults",
                    "sig_multigenerational", "sig_passholder_local",
                    "score", "num_comments", "permalink"]
    with open("trip_reports_index.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=index_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(usable)

    # ------------------------------------------------------------- report
    n = len(usable)
    lengths = sorted(r["word_count"] for r in usable)
    structured = sum(1 for r in usable if r["day_markers"] >= 2)
    verdicts = sum(1 for r in usable if r["has_verdict"])
    both = sum(1 for r in usable if r["day_markers"] >= 2 and r["has_verdict"])
    no_persona = sum(1 for r in usable if not any(r["sig_" + k] for k in PERSONA_SIGNALS))

    print("Trip Report posts:            %d" % total_trip)
    print("  usable (%d+ words):         %d" % (args.min_words, status_counts["usable"]))
    print("  too short:                  %d" % status_counts["too_short"])
    print("  no body text (images/links):%d" % status_counts["no_text"])
    print("  removed or deleted:         %d" % status_counts["removed"])

    print("\nLength of usable reports (words)")
    for p in (10, 25, 50, 75, 90):
        print("  %2dth percentile: %6d" % (p, percentile(lengths, p)))
    print("  longest:          %6d" % lengths[-1])

    print("\nStructure")
    print("  day by day (2+ day markers):   %4d  (%d%%)" % (structured, 100 * structured // n))
    print("  ends with a verdict:           %4d  (%d%%)" % (verdicts, 100 * verdicts // n))
    print("  both, ideal for peak-end:      %4d  (%d%%)" % (both, 100 * both // n))

    print("\nPersona signals (rough, overlapping, keyword-based)")
    for name in PERSONA_SIGNALS:
        hits = sum(1 for r in usable if r["sig_" + name])
        print("  %-22s %4d  (%d%%)" % (name, hits, 100 * hits // n))
    print("  %-22s %4d  (%d%%)" % ("no signal at all", no_persona, 100 * no_persona // n))

    print("\nPassholder-flaired posts that look like trip reports: %d" % passholder_candidates)
    print("  (400+ words mentioning a trip or visit; worth skimming before")
    print("   deciding whether to add them)")

    print("\nWrote trip_reports.jsonl and trip_reports_index.csv")
    print("Both contain other people's writing: keep them out of GitHub.")


if __name__ == "__main__":
    main()
