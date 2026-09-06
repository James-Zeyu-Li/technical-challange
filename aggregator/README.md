# README

## Setup Instructions

-

## Run Instructions

-

## Test Instructions

Run from the `aggregator/` directory (needs the `aggregator.*` package on the path):

```bash
python3 -m unittest tests.test_normalize -v
```

## What's Implemented So Far

- `normalize()` (`aggregator/normalize.py`): converts one raw record from a
  source's native schema into the unified product format (`id`, `title`,
  `source`, `price`, `category`), using a per-source field map
  (`aggregator/sources.py`). Raises `MalformedRecordError` on a missing
  field or failed type conversion, so a bad record can be skipped without
  crashing the batch.
- "Source" is currently just a config key (`"source_a"`/`"source_b"`/`"source_c"`)
  used to look up the right field map — there's no HTTP fetching yet, so it's
  not yet wired to the real mock API responses.

## Language/Framework Choice (if not using Python)

-

## Important Assumptions

-

## Known Limitations

-

## Anything Intentionally Not Implemented Because of the Time Limit

-
