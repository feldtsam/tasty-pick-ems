# Shared webhook secret — MLB / NFL / Lovable

One HMAC secret value is split across three env vars in three separate
dashboards. All three must hold the **identical** value. Rotating one
without the other two doesn't fail loudly — grading just starts getting
401s from whichever side you missed.

| Env var | Lives in | Signs / verifies |
|---|---|---|
| `PIPELINE_WEBHOOK_SECRET` | Lovable, `tastypickems.com` (the Vercel project behind the `tastypickems`/Lovable app) | Verifies incoming signed requests on the shared read/write routes — `bookmarks-needing-grading-read.ts`, `bookmarks-needing-correction-read.ts`, `bookmark-results-write.ts`, `nfl-shelf-picks-needing-*-read.ts`, `nfl-official-pick-results-write.ts`, etc. |
| `LOVABLE_WEBHOOK_SECRET` | MLB pipeline's Vercel project (`pipeline`) | Signs MLB's outbound calls to the routes above (`grade-bookmarks`, `grade-official-picks`, `curate-shelves`, ...). |
| `PIPELINE_WEBHOOK_SECRET` | NFL pipeline's Vercel project (`tasty-pick-ems`) | Signs NFL's outbound calls to the same shared routes (`grade-nfl-bookmarks-live`, `grade-nfl-bookmarks-correction`). Same name as the Lovable-side var above — different project, easy to confuse, not the same setting. |

**Rotating this secret**: generate one new value, set it in all three
places in the same pass, then trigger a fresh deploy on both Vercel
projects (env var changes need a new deployment to reach already-running
functions — pushing a trivial commit is enough). Don't consider the
rotation done until a real grading call has succeeded end-to-end on
*both* the MLB and NFL side — a mismatch shows up as a 401 on whichever
pipeline you didn't just test, not as an error where you're looking.

**Real incident this closes**: rotating the Lovable-side secret (to fix
NFL grading auth) updated the Lovable and NFL values but missed MLB's
`LOVABLE_WEBHOOK_SECRET`, which broke MLB's `grade-official-picks`
scenario with silent 401s until the mismatch was found by hand.
