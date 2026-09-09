# Human research usefulness feedback

Inspired by the human feedback stage of Agent Laboratory, this local extension
lets a researcher assess a lane without editing its scientific result, claim
ledger or frozen package. No model call or follow-up experiment is triggered.

```python
from lab.research_review import record_review, load_reviews, build_reviewed_report

# The store must be a JSON file outside the run folder.
record_review("feedback/reviews.json", run_folder, "producer-a",
              "needs_work", "Check the endpoint assumption", "Principal", 4.5)
reviews = load_reviews("feedback/reviews.json", run_folder)
report = build_reviewed_report(run_folder, "feedback/reviews.json")
```

Decisions are `useful`, `not_useful`, or `needs_work`. Notes and the reviewer label
are self-reported; the label does not authenticate the reviewer. Feedback does not
certify mathematical truth, change a claim to PROVED, or authorize another run.

Each event records the exact byte SHA-256 of TASK_CONTRACT.json and the declared
lane's RESULT.json. A newer review appends an event rather than changing an older
event. The latest event per run folder/lane is effective. If either source changes
or disappears, it becomes stale and is excluded from current metrics. A new review
of the changed files restores current status. Moving a run to another folder does
not silently apply the original review to it.

`load_reviews` returns a mapping keyed by lane ID. Event fields include `decision`,
`note`, `reviewer`, `review_minutes`, source hashes, `current`, and `stale`.
`build_reviewed_report` adds `human_review` and `review_status` to each lane.
Model/effort metrics add `reviewed_count`, `useful_count`, `useful_rate`,
`review_minutes`, and `cost_per_useful_usd`.

The usefulness rate denominator is currently reviewed lanes, including needs_work.
The review-minute total covers their latest effective review events, not historical
review labor. Cost per useful result includes the entire model/effort group's
reported cost, including failed and unreviewed work; it is null if any lane cost
is incomplete or no result was reviewed useful. Completion alone is not usefulness.

Writes use a process-local lock plus an exclusive `.lock` file and atomic file
replacement. Concurrent processes fail clearly instead of silently losing events.
If a writer is forcibly terminated, an operator must confirm it stopped before
removing its leftover lock. The application journal is append-only by convention;
it is not tamper-proof against someone editing local files. Malformed stores fail
closed rather than being overwritten. No provider credentials are read.
