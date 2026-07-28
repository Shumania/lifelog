# Variant G — Pass 2.6 (2026-07-28, Andrew feedback on pass 2.5)

Builds on pass 2.5 (inline full-width panel + dense grid). Four changes.

## 1. Multi-expand with per-card panels (replaces single shared panel)

- Remove the "one shared panel" model. Each expanded session card gets **its own**
  full-width detail panel, inserted after that card's grid row (same `grid-column: 1 / -1`
  technique, same caret).
- **Any number of cards can be expanded at once.** Clicking a card toggles ONLY that
  card's panel; other open panels are untouched.
- Two+ expanded cards in the same grid row → their panels stack below that row in
  card order (left→right), each caret horizontally aligned to its own card
  (existing caret math, per panel).
- **Expanded state is remembered** in a JS Set keyed by session id. It must survive:
  re-render on window resize (column count change), Sessions↔Days zoom toggles, and
  day-jump navigation from Days view. (In-memory only; page reload may reset.)
- Card selected-state styling (teal ring) applies to every expanded card.
- Phone/narrow (<900px single column): unchanged accordion-style inline expansion,
  but now also multi-expand (opening one does NOT close another) with the same
  remembered-state Set. Same code path where practical.
- Panel close: ✕ button or re-click of its card. No "close all" needed.

## 2. Wide-panel internal layout (fix ultrawide stretch)

Problem: at large widths the expanded panel content stretched edge-to-edge; track
title sat very far from artist + ♥/🔍/▶/＋ buttons.

- Panel inner layout at panel width ≥ ~1000px becomes **two regions**:
  - **Left rail (~280px, sticky within panel):** art fan/cover, session title
    ("led by The Meters"), meta line (time · duration · N tracks · artists count),
    playlist name if any, exploration link.
  - **Right region: tracklist with `max-width: 760px`,** left-aligned next to rail.
    Track rows keep the current layout (time · title · album dim · artist · ♥ 🔍 ▶ ＋)
    but now at a readable measure — artist + buttons never more than ~760px from title.
  - Leftover space on ultrawides stays empty (breathing room), no stretching.
- Below ~1000px panel width: current stacked layout (header above tracklist,
  tracklist un-capped at these sizes since width is already narrow).
- Album-run headers (💿, collapsible) live inside the tracklist region, same as now.

## 3. Dense Days ledger (replaces rich day cards)

Days zoom is now a **scan-density-first ledger**, meant as the fast index into Sessions:

- One row per day, ~44–48px tall, single-column list, `max-width: 920px`, centered.
- Row anatomy (left→right):
  - date label (`Mon Jul 27`, fixed-width column; today/yesterday styling optional)
  - mini art strip: up to 4 × 24px rounded thumbs (top albums of the day by play count)
  - headline: anchor session summary, one line, truncated
    (`💿 Thimar — Anouar Brahem` or `led by The Meters` or `📻 KEXP morning`)
  - right-aligned stats: `4h 44m · 53 tracks · 5 sessions` (+ `· ▫3` if quick plays)
  - chevron `›`
- Group rows under **week headers**: `Week of Jul 21 · 21h 12m · 6 days listened`
  (sum of days in that week). Newest week first.
- Days with zero listening: skip entirely (no empty rows).
- Click anywhere on a row → existing behavior: switch to Sessions zoom scrolled to
  that day's header.
- Hover (desktop): subtle row highlight.
- The old rich day cards are REMOVED (git history keeps them if we want them back).

## 4. Regression constraints

- Phone (<900px) Sessions view: identical to 2.5 except multi-expand now allowed.
- Grid density (auto-fill ~300px, cap 5 cols) unchanged.
- Quick-plays ledger, A1/A2 merges, seams, ♥/🔍, collapsible runs, exploration links:
  all unchanged.
- Header subtitle: "Pass 2.6 · multi-expand + wide-panel rail + dense Days".
- Keep D/E/F tabs untouched.
