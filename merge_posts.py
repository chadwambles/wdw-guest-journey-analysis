"""
Merge the Arctic Shift downloads for r/WaltDisneyWorld and verify them.

What it does
------------
  1. Reads every posts_*.jsonl file in this folder
  2. Drops duplicate posts where date ranges overlap (same post id)
  3. Writes one combined file, posts_merged.jsonl, sorted oldest first
  4. Checks coverage: posts per month, and any stretch of days with no posts,
     since a subreddit this size should have posts every single day
  5. Lists the post flairs, which is how trip reports will be found next
  6. Counts removed and deleted posts, which have no usable text

Comments are optional. They're roughly 2 GB, and trip reports live in the
post body, so they aren't needed yet. With --comments they are streamed and
deduplicated without loading them all into memory.

Usage:
    py merge_posts.py
    py merge_posts.py --comments

Output:
    posts_merged.jsonl
    comments_merged.jsonl   (only with --comments)
"""

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

WINDOW_START = datetime(2024, 7, 24, tzinfo=timezone.utc).date()


def to_date(value):
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OverflowError):
        return None


def read_jsonl(path):
    """Yield (record, error) one line at a time, tolerating bad lines."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line), None
            except json.JSONDecodeError:
                yield None, True


# ------------------------------------------------------------------ posts


def merge_posts():
    files = sorted(glob.glob("posts_*.jsonl"))
    files = [f for f in files if f != "posts_merged.jsonl"]
    if not files:
        sys.exit("No posts_*.jsonl files found in this folder.")

    posts = {}
    duplicates = 0
    bad_lines = 0

    print("Input files")
    print("-" * 72)
    for path in files:
        count = 0
        dates = []
        for record, error in read_jsonl(path):
            if error:
                bad_lines += 1
                continue
            post_id = record.get("id")
            if not post_id:
                continue
            count += 1
            day = to_date(record.get("created_utc"))
            if day:
                dates.append(day)
            if post_id in posts:
                duplicates += 1
            else:
                posts[post_id] = record
        span = "%s to %s" % (min(dates), max(dates)) if dates else "no dates"
        print("%-42s %8d posts   %s" % (path, count, span))

    ordered = sorted(posts.values(), key=lambda r: float(r.get("created_utc") or 0))
    with open("posts_merged.jsonl", "w", encoding="utf-8") as out:
        for record in ordered:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")

    print("-" * 72)
    print("Unique posts:      %d" % len(ordered))
    print("Duplicates dropped: %d" % duplicates)
    if bad_lines:
        print("Unreadable lines:   %d  (skipped)" % bad_lines)
    print("Wrote posts_merged.jsonl")
    return ordered


def check_coverage(posts):
    days = Counter()
    for record in posts:
        day = to_date(record.get("created_utc"))
        if day:
            days[day] += 1
    if not days:
        print("\nNo dated posts, cannot check coverage.")
        return

    first, last = min(days), max(days)
    print("\nCoverage: %s to %s" % (first, last))
    if first > WINDOW_START:
        print("  WARNING: starts after %s, the start of your window" % WINDOW_START)

    months = defaultdict(int)
    for day, n in days.items():
        months[day.strftime("%Y-%m")] += n
    values = sorted(months.values())
    median = values[len(values) // 2]

    print("\nPosts per month (flagged if under half the median of %d)" % median)
    for month in sorted(months):
        flag = "   <-- low, possible missing data" if months[month] < median / 2 else ""
        print("  %s  %6d%s" % (month, months[month], flag))
    print("  (first and last months are partial, so lower counts are expected)")

    gaps = []
    day = first
    run_start = None
    while day <= last:
        if days.get(day, 0) == 0:
            run_start = run_start or day
        elif run_start:
            gaps.append((run_start, day - timedelta(days=1)))
            run_start = None
        day += timedelta(days=1)
    if run_start:
        gaps.append((run_start, last))

    if gaps:
        print("\nDays with zero posts (a subreddit this size shouldn't have any):")
        for start, end in gaps:
            length = (end - start).days + 1
            print("  %s to %s  (%d day%s)" % (start, end, length, "" if length == 1 else "s"))
        print("Re-download those dates if the gap sits on a file boundary.")
    else:
        print("\nNo days with zero posts. Coverage is continuous.")


def summarize_flairs(posts):
    flairs = Counter((r.get("link_flair_text") or "(no flair)").strip() for r in posts)
    print("\nPost flairs (top 30)")
    for flair, n in flairs.most_common(30):
        print("  %6d  %s" % (n, flair))

    trip = sum(n for f, n in flairs.items() if "trip report" in f.lower())
    print("\nFlairs containing 'trip report': %d posts" % trip)

    removed = sum(1 for r in posts if (r.get("selftext") or "").strip() in ("[removed]", "[deleted]"))
    empty = sum(1 for r in posts if not (r.get("selftext") or "").strip())
    print("Removed or deleted text: %d" % removed)
    print("No body text at all:      %d  (link and image posts, or removed)" % empty)


# --------------------------------------------------------------- comments


def merge_comments():
    files = sorted(glob.glob("comments_*.jsonl"))
    files = [f for f in files if f != "comments_merged.jsonl"]
    if not files:
        print("\nNo comments_*.jsonl files found.")
        return

    print("\nMerging comments (streaming, this takes a while)")
    seen = set()
    written = duplicates = bad_lines = 0
    with open("comments_merged.jsonl", "w", encoding="utf-8") as out:
        for path in files:
            count = 0
            for record, error in read_jsonl(path):
                if error:
                    bad_lines += 1
                    continue
                comment_id = record.get("id")
                if not comment_id:
                    continue
                count += 1
                if comment_id in seen:
                    duplicates += 1
                    continue
                seen.add(comment_id)
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
            print("  %-42s %9d comments" % (path, count))
    print("Unique comments: %d, duplicates dropped: %d" % (written, duplicates))
    if bad_lines:
        print("Unreadable lines: %d (skipped)" % bad_lines)
    print("Wrote comments_merged.jsonl (not sorted, to save memory)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comments", action="store_true",
                        help="also merge the comment files (about 2 GB)")
    args = parser.parse_args()

    posts = merge_posts()
    check_coverage(posts)
    summarize_flairs(posts)
    if args.comments:
        merge_comments()


if __name__ == "__main__":
    main()
