# 2026-09-23: run experiment identity fields (348 -> 359)

Run summaries now expose optional design revision, experiment and platform
computed input manifest identity fields while preserving the existing response
shape for older runs. Ceiling: 359 lines.
# 2026-09-24: complete run counts for paginated design history (359 -> 370)

The provenance read model now counts every matching run independently of the
presentation page size, allowing the gateway to report an accurate design
history total while still returning bounded pages.
