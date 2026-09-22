"""
Build the publishable version of the labeled dataset.

Strips everything that is someone else's writing (evidence quotes, titles,
post bodies, usernames) and keeps only the derived labels plus the Reddit
post id, so anyone can verify a label against the original post.

Reads:  labels.jsonl, trip_reports.jsonl
Writes: publish/wdw_journey_labels_long.csv    one row per report per rated stage
        publish/wdw_journey_labels_wide.csv    one row per report

Refuses to write if any banned field survives into the output.

Usage:
    py build_kaggle_dataset.py
"""

import csv
import json
import os
import sys

OUT_DIR = "publish"
STAGE_KEYS = [
    "planning_booking", "getting_there", "resort_stay", "transportation",
    "park_entry_crowds", "attractions", "lightning_lane_app", "dining",
    "entertainment_characters", "cast_members", "departure",
]
STAGE_NAMES = {
    "planning_booking": "Planning and booking",
    "getting_there": "Getting there",
    "resort_stay": "Resort stay",
    "transportation": "Transportation around WDW",
    "park_entry_crowds": "Park entry and crowds",
    "attractions": "Attractions",
    "lightning_lane_app": "Lightning Lane and the app",
    "dining": "Dining",
    "entertainment_characters": "Entertainment and characters",
    "cast_members": "Cast members",
    "departure": "Departure",
}
PARTY_FACTS = ["party_known", "has_child_under_18", "has_adult_child_with_parent",
               "has_three_generations", "is_local_visit"]

# Anything resembling these must never reach the published files.
BANNED = ("evidence", "text", "title", "author", "body", "quote", "permalink")


def load_jsonl(path):
    if not os.path.exists(path):
        sys.exit("Can't find %s." % path)
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def as_bool(value):
    if value is None:
        return ""
    return "TRUE" if value else "FALSE"


def main():
    labels = load_jsonl("labels.jsonl")
    meta = {r["id"]: r for r in load_jsonl("trip_reports.jsonl")}
    os.makedirs(OUT_DIR, exist_ok=True)

    wide_fields = (["report_id", "posted_date", "word_count", "is_wdw_trip",
                    "trip_year", "trip_month", "persona"] + PARTY_FACTS
                   + ["verdict", "peak_stage"] + STAGE_KEYS)
    long_fields = ["report_id", "posted_date", "trip_year", "trip_month", "persona",
                   "is_wdw_trip", "stage_order", "stage_key", "stage", "rating",
                   "verdict", "is_peak"]

    for field in wide_fields + long_fields:
        if any(word in field.lower() for word in BANNED):
            sys.exit("Field '%s' looks like third-party text. Refusing to write." % field)

    wide_rows, long_rows = [], []
    for record in labels:
        report_id = record["id"]
        info = meta.get(report_id, {})
        row = {
            "report_id": report_id,
            "posted_date": info.get("date", ""),
            "word_count": info.get("word_count", ""),
            "is_wdw_trip": as_bool(record.get("is_wdw_trip")),
            "trip_year": record.get("trip_year") or "",
            "trip_month": record.get("trip_month") or "",
            "persona": record.get("persona", ""),
            "verdict": record.get("verdict", ""),
            "peak_stage": record.get("peak_stage", ""),
        }
        for fact in PARTY_FACTS:
            row[fact] = as_bool(record.get(fact))
        stages = record.get("stages", {})
        for key in STAGE_KEYS:
            row[key] = stages.get(key, {}).get("rating", "not_mentioned")
        wide_rows.append(row)

        for order, key in enumerate(STAGE_KEYS, 1):
            rating = stages.get(key, {}).get("rating", "not_mentioned")
            if rating == "not_mentioned":
                continue
            long_rows.append({
                "report_id": report_id,
                "posted_date": row["posted_date"],
                "trip_year": row["trip_year"],
                "trip_month": row["trip_month"],
                "persona": row["persona"],
                "is_wdw_trip": row["is_wdw_trip"],
                "stage_order": order,
                "stage_key": key,
                "stage": STAGE_NAMES[key],
                "rating": rating,
                "verdict": row["verdict"],
                "is_peak": int(record.get("peak_stage") == key),
            })

    for name, rows, fields in (
        ("wdw_journey_labels_wide.csv", wide_rows, wide_fields),
        ("wdw_journey_labels_long.csv", long_rows, long_fields),
    ):
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print("wrote %s  (%d rows)" % (path, len(rows)))

    # Final check: scan the written files for anything long enough to be prose.
    for name in os.listdir(OUT_DIR):
        with open(os.path.join(OUT_DIR, name), "r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                for cell in next(csv.reader([line])):
                    if len(cell) > 40:
                        sys.exit("Long value in %s line %d: %r\nRefusing to publish."
                                 % (name, line_no, cell[:60]))
    print("\nChecked: no quotes, titles, post text or usernames in the output.")
    print("Reddit post ids are included so labels can be verified at")
    print("https://www.reddit.com/comments/<report_id>")
    print("\nUpload only the files in %s/ to Kaggle." % OUT_DIR)


if __name__ == "__main__":
    main()
