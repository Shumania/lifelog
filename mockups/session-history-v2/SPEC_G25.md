# Variant G — Pass 2.5 (2026-07-28, Andrew feedback on 2.4 wide-screen UX)

Builds on SPEC_G24.md. Only wide-screen expansion + grid density change. Everything else in G pass 2.4 is locked and must not regress.

## Andrew's feedback (verbatim intent)
1. Pinned right detail pane on large screens does NOT work well — when he clicked a card it opened far away on the right side. He wants expansion "in line somehow."
2. Freeing the right pane should let the grid use more horizontal space — 2.4 maxed out at 3 columns; he wants more cards per row on a wide PC monitor.

## Changes

### 1. Remove the pinned right pane entirely
- Delete the ≥1200px pane mode, pane element, pane CSS, and pane JS path.
- One expansion behavior at all widths ≥900px (below 900px = existing single-column inline expand, unchanged).

### 2. Full-width inline expansion row ("Google Images" pattern)
- Clicking a card in the grid inserts a detail panel that spans the FULL grid width, positioned immediately below the ROW containing the clicked card.
- Implementation: panel is a grid item with `grid-column: 1 / -1`, inserted after the last card of the clicked card's visual row (compute row by comparing offsetTop of grid children), OR use `grid-row` placement — whichever is robust with auto-fill.
- A caret/notch (▲, styled with border trick) visually points from the panel's top edge to the horizontal center of the clicked card.
- The clicked card gets the existing `.sel` highlight ring.
- Panel content = the exact same expanded content as the current inline expansion (top line, art fan, ♥/🔍 rows, collapsible album runs, seams, exploration links). Reuse the existing expansion renderer — do not fork it.
- Close: clicking the same card again, clicking another card (panel moves to the new card's row, single panel at a time), or an ✕ button top-right of the panel.
- When the panel opens, scroll it into view only if its top would be off-screen (gentle `scrollIntoView({block:'nearest'})`).
- Columns must NEVER reflow when the panel opens — the panel is a new full-width row; cards keep their column positions.

### 3. Denser grid on wide screens
- Replace the fixed 2-col/3-col breakpoints with `grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))` for the day grid at ≥900px.
- Card min 300px, so ~4 cols at 1300px, ~5 cols at 1600px+.
- Day headers still span full width (`grid-column: 1 / -1`).
- Quick-plays ledger line still docks last in its day and spans full width.
- Days-zoom day cards use the same auto-fill density and the same full-width inline expansion pattern if they expand (day cards currently navigate to Sessions zoom — keep that navigation behavior).

### 4. Version stamp
- Title/subtitle updated to "Pass 2.5 · inline expansion + dense grid".

## Regression checklist (must all pass before publish)
- Phone width (390px): single column, inline expand, quick-ledger, seams — identical to 2.4.
- ≥900px: clicking a card opens full-width panel below its row; caret aligned to card center; columns unchanged.
- Clicking a different card moves the panel; clicking same card / ✕ closes it.
- Merged-session seams (`· resumed …`), collapsible album runs, ♥/🔍, exploration links all render inside the panel.
- Days zoom: denser grid; day-card click still jumps to Sessions zoom at that day.
- Bill Evans Portrait In Jazz art still renders.
- D/E/F variants untouched.
