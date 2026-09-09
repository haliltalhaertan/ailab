# Read-only research delivery summaries

`lab.directed_reports.build_research_report(run_folder)` reads the frozen task contract,
current runtime snapshot and declared lane `RESULT.json` files. It does not start a
model, read credentials, rewrite historical evidence or change a package hash.
`render_research_report(report)` returns Markdown suitable for a separate download.

Each lane summary provides the assigned role, a bounded final-result excerpt,
reported evidence references, the first missing step and explicit unreviewed
scientific usefulness. Full results remain in the original artifacts. Claim statuses
such as `PROVED` do not automatically become a useful-result score or independent
verification in this report.

Model/effort metrics separate scheduled lanes from attempted lanes. The answer
completion rate counts nonempty `COMPLETED` final answers divided by attempted
lanes; blocked dependencies are excluded from that denominator. Native checks are
excluded from model metrics. A missing duration is not a measured zero; the report
includes `lanes_with_duration` and a sum of observed lane durations, which is not
wall-clock run duration. No cross-task difficulty adjustment is implied.

Known provider charges are separated from uncertainty. Missing usage, incomplete
retry histories and invalid numeric costs cannot establish an exact zero charge.
Reservation bounds are budget estimates, not provider billing guarantees. Scientific
usefulness counts and rates remain null until an independently attributable human
review workflow is added; no model can grade itself by emitting `PROVED` or `useful`.
