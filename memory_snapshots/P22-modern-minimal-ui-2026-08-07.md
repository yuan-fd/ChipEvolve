# P22 modern-minimal UI memory snapshot

The user prefers a modern minimal efficiency-tool interface over the prior
editorial/art-directed style. Preserve the six-page information architecture and
vertical task flows, but use neutral white/light-gray surfaces, one blue action
color, system sans-serif typography, compact spacing, small radii, clear 1 px
borders, and no decorative shadow on ordinary content.

Reference screenshots are guidance for clarity only. Do not copy their toy-like
button density, tiny type, excessive micro-cards, or Chinese demo layout. The
platform should feel professional and utilitarian: task title, short explanation,
controls, primary action, status, and results in reading order.

The visual change is concentrated in `apps/web/assets/app.css`; API, runtime,
design, learning, and extension behavior stay unchanged. DPLEvolve dashboard is
hidden by default and shown only when the user selects that extension.

Visual review covered all six pages at 1440 px and Frontend at 390 px. Continue
to treat simple hierarchy and immediate operability as more important than
artistic styling.
