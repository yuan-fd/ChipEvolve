# P22 reference workspace and bilingual handoff

- Frontend and Backend intentionally follow the user's ICCAD demo reference:
  centered narrow column, mode pills, numbered panel headers, compact borders,
  vertical action/result order, and no unrelated dashboard blocks in the main
  path.
- Frontend: source input → audited examples → real synthesis outputs → optional
  RTLScout/EDACraft.
- Backend: registered design → implementation configuration → baseline run or
  Campaign plan → six vertical stages → layout/QoR/artifacts → optional tools.
- `flowMode=baseline` queues one run. `campaign` and `agent` create three
  unbound candidates only. They do not execute from the Web process.
- Header language switch persists `zh/en` in localStorage. Query override
  `?lang=zh|en` exists for review. Technical identifiers stay unchanged.
- Runtime stage completion, layout, QoR cards, and artifacts use registered
  evidence; no UI sample metrics are invented.
- Full regression: 196 passed. Local review server remains on port 8000.
- User-owned untracked `plan/` files remain untouched.
