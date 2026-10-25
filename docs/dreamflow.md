# Dreamflow

Dreamflow is the offline consolidation pipeline. `MemoryManager.run_dreamflow` and `memcave dreamflow run` both call it.

Stages, in order: select closed sessions, extract candidates, validate evidence, classify, detect duplicates, detect conflicts, plan a resolution, write, regenerate the index, and report.

The default extractor is rule-based. It keeps explicit structured outcomes that look like workflows and user messages that classify as something more specific than generic domain knowledge. `use_model=True` also asks the configured `ModelProvider` for JSON. Invalid JSON is retried once and then dropped. If the model category disagrees with the rule, or confidence is below 0.60, the candidate is stored as `needs_review` and is not promoted to active memory.

Exact content hashes and near-duplicate vectors merge evidence into the existing memory. Contradictions create a new version. A temporal change keeps the previous version active until the new `valid_from`. Forgotten hashes are suppressed. The run report lists rejected candidates, review candidates, and notes. Automatic resolution is marked uncertain.

A succeeded run is idempotent for the same pipeline version, tenant, user, and selected sessions. Zero eligible sessions is a successful empty run.

Opted-out users are skipped.
