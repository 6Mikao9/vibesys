These fixtures were captured from `origin/main` (`cc0e642a`) before moving the
`vs_loop_state.api` encoders, using Pydantic 2.12.5. `current.json` covers all
persisted fields, `legacy.json` preserves retired policy strings and defaults,
and `deferred.json` preserves contradictory-review normalization.
`legacy-input.json` is the sparse historical input for `legacy.json`.

`recorded-with-incomparable-verdict.json` is `deferred.json` stored with the verdict word
`incomparable`, which `inconclusive` replaced; it must keep loading as the same record.

Tests compare decoded records to independently constructed records, persisted
fields, and JSON bytes. Regenerate only for a deliberate, reviewed format change:

```bash
uv run python tests/vibesys/orchestration/hypothesis/fixtures/round_records/regenerate.py
```
