# Variant G — Pass 2.7 (2026-07-28, Andrew feedback on pass 2.6)

Two changes: **multi-anchor day rows in the Days ledger** + **playlist-header coverage fix**.

## Bug fix (Andrew screenshot, 2026-07-28): partial playlist context stole the session header
The merged Bill Evans session (Jul 23, 8 tracks) showed a whole-session "📃 2020s Mix ·
8 trk" header because ANY named context triggered the playlist layout — but the context
covered only 3 of 8 plays. **Fix:** whole-session 📃 header requires the top context to
cover ≥80% of plays. Below that, the expansion uses normal album-run grouping (💿 headers)
plus a dim one-line note: `📃 2020s Mix · 3 of 8 plays`. Card-title logic (≥50% for the
collapsed 📃 title) unchanged. Real-build note: with per-track context capture (DIDL /
poller fix), the note can become a per-run 📃 header on exactly the playlist tracks.

## Problem
Pass 2.6 Days ledger gives each day exactly one row, headlined by the single anchor
session. A day with two+ significant but very different sessions (e.g. 2h classical
in the morning, 2h workout music in the afternoon) collapses into one headline and
the second chapter is invisible.

## Design (locked)

### Anchor qualification
A session qualifies as an **anchor** if it has ≥8 unique tracks OR ≥45 min duration.
(Same threshold family as the 3-month analysis: 88% of days have ≥1 anchor.)

### Multi-row days
- If a day has **2 or 3 anchors**, the day gets one ledger row **per anchor**.
- If a day has **4+ anchors**, show the top 3 by duration, and fold the rest into the
  last row's residual count.
- Days with 0–1 anchors keep the single-row format from pass 2.6 (unchanged).

### Row format for multi-anchor days
- **First row of a day** carries the date cell (e.g. `Thu Jul 17`) plus that anchor's
  thumbs + headline + its own stats: `💿 Skinshape · morning · 2h 10m · 31 tracks`.
- **Continuation rows** leave the date cell blank (or a subtle `〃`/indent) so the day
  reads as one visual group — thumbs + headline + per-anchor stats.
- **Day totals + residual** (`· 5 sessions · ▫4` etc.) move to the LAST row of the
  day group, right-aligned as before, so day-level totals still exist exactly once.
- Time-of-day word (morning/afternoon/evening/night) appears in each anchor headline
  since that's the differentiator Andrew cares about.
- Visual grouping: continuation rows get no top border (hairline only between days),
  slightly tighter padding, so a 2-anchor day reads as one 2-line block.

### Click behavior
- Clicking any anchor row jumps to Sessions zoom scrolled to **that session's card**
  (not just the day header). Day-level jump from the date cell still lands on the day.

### Unchanged
Everything else from pass 2.6: per-card panels w/ memory, carets, rail layout,
760px tracklist cap, 5-col grid cap, phone behavior, Sessions zoom, quick-plays
ledger, A1/A2 suppression.

## Testing checklist
1. Find a real multi-anchor day in the dataset; verify it renders 2–3 rows grouped.
2. Verify single-anchor days unchanged (one row, same as 2.6).
3. Verify day totals appear exactly once per day (last row).
4. Click a second-anchor row → Sessions zoom scrolls to that specific session card.
5. Date cell click → day header jump still works.
6. Week header sums unchanged.
7. Phone width 390px: ledger rows still readable (headline truncates with ellipsis).
