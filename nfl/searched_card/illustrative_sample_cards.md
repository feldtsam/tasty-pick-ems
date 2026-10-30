# Illustrative Sample Cards (Stage 1h, Task 6)

**ILLUSTRATIVE ONLY — NOT REAL CARDS, NOT CANONIZED.** Written by hand
below, exactly as the writer model would, using ONLY fields the writer
will actually receive (`pillar_context`, price arithmetic, `label`,
`evidence_vs_price`, and the `market_price` arithmetic fact) — no
Anthropic API call was made. The underlying numbers ARE real (week 7,
2026, from the two local CSV exports already used throughout Stage
1e–1h), so the pillar values/medians/positions/labels below are
accurate as of that data; the prose wrapping them is hand-written
illustration, not a generated card. Every claim below is tagged with the
exact field that backs it. Where a claim would need a field this export
doesn't carry (touch counts, snap-share trend, depth-chart movement),
it is explicitly flagged and left out rather than invented — see the
note after each card.

---

## Card A — a favorite whose evidence ranks lower

**Derrick Henry, -188, `diverges_from_expectation`, `evidence_ranks_lower`**

**Title**: The Price Is Betting on the Name

**Story**: Recent red-zone touchdown production and conversion ranks
below the median of this week's comparison group, and the matchup and
environment read the same way. The week-over-week change in his role
hasn't picked up either. That's an unusual mix for a price this short —
the market reads far more confident here than the production and
matchup picture does this week.

**Why reasons**:
1. *Pillar: td_opportunity, 2 stars* — "His recent red-zone touchdown
   production and conversion ranks below the median of this week's
   comparison group." — **field: `td_opportunity` (value 47.4, median
   59.9, position "below")**
2. *Pillar: situation, 2 stars* — "The matchup and environment reading
   also ranks below the median of this week's comparison group." —
   **field: `situation` (value 31.9, median 47.0, position "below")**
3. *Pillar: market_price, 3 stars* — "The price implies a touch under
   two-thirds likelihood to score anytime." — **field:
   `implied_probability_pct` (65.3), framed arithmetically per the hard
   rule**

**Left out, not invented**: a real card might want to say WHICH part of
the role changed, or cite a specific recent-game trend — this export
has no `snap_share_trend_pct`/`touch_share_trend_pct`-style field
populated for this row (the slim weekly export used for this check
carries only the four raw pillar scores, not the full citable-fields
set `searched_card_citable_fields.py` defines), so no touch/snap-share
claim is made.

---

## Card B — a favorite that is aligned

**Christian McCaffrey, -247, `supports_expectation`, `aligned`**

**Title**: Nothing Here Is Fighting the Price

**Story**: Recent red-zone touchdown production, the week-over-week
change in role, and the matchup and environment reading all sit above
the median of this week's comparison group. There isn't much tension to
chase this week — the on-field picture and the price are telling the
same story, not competing ones.

**Why reasons**:
1. *Pillar: td_opportunity, 5 stars* — "Recent red-zone touchdown
   production and conversion ranks well above the median of this week's
   comparison group." — **field: `td_opportunity` (value 92.5, median
   59.9, position "above")**
2. *Pillar: role_momentum, 3 stars* — "His role has been trending up
   this week, by the same comparison-group standard." — **field:
   `role_momentum` (value 61.6, median 49.2, position "above") —
   phrased as change, never as role size, per the hard rule**
3. *Pillar: market_price, 4 stars* — "The price implies a little over
   seven in ten." — **field: `implied_probability_pct` (71.2)**

**Left out, not invented**: same gap as Card A — no snap-share/touch-
count trend field is populated in this export to back a more specific
"how" claim about the role change.

---

## Card C — a mid-price player whose evidence ranks higher

**Darnell Washington, +368, `diverges_from_expectation`, `evidence_ranks_higher`**

**Title**: The Profile Is Ahead of the Price

**Story**: Recent red-zone touchdown production ranks above the median
of this week's comparison group, and the week-over-week change in role
does too, though that role trend is still an early read. The matchup and
data-quality readings are both right around the comparison group's
usual range. The football profile here ranks ahead of where the price
sits this week — not a claim about whether the price itself is right,
just about which side of the ledger is making the stronger case right
now.

**Why reasons**:
1. *Pillar: td_opportunity, 4 stars* — "Recent red-zone touchdown
   production and conversion ranks above the median of this week's
   comparison group." — **field: `td_opportunity` (value 82.9, median
   59.9, position "above")**
2. *Pillar: role_momentum, 3 stars* — "The week-over-week change in his
   role also ranks above the median, though it's still an early trend."
   — **field: `role_momentum` (value 62.1, median 49.2, position
   "above"); the "still early" phrasing reflects
   `role_momentum_completeness` = 80 (not yet 100), without naming the
   field or the number directly in `story`, per the plain-language
   rule**
3. *Pillar: market_price, 3 stars* — "The price implies a bit over one
   in five." — **field: `implied_probability_pct` (21.4)**

**Note on the HARD RULE this card is built to respect**: nowhere above
does the text say this makes the price wrong, cheap, a bargain, or an
opportunity — "the profile ranks ahead of the price" states the
relative-ranking fact (`evidence_vs_price` = `evidence_ranks_higher`)
without turning it into a verdict.

---

## Card D — a long shot with a real signal

**Chris Bell, +637, `diverges_from_expectation`, `evidence_ranks_higher`**

**Title**: A Real Signal, Still Early

**Story**: Recent red-zone touchdown production ranks above the median
of this week's comparison group, and the week-over-week change in role
does too — though both readings here are thinner and earlier than most
of the comparison group's. The matchup reading is right around the
group's usual range. This is a real above-median signal, not a
coincidence of a small sample, but it is a newer one than Card C's.

**Why reasons**:
1. *Pillar: td_opportunity, 3 stars* — "Recent red-zone touchdown
   production and conversion ranks above the median of this week's
   comparison group, on a thinner read than most." — **field:
   `td_opportunity` (value 66.6, median 59.9, position "above");
   "thinner read" reflects `td_opportunity_completeness` = 60**
2. *Pillar: role_momentum, 3 stars* — "The change in his role also
   ranks above the median, on an even earlier read." — **field:
   `role_momentum` (value 56.2, median 49.2, position "above");
   `role_momentum_completeness` = 40**
3. *Pillar: market_price, 2 stars* — "The price implies a bit under one
   in seven." — **field: `implied_probability_pct` (13.6)**

**Left out, not invented**: this is the clearest case among the five
where a real production system would want a trailing touch-count or
target-share field to say WHAT changed in the role — not available in
this export, so the card stays at "the change ranks above the median,"
never a specific count.

---

## Card E — an `insufficient_evidence` card

**Marquise Brown, +740, `insufficient_evidence`**

**Title**: Not Enough Here to Read

**Story**: The evidence behind this one is too thin to trust a
comparison at all this week — the usage data that would normally
support a read on his role is effectively missing, not just modest.
That's a plain limitation to say outright, not a reason to reach for a
tidier story than the data supports.

**Why reasons**:
1. *Pillar: market_price, 2 stars* — "The price implies a bit under one
   in eight." — **field: `implied_probability_pct` (11.9) — the one
   fact this label does NOT withhold, since arithmetic-only pricing is
   always available regardless of the evidence-gate outcome**

**An open design question this card surfaced, not resolved here**: the
real schema calls for 2-3 `why_reasons` per card. For a fully gated
`insufficient_evidence` row, `pillar_context` is withheld down to just
the `limits` list and the gate's own `reason`
(`role_momentum_completeness` = 0 fired the completeness gate here, per
`evidence_price_classifier.py`'s own `reason` text) — neither of those
is itself a citable fact today, and there is no second real pillar
claim available to write honestly. This sample intentionally shows only
ONE why_reason rather than inventing a second one from data that
doesn't exist. Whether a real writer entry point should (a) allow fewer
than 2 why_reasons specifically for `insufficient_evidence` cards, or
(b) add a citable "why this was gated" fact so a second, honest reason
can reference the gate itself, is a real open question this
illustration surfaces — not something to guess an answer to here.
