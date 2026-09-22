# Walt Disney World Guest Journey, 2024-2026

Where does a Disney World trip actually go wrong?

Across 604 guest trip reports, every stage of the trip scores positively except one. Park entry and crowds is the only negative stage, and departure is a distant second. Attractions, dining, resorts and cast members all score strongly.

This repository holds the pipeline that produced that: scraping, extraction, labeling, and profiling.

**Labeled dataset on Kaggle:** _[[link](https://www.kaggle.com/datasets/chadwambles/disney-world-guest-journey-labels/data)]_
**Write-up:** _[link]_

---

## What it does

Takes two years of Reddit trip reports and turns them into a guest journey map: each trip broken into up to 11 stages, each stage rated bad, neutral or good, each report tagged with who traveled and how they judged the trip overall.

**Window:** July 2024 to September 2026, starting when Lightning Lane Multi Pass replaced Genie+, so the whole dataset sits under one set of park rules.

**Scale:** 46,855 subreddit posts, 997 flaired Trip Report, 613 with enough text to label, 604 in scope after filtering.

**The 11 stages:** planning and booking, getting there, resort stay, transportation around WDW, park entry and crowds, attractions, Lightning Lane and the app, dining, entertainment and characters, cast members, departure.

---

## Findings

Net score means the share of good ratings minus the share of bad.

| Stage | Net | Reports |
|---|---|---|
| Attractions | +64.5 | 496 |
| Resort stay | +63.0 | 319 |
| Entertainment and characters | +61.0 | 390 |
| Cast members | +57.7 | 338 |
| Dining | +51.8 | 415 |
| Planning and booking | +41.9 | 229 |
| Lightning Lane and the app | +33.3 | 324 |
| Transportation around WDW | +29.7 | 306 |
| Getting there | +15.6 | 135 |
| Departure | +5.1 | 118 |
| **Park entry and crowds** | **-5.9** | **353** |

Crowds are the one part of the trip guests consistently don't enjoy, and the one Disney has least control over.

Personas in scope: families with kids 219, couples and adults 176, multigenerational 65, passholders and locals 26, unassigned 118.

---

## Method

**Source.** Posts flaired Trip Report in r/WaltDisneyWorld, retrieved through the Arctic Shift Reddit archive.

**Labeling.** Each report goes to Claude Sonnet 5 through the Anthropic Batch API with a fixed tool schema, so every response has to use the same 11 stages and the same three ratings. Nothing free-form comes back.

**Personas are derived, not chosen.** The model kept pattern-matching "my daughter" to families with kids even when the daughter was 27. So it no longer picks a category. It extracts five facts about the travel party, and the code applies the precedence rules:

1. Local visit → passholder or local
2. Three generations, or an adult child with a parent → multigenerational
3. Anyone under 18 → families with kids
4. Party known, no children → couples and adults
5. Otherwise → unassigned

That makes persona assignment auditable and repeatable, and the underlying facts ship with the data so anyone can reclassify under their own rules.

**Prompt development.** Several rounds against manually reviewed samples. Fixes included: bare mentions ("Hotel: Swan") no longer count as neutral experiences, costumed characters are separated from cast members, Lightning Lane purchases sit in one stage instead of two, and the posting date is passed in so trip years can be inferred.

---

## Requirements

```
Python 3.9+
pip install requests beautifulsoup4 anthropic
```

An Anthropic API key in the `ANTHROPIC_API_KEY` environment variable, for the labeling step only. The full run cost about $6 through the Batch API.

---

## Reproducing

Download posts from the Arctic Shift download tool for r/WaltDisneyWorld, posts only, July 2024 onward, into this folder. Then:

```bash
python merge_posts.py              # combine downloads, dedupe, check coverage
python extract_trip_reports.py     # pull Trip Report posts with usable text
python label_reports.py test       # label 10 at random, check the output
python label_reports.py submit     # send all reports as one batch
python label_reports.py status     # check progress
python label_reports.py fetch      # download results, write CSVs
python profile_labels.py           # filter to scope, profile coverage
python build_kaggle_dataset.py     # build the publishable files
```

The intermediate files contain other people's writing and are gitignored. Only `build_kaggle_dataset.py` output is safe to publish: it strips quotes, titles, post text and usernames, and aborts if anything long enough to be prose survives into the output.

---

## Limitations

- **Self-selection.** People who write trip reports are enthusiasts. 82% of verdicts are good. Compare stages against each other, not against an absolute scale.
- **Model labels.** Validated against hand-reviewed samples, not human-coded. Labels are not perfectly deterministic, so small gaps between groups are noise.
- **Uneven coverage.** Attractions appears in 496 reports, departure in 118. The passholder persona has only 26 reports and can't support stage-level conclusions.
- **Ordinal ratings.** Bad, neutral and good have an order but no fixed distance. Use distributions or net scores, not means.
- **Three missing days** where download ranges didn't meet, about 0.4% of the period.

---

## Attribution

Source posts from r/WaltDisneyWorld via the Arctic Shift archive. Labels generated with Claude Sonnet 5 (Anthropic). No post text, titles or usernames are stored in this repository or the published dataset.

Analysis by Chad Wambles, 2026.
#
