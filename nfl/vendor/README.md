# Vendored dependencies

## `nfl_data_py` (0.3.3, MIT)

Vendored, not pip-installed, as of 2026-08. `nfl_data_py==0.3.3` on PyPI
declares `pandas<2.0,>=1.0` in its own package metadata — a stale pin
(the actual code has been validated against pandas 2.x throughout this
whole project; only the declared constraint is wrong, not the behavior).

Confirmed directly before vendoring: a plain
`pip install pandas==2.2.3 nfl_data_py==0.3.3` fails outright with pip's
own `ResolutionImpossible` error, regardless of requirements.txt
ordering or what else is listed. `pip`/`uv` have no per-line `--no-deps`
requirements.txt syntax, so there's no way to tell a normal
`pip install -r requirements.txt` to skip just this one package's
declared constraints. This is what actually broke the Vercel build:
`nfl_data_py` was never a real line in `requirements.txt` in the first
place, only documented as a manual local `pip install --no-deps` second
step — which a single-command `pip install -r requirements.txt` build
(Vercel's, and any other standard one) never runs.

`nfl/vendor/nfl_data_py/__init__.py` is a verbatim copy of the real
0.3.3 source — see its own header comment for the exact reasoning, and
`nfl/vendor/nfl_data_py/LICENSE` for the original MIT license, unchanged.
Callers add `nfl/vendor` to `sys.path` before `import nfl_data_py` (see
`nfl/scripts/backfill_redzone.py` and `nfl/api/index.py`) — no different
in principle from those same files already adding `nfl/` itself to
`sys.path` to import `redzone`/`scoring`/`market_value`.

If nflverse ever ships a release with a relaxed pin, this vendored copy
should be dropped and the real PyPI package restored — checked for at
the time of vendoring (2026-08): still not the case, 0.3.3 is still
current and still declares the same pin.
