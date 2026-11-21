# cockroach — Mech Royale bot (Builders Buildathon)

Survival-first bot for the `mechfight/1` battle royale.

- `bot.py` — the brain: zone timing, tucked-in camping spots, cover/evade from snipers, focus-fire with lead, loot + upgrades.
- `world.py` — map model: walk grid, line of sight, time-sliced A* (stays inside the 50 ms budget).
- `grid.bin`, `exposure.bin` — precomputed from the kit's `arena/` by `tools/build_grid.py` and `tools/build_exposure.py`.
- `mechfight.py` — the kit's wrapper, unchanged.

Local check (from the kit folder): `python3 check/check.py -- python3 -u /path/to/bot.py`
