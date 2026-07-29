"""Structured signal extraction — the replacement for regex-parsed research.

As of phase 5 this is the primary scoring path. `services/parsing.py` remains
as the fallback for artifacts that have not been extracted — a large majority
of the historical rows — so the pipeline still behaves exactly as before
wherever extraction has not run.

The flow over the same artifacts:

    ResearchArtifact.cell_value        (already persisted, untouched)
      → extraction.py                  one LLM pass, content-hash cached
      → ArtifactExtraction + ExtractedSignalRow    (new tables)
      → scoring.py                     0-100 composite with a stored breakdown
      → elaboration.py                 per-account brief written from the
                                       stored signals, not from raw prose

Intentionally no imports at package level: `app/models.py` registers
`signals.models` for `create_all`, and pulling `extraction` in transitively
would drag the Anthropic client into every import of the ORM. Import the
submodule you need.
"""
