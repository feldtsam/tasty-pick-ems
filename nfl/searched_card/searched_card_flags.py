"""
Searched Card writer — the one feature flag that gates all of it.

STAGE 1 DELIVERABLE. Everything under nfl/searched_card/ is a design-and-
tests package for a NEW, distinct writer type (see this package's other
modules): a deterministic evidence-vs-price classifier that runs before
any writer call, a new system prompt, a new output schema, a new
citable-fields list, and a new pricing-claim validator. None of it is
wired into any existing pipeline file. Confirm this for yourself:

    grep -rn "searched_card" nfl --include=*.py | grep -v nfl/searched_card/

returns nothing outside this directory — nothing in api/index.py,
curate_home_shelves.py, generate_nfl_shelf_card_content.py, or any
scheduler/cron entry point imports anything here. The only way to reach
this code is to import nfl.searched_card.* directly, which the live
pipeline does not do.

SEARCHED_CARD_WRITER_ENABLED below is the single source of truth for
"is this live yet." Every would-be entry point in this package
(searched_card_writer_entry.generate_searched_card_draft) reads it at
call time and refuses to run while it is False — see that module's own
docstring. Flipping it to True is a human decision, made after review,
and this file performs none of the wiring that decision would also
require (a real shelf/curation caller, a real API key path, a real
database write) — it only ungates code that, as of this commit, has no
caller at all.
"""

SEARCHED_CARD_WRITER_ENABLED = False
