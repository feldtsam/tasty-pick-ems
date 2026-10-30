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

**Title**: Two Different Readings This Week

**Story**: Recent red-zone touchdown production and conversion ranks
below the median of this week's comparison group, and the matchup and
environment read the same way.

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

**Story**: Recent red-zone touchdown production and conversion, and the
matchup and environment reading, both rank above the median of this
week's comparison group. There isn't much tension to chase this week —
the on-field picture and the price are telling the same story, not
competing ones.

**Why reasons**:
1. *Pillar: td_opportunity, 5 stars* — "Recent red-zone touchdown
   production and conversion ranks well above the median of this week's
   comparison group." — **field: `td_opportunity` (value 92.5, median
   59.9, position "above")**
2. *Pillar: situation, 4 stars* — "The matchup and environment reading
   also ranks above the median of this week's comparison group." —
   **field: `situation` (value 73.5, median 47.0, position "above") —
   re-checked directly against the real week-7 data before this
   revision; it is clearly above median, not a borderline call**
3. *Pillar: market_price, 4 stars* — "The price implies a little over
   seven in ten." — **field: `implied_probability_pct` (71.2)**

**Change from the prior draft**: the earlier version mentioned a role
trend in the story (above median) without a why_reason backing it — an
ungrounded claim, exactly the failure mode this package's own
citation-checking validators exist to catch. Fixed by replacing that
mention with `situation`, which the real data does support and which
now has its own tagged why_reason. role_momentum is dropped from this
card entirely rather than left as an unbacked story claim.

**Left out, not invented**: same gap as Card A — no snap-share/touch-
count trend field is populated in this export to back a more specific
"how" claim about any role change.

---

## Card C — a mid-price player whose evidence ranks higher

**Darnell Washington, +368, `diverges_from_expectation`, `evidence_ranks_higher`**

**Title**: The Profile Is Ahead of the Price

**Story**: Recent red-zone touchdown production ranks above the median
of this week's comparison group, and the week-over-week change in role
does too, though that role trend is still an early read. The matchup, and how solid the numbers
behind this read are, both sit right around the comparison group's
usual range. The football evidence here sits ahead of where the price
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

**Note on the HARD RULES this card respects**: the two median claims
above each name their comparison group explicitly in the same sentence
("...of this week's comparison group"), satisfying the comparison-basis
rule. The sentence comparing the evidence to the price was corrected in
this revision — it originally read "the profile ranks ahead of where
the price sits," which uses "ranks" without naming a comparison group
in that same sentence and would have failed the comparison-basis check;
it's rephrased above to "the football evidence here sits ahead of where
the price sits," which drops "ranks" entirely rather than restating the
basis a second time. Separately, and still true: nowhere does the text
say this makes the price wrong, cheap, a bargain, or an opportunity —
it states the relative-ranking fact (`evidence_vs_price` =
`evidence_ranks_higher`) without turning it into a verdict.

---

## Card D — a long shot with a real signal

**Chris Bell, +637, `diverges_from_expectation`, `evidence_ranks_higher`**

**Title**: A Real Signal, Still Early

**Story**: Recent red-zone touchdown production ranks above the median
of this week's comparison group, and the week-over-week change in role
does too, though both readings here are thinner and earlier than most
of the comparison group's. The matchup reading is right around the
group's usual range. This is a real above-median reading within this
week's comparison group, on an early sample.

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

**Why reasons**:
1. *Pillar: market_price, 2 stars* — "The price implies a bit under one
   in eight." — **field: `implied_probability_pct` (11.9) — the one
   fact this label does NOT withhold, since arithmetic-only pricing is
   always available regardless of the evidence-gate outcome**

**Proposed decision, awaiting Sam's approval**: the real schema calls
for 2-3 `why_reasons` per card, but for a fully gated
`insufficient_evidence` row, `pillar_context` is withheld down to just
the `limits` list and the gate's own `reason`
(`role_momentum_completeness` = 0 fired the completeness gate here, per
`evidence_price_classifier.py`'s own `reason` text) — neither of those
is itself a citable fact today, and there is no second real pillar
claim available to write honestly. **Proposal: an `insufficient_
evidence` card is allowed exactly 1 why_reason** (the price arithmetic,
via `market_price`/`implied_probability_pct`, which the label never
withholds) **instead of the usual 2-3**, rather than inventing a second
reason from data that doesn't exist, and rather than replacing the
why_reasons section with a bare notice. A real writer entry point's
schema-shape validation needs to special-case this: the 2-3 count
requirement applies only when `label != "insufficient_evidence"`.
