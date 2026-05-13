# Prospecting Runbook

Run these commands from the repo root:

```bash
cd /Users/servandodavidtorresgarcia/servando/controlthrive/oss/ct-search
```

## Where The Query Goes

Edit this file:

```text
prospecting_lists.example.json
```

That is the file name. It lives in the `ct-search` repo root.

The command does not take a separate natural-language query. The config file is the query.

Example list config:

```json
{
  "name": "placement_agents_europe_london",
  "display_name": "Placement agents in Europe including London",
  "geography": "Europe, explicitly including London and the United Kingdom",
  "target_count": 20,
  "candidate_pool": 60,
  "max_headcount": 10,
  "require_contact_email": true,
  "require_contact_linkedin": true
}
```

## Setup

Put your key in `.env` in this repo root:

```env
PARALLEL_API_KEY=your_parallel_key_here
DRY_RUN=true
```

## First Run

Start tiny:

```bash
uv run prospect-engine prospecting-review \
  --config prospecting_lists.example.json \
  --max-reviewed-per-list 1
```

This researches one candidate per list and writes files to:

```text
exports/prospecting_reviews/
```

## Check Results

Open:

```text
exports/prospecting_reviews/placement_agents_europe_london_review.csv
exports/prospecting_reviews/placement_agents_ny_review.csv
```

Look for:

```text
qualified = True
primary_contact_email is filled
primary_contact_linkedin is filled
headcount evidence makes sense
placement-agent evidence makes sense
```

## Continue Safely

Small batch:

```bash
uv run prospect-engine prospecting-review \
  --config prospecting_lists.example.json \
  --max-reviewed-per-list 5 \
  --resume
```

Larger paid batch:

```bash
uv run prospect-engine prospecting-review \
  --config prospecting_lists.example.json \
  --generator core \
  --max-reviewed-per-list 25 \
  --resume \
  --confirm-paid-run
```

## Important

Do not start with `--generator core`.

Do not delete `exports/prospecting_reviews/` unless you intentionally want to lose checkpoints.

The workflow saves:

```text
*_candidates.json
*_review_partial.jsonl
*_review.json
*_review.csv
```

If anything fails, rerun with `--resume`.
