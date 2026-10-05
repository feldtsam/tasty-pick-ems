"""
Tasty Pick Ems — CFB v1 shared red-zone ingestion layer.

Mirrors nfl/ in shape (its own venv, its own requirements.txt, "duplicate
rather than cross-import" as the stated rule everywhere else in this
codebase) — this package does NOT import from nfl/.

Scope of this package (CFB v1, per docs/CFB_v1_Scoring_Design_Spec.md §2,
§3, §8 and the approved Step 1 schema proposal):

  * Pull CFBD /plays/stats per completed FBS game for one (season, week),
    filtered by gameId so the 2,000-row/call cap is never hit.
  * Aggregation A  (TD Opportunity, §2): red-zone band touch/TD counts
    per (player_id, season, week)  ->  cfb_player_redzone_weekly
  * Aggregation B  (Situation, §3): red-zone band touch/TD counts ALLOWED
    per (defense team_id, position_group, season, week)
        ->  cfb_defense_redzone_allowed_weekly
  * Manual POST {season, week} trigger endpoint (cfb/api/index.py) that
    runs the ingestion and forwards both aggregations to their Lovable
    write routes via one HMAC-signed POST each.

Explicitly NOT in scope here: scoring / percentiles / rolling windows /
core_weights (a later task, computed at scoring time over these tables' raw
rows — NFL parity), Role & Momentum, Evidence Quality, Market Value, and
the Environment half of Situation (dome / wind / temp — a separate concern,
not part of the /plays/stats ingest).
"""
