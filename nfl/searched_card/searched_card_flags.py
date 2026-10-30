"""
Searched Card writer — the one feature flag that gates all of it.

STAGE 1 DELIVERABLE (this file predates Stage 2). Everything under
nfl/searched_card/ is a design-and-tests package for a NEW, distinct
writer type (see this package's other modules): a deterministic
evidence-vs-price classifier that runs before any writer call, a new
system prompt, a new output schema, a new citable-fields list, and a new
pricing-claim validator.

STAGE 2 UPDATE: one caller now exists outside this directory —
nfl/api/index.py's POST /api/generate-searched-card route imports
searched_card_writer_entry.generate_searched_card_draft and this flag
directly, so the old "confirm via grep that nothing outside this
directory imports anything here" check below is no longer literally true
for api/index.py and should not be re-run expecting a clean result there:

    grep -rn "searched_card" nfl --include=*.py | grep -v nfl/searched_card/

now returns exactly that one route (and nothing else — no scheduler/cron
entry point, no curate_home_shelves.py, no generate_nfl_shelf_card_
content.py imports anything here). That route itself calls
check-the-flag-then-401/503-before-ever-reaching-the-writer the same way
generate_searched_card_draft does internally (see that function's own
docstring) — reachability through an HTTP route is not the same thing as
being LIVE, which is exactly what the flag below still, and only, decides.

SEARCHED_CARD_WRITER_ENABLED below is the single source of truth for
"is this live yet." Every would-be entry point in this package
(searched_card_writer_entry.generate_searched_card_draft) reads it at
call time and refuses to run while it is False — see that module's own
docstring. Flipping it to True is a human decision, made after review.
This file still performs none of that review itself — it only ungates
code that, as of this commit, has a real caller (the route above) but
that caller itself also still refuses to run while this flag is False.
"""

SEARCHED_CARD_WRITER_ENABLED = False
