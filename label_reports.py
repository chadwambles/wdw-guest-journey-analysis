"""
Label the trip reports with Claude: persona, stages, bad/neutral/good per
stage, verdict, peak stage, and a short evidence quote for every label.

Four commands, run in this order:

    py label_reports.py test            label 10 random reports right away,
                                        print them, and estimate the full cost
    py label_reports.py submit          send all reports as one Message Batch
                                        (50% cheaper, finishes within 24 hours,
                                        usually much sooner)
    py label_reports.py status          check whether the batch is done
    py label_reports.py fetch           download results and write the CSVs

Reads:  trip_reports.jsonl        (from extract_trip_reports.py)
Writes: labels_test.jsonl         test run output
        labels.jsonl              every label plus evidence quotes
        labels_long.csv           one row per report per stage, for Tableau
        labels_wide.csv           one row per report
        batch_id.txt              so status and fetch know which batch

The API key is read from the ANTHROPIC_API_KEY environment variable. Never
put it in this file.

Evidence quotes are other people's words. They stay on your machine: strip
the evidence columns before publishing anything to Kaggle.

Requirements:
    py -m pip install anthropic
"""

import argparse
import csv
import json
import os
import random
import sys

try:
    import anthropic
except ImportError:
    sys.exit("Install the SDK first:  py -m pip install anthropic")

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1500
SOURCE = "trip_reports.jsonl"
BATCH_FILE = "batch_id.txt"

# Standard rates per million tokens, input and output. Batch is half.
# Check platform.claude.com/docs/en/about-claude/pricing if these change.
PRICE_IN, PRICE_OUT = 2.00, 10.00

STAGES = [
    ("planning_booking", "Planning and booking",
     "booking the trip, park tickets, dining reservations, itinerary planning. "
     "Lightning Lane purchases belong to lightning_lane_app, not here"),
    ("getting_there", "Getting there",
     "flights, the airport, transfers to the resort, driving down"),
    ("resort_stay", "Resort stay",
     "the room, check-in, pools, resort grounds"),
    ("transportation", "Transportation around WDW",
     "buses, Skyliner, monorail, boats, parking. Airport travel belongs to "
     "getting_there or departure"),
    ("park_entry_crowds", "Park entry and crowds",
     "getting in, crowd levels, heat and weather as they affected the day"),
    ("attractions", "Attractions",
     "rides and their standby waits"),
    ("lightning_lane_app", "Lightning Lane and the app",
     "buying or booking Lightning Lane (Multi Pass, Single Pass, Premier Pass) "
     "before or during the trip, virtual queues, the My Disney Experience app"),
    ("dining", "Dining",
     "table service, quick service, snacks, dining plans"),
    ("entertainment_characters", "Entertainment and characters",
     "fireworks, parades, shows, parties, character meet-and-greets and any "
     "interaction with a costumed character"),
    ("cast_members", "Cast members",
     "staff interactions: ride operators, servers, resort and guest services "
     "staff. Costumed characters are never cast_members, even though Disney "
     "calls its performers cast members. Chewbacca, Mickey, princesses, or a "
     "performer playing Russell all belong to entertainment_characters"),
    ("departure", "Departure",
     "last day, checkout, getting home"),
]
STAGE_KEYS = [s[0] for s in STAGES]
PERSONAS = ["passholder_local", "multigenerational", "families_with_kids",
            "couples_adults", "unassigned"]

SYSTEM_PROMPT = """You label Walt Disney World trip reports for a research study of the guest journey. You record labels with the record_labels tool and nothing else.

STAGES. Rate a stage only when the author says how it went. A stage that is only named or described as logistics, with no judgment ("Hotel: Swan", "took the bus at 6:30", "didn't get to try the Skyliner"), is not_mentioned.
%s

RATINGS for each discussed stage:
- good: clearly positive, met or beat expectations
- neutral: neither clearly positive nor clearly negative, including mixed experiences where good and bad roughly balance
- bad: clearly negative, frustrating, disappointing
For mixed passages ("the line was insane but worth it"), follow the author's overall feeling about that stage, not individual complaints. The quote you choose must support the rating you give: a clearly positive quote cannot back a bad or neutral rating. If you rate a stage neutral because feelings are mixed, quote the sentence that shows the mix.

THE TRAVEL PARTY. Do not choose a traveler category. Record facts about who traveled; the category is assigned afterward from these facts.
- party_known: true if the text makes clear who was on the trip.
- has_child_under_18: true if anyone in the party is under 18, false if the text makes clear no one is, null if unclear. Check stated ages: a son or daughter aged 18 or older is not a child for this field, even when called "my daughter" or "the kids".
- has_adult_child_with_parent: true if anyone aged 18 or older is traveling with their own parent. This includes an adult author traveling with their own parents ("My parents and siblings planned a week long trip") and a parent traveling with a grown son or daughter ("Me (F59) & T (F27 Daughter)"). Assume the author is an adult unless the text says otherwise.
- has_three_generations: true if grandparents, their children and their grandchildren are all in the party.
- is_local_visit: true if the party lives within day-trip distance and visits without an overnight vacation stay, such as locals or annual passholders popping in. Tourists staying off site are not local visits, and neither are locals who book a resort vacation.
- party_evidence: a short quote describing who traveled.

ALSO RECORD
- is_wdw_trip: false if the trip was mainly to a different resort (Disneyland, Tokyo, Paris, a cruise, Universal only).
- trip_year and trip_month: when the trip happened, if stated or clearly implied. This is the trip date, not the posting date, which is given at the top of the report. If the text gives a month but no year, use the most recent occurrence of that month on or before the posting date. Never return a date after the posting date. Use null only if there is no clue at all.
- verdict: the author's explicit overall judgment of the whole trip, usually near the end. A remark about writing the report ("It always helps me to write these recaps"), or praise for a single moment, is not a verdict. Use none if there is no judgment of the trip as a whole.
- peak_stage: the stage the author describes most positively, or none.

EVIDENCE. For every label other than not_mentioned and none, give a short quote from the report, 20 words or fewer, that best supports it. Each quote must be one continuous excerpt copied exactly, never pieces joined with an ellipsis. Do not reuse the same quote for more than one label: if one sentence is the only evidence for two stages, give it to the stage it fits best and mark the other not_mentioned. Use an empty string when there is nothing to quote.""" % "\n".join(
    "- %s (%s): %s" % (key, name, desc) for key, name, desc in STAGES
)

RATED = {"type": "string", "enum": ["good", "neutral", "bad", "not_mentioned"]}
EVIDENCE = {"type": "string", "description": "Exact quote, 20 words or fewer, or empty"}

TOOL = {
    "name": "record_labels",
    "description": "Record the labels for one trip report.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_wdw_trip": {"type": "boolean"},
            "trip_year": {"type": ["integer", "null"]},
            "trip_month": {"type": ["integer", "null"], "minimum": 1, "maximum": 12},
            "party_known": {"type": "boolean"},
            "has_child_under_18": {"type": ["boolean", "null"]},
            "has_adult_child_with_parent": {"type": "boolean"},
            "has_three_generations": {"type": "boolean"},
            "is_local_visit": {"type": "boolean"},
            "party_evidence": EVIDENCE,
            "stages": {
                "type": "object",
                "properties": {
                    key: {
                        "type": "object",
                        "properties": {"rating": RATED, "evidence": EVIDENCE},
                        "required": ["rating", "evidence"],
                    }
                    for key in STAGE_KEYS
                },
                "required": STAGE_KEYS,
            },
            "verdict": {"type": "string", "enum": ["good", "neutral", "bad", "none"]},
            "verdict_evidence": EVIDENCE,
            "peak_stage": {"type": "string", "enum": STAGE_KEYS + ["none"]},
        },
        "required": ["is_wdw_trip", "trip_year", "trip_month", "party_known",
                     "has_child_under_18", "has_adult_child_with_parent",
                     "has_three_generations", "is_local_visit",
                     "party_evidence", "stages", "verdict",
                     "verdict_evidence", "peak_stage"],
    },
}


# ---------------------------------------------------------------- helpers


def load_reports():
    if not os.path.exists(SOURCE):
        sys.exit("Can't find %s. Run extract_trip_reports.py first." % SOURCE)
    reports = []
    with open(SOURCE, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                reports.append(json.loads(line))
    return reports


def request_params(report):
    body = "Posted: %s\nTitle: %s\n\n%s" % (
        report.get("date", "unknown"), report.get("title", ""), report.get("selftext", ""))
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "tools": [TOOL],
        "tool_choice": {"type": "tool", "name": "record_labels"},
        "messages": [{"role": "user", "content": body}],
    }


def clean_quote(text):
    """Strip stray wrapping quote marks the model sometimes adds."""
    pairs = {'"': '"', "'": "'", "\u201c": "\u201d"}
    text = (text or "").strip()
    while len(text) >= 2 and pairs.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    return "" if text in ('""', "''") else text


PARTY_FACTS = ["party_known", "has_child_under_18", "has_adult_child_with_parent",
               "has_three_generations", "is_local_visit"]


def normalize_stages(stages):
    """
    Force the stages field into the expected shape. Models occasionally return
    it as a JSON string, or return a stage as a bare rating instead of an
    object, so every variation is coerced rather than crashing the fetch.
    """
    if isinstance(stages, str):
        try:
            stages = json.loads(stages)
        except json.JSONDecodeError:
            stages = {}
    if not isinstance(stages, dict):
        stages = {}

    clean = {}
    for key in STAGE_KEYS:
        entry = stages.get(key)
        if isinstance(entry, str):
            try:
                entry = json.loads(entry)
            except json.JSONDecodeError:
                entry = {"rating": entry, "evidence": ""}
        if not isinstance(entry, dict):
            entry = {}
        rating = entry.get("rating", "not_mentioned")
        if rating not in ("good", "neutral", "bad", "not_mentioned"):
            rating = "not_mentioned"
        clean[key] = {"rating": rating, "evidence": clean_quote(entry.get("evidence", ""))}
    return clean


def derive_persona(labels):
    """
    Apply the persona precedence rules to the extracted facts. Doing this in
    code rather than asking the model makes the assignment auditable, and it
    stops the model pattern-matching "my daughter" to families with kids.
    """
    if labels.get("is_local_visit"):
        return "passholder_local"
    if labels.get("has_three_generations") or labels.get("has_adult_child_with_parent"):
        return "multigenerational"
    if labels.get("has_child_under_18") is True:
        return "families_with_kids"
    if labels.get("party_known") and labels.get("has_child_under_18") is False:
        return "couples_adults"
    return "unassigned"


def extract_labels(message):
    """Pull the tool input out of a response, and fill any gaps safely."""
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_labels":
            labels = dict(block.input)
            for field in ("party_evidence", "verdict_evidence"):
                labels[field] = clean_quote(labels.get(field, ""))
            labels["persona"] = derive_persona(labels)
            labels["stages"] = normalize_stages(labels.get("stages"))
            return labels
    return None


def client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(
            "ANTHROPIC_API_KEY isn't set. Run  setx ANTHROPIC_API_KEY \"your-key\"\n"
            "then close and reopen PowerShell."
        )
    return anthropic.Anthropic()


def print_report(report, labels):
    print("\n" + "=" * 72)
    print("%s  |  %s  |  %d words" % (report["id"], report.get("date", ""), report.get("word_count", 0)))
    print(report.get("title", "")[:72])
    print("-" * 72)
    if labels is None:
        print("  NO LABELS RETURNED")
        return
    print("  wdw trip: %s   trip date: %s-%s" % (
        labels["is_wdw_trip"], labels.get("trip_year"), labels.get("trip_month")))
    facts = [f.replace("has_", "").replace("is_", "") for f in PARTY_FACTS[1:] if labels.get(f)]
    print("  persona:  %-20s \"%s\"" % (labels["persona"], labels.get("party_evidence", "")))
    print("            facts: %s%s" % (", ".join(facts) or "none",
          "" if labels.get("party_known") else "  (party unclear)"))
    for key in STAGE_KEYS:
        stage = labels["stages"][key]
        if stage["rating"] != "not_mentioned":
            print("  %-26s %-8s \"%s\"" % (key, stage["rating"], stage["evidence"]))
    print("  verdict:  %-8s \"%s\"" % (labels["verdict"], labels.get("verdict_evidence", "")))
    print("  peak:     %s" % labels["peak_stage"])


# ----------------------------------------------------------------- output


def write_outputs(reports_by_id, labeled, errors):
    with open("labels.jsonl", "w", encoding="utf-8") as fh:
        for report_id, labels in labeled.items():
            fh.write(json.dumps({"id": report_id, **labels}, ensure_ascii=False) + "\n")

    base = ["id", "posted_date", "word_count", "is_wdw_trip", "trip_year",
            "trip_month", "persona"] + PARTY_FACTS + ["verdict", "peak_stage"]

    with open("labels_wide.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(base + STAGE_KEYS)
        for report_id, labels in labeled.items():
            report = reports_by_id.get(report_id, {})
            writer.writerow(
                [report_id, report.get("date", ""), report.get("word_count", ""),
                 labels["is_wdw_trip"], labels.get("trip_year") or "",
                 labels.get("trip_month") or "", labels["persona"]]
                + [labels.get(f) for f in PARTY_FACTS]
                + [labels["verdict"], labels["peak_stage"]]
                + [labels["stages"][k]["rating"] for k in STAGE_KEYS]
            )

    names = {key: name for key, name, _ in STAGES}
    with open("labels_long.csv", "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "posted_date", "trip_year", "trip_month", "persona",
                         "is_wdw_trip", "stage_order", "stage_key", "stage",
                         "rating", "verdict", "is_peak", "evidence"])
        for report_id, labels in labeled.items():
            report = reports_by_id.get(report_id, {})
            for order, key in enumerate(STAGE_KEYS, 1):
                stage = labels["stages"][key]
                if stage["rating"] == "not_mentioned":
                    continue
                writer.writerow([
                    report_id, report.get("date", ""), labels.get("trip_year") or "",
                    labels.get("trip_month") or "", labels["persona"],
                    labels["is_wdw_trip"], order, key, names[key], stage["rating"],
                    labels["verdict"], int(labels["peak_stage"] == key),
                    stage["evidence"],
                ])

    print("\nWrote labels.jsonl, labels_wide.csv, labels_long.csv  (%d reports)" % len(labeled))
    if errors:
        print("%d reports failed:" % len(errors))
        for report_id, reason in errors[:20]:
            print("  %s  %s" % (report_id, reason))
        if len(errors) > 20:
            print("  ...and %d more" % (len(errors) - 20))


# --------------------------------------------------------------- commands


def cmd_test(args):
    reports = load_reports()
    random.seed(args.seed)
    sample = random.sample(reports, min(args.n, len(reports)))
    api = client()

    tokens_in = tokens_out = 0
    results = []
    for report in sample:
        try:
            message = api.messages.create(**request_params(report))
        except anthropic.APIError as exc:
            print("\n%s failed: %s" % (report["id"], exc))
            continue
        tokens_in += message.usage.input_tokens
        tokens_out += message.usage.output_tokens
        labels = extract_labels(message)
        print_report(report, labels)
        if labels:
            results.append({"id": report["id"], **labels})

    with open("labels_test.jsonl", "w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    done = max(len(results), 1)
    cost_test = tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT
    per_report = cost_test / done
    full = per_report * len(reports) * 0.5
    print("\n" + "=" * 72)
    print("Labeled %d of %d sampled reports. Wrote labels_test.jsonl" % (len(results), len(sample)))
    print("This test cost about $%.3f" % cost_test)
    print("Full run of %d reports as a batch: about $%.2f" % (len(reports), full))
    print("\nRead the evidence quotes above. If the labels look right, run:")
    print("  py label_reports.py submit")


def cmd_submit(args):
    if os.path.exists(BATCH_FILE) and not args.force:
        sys.exit("%s already exists. Use status/fetch, or submit --force to start over." % BATCH_FILE)
    reports = load_reports()
    ids = [r["id"] for r in reports]
    if len(set(ids)) != len(ids):
        sys.exit("Duplicate report ids in %s; batch custom_ids must be unique." % SOURCE)

    requests = [{"custom_id": r["id"], "params": request_params(r)} for r in reports]
    batch = client().messages.batches.create(requests=requests)
    with open(BATCH_FILE, "w") as fh:
        fh.write(batch.id)
    print("Submitted %d reports as batch %s" % (len(requests), batch.id))
    print("Check progress with:  py label_reports.py status")


def read_batch_id():
    if not os.path.exists(BATCH_FILE):
        sys.exit("No %s. Run submit first." % BATCH_FILE)
    with open(BATCH_FILE) as fh:
        return fh.read().strip()


def cmd_status(args):
    batch = client().messages.batches.retrieve(read_batch_id())
    counts = batch.request_counts
    print("Batch %s: %s" % (batch.id, batch.processing_status))
    print("  processing %d   succeeded %d   errored %d   canceled %d   expired %d" % (
        counts.processing, counts.succeeded, counts.errored, counts.canceled, counts.expired))
    if batch.processing_status == "ended":
        print("Done. Run:  py label_reports.py fetch")


def cmd_fetch(args):
    api = client()
    batch_id = read_batch_id()
    batch = api.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        sys.exit("Batch is still %s. Try again later." % batch.processing_status)

    reports_by_id = {r["id"]: r for r in load_reports()}
    labeled, errors = {}, []
    for entry in api.messages.batches.results(batch_id):
        kind = entry.result.type
        if kind != "succeeded":
            errors.append((entry.custom_id, kind))
            continue
        labels = extract_labels(entry.result.message)
        if labels is None:
            errors.append((entry.custom_id, "no tool output"))
        else:
            labeled[entry.custom_id] = labels
    write_outputs(reports_by_id, labeled, errors)


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    test = sub.add_parser("test", help="label a small random sample now")
    test.add_argument("--n", type=int, default=10)
    test.add_argument("--seed", type=int, default=1)
    test.set_defaults(func=cmd_test)

    submit = sub.add_parser("submit", help="submit every report as a batch")
    submit.add_argument("--force", action="store_true")
    submit.set_defaults(func=cmd_submit)

    sub.add_parser("status", help="check batch progress").set_defaults(func=cmd_status)
    sub.add_parser("fetch", help="download results and write CSVs").set_defaults(func=cmd_fetch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
