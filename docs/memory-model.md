# Memory model

This document defines the 0.1 domain model, invariants, and persistence mapping. The structural context is [architecture.md](architecture.md).

Identifiers are UUID4. Timestamps are timezone-aware UTC. Confidence and importance are floats in the closed interval `[0, 1]`. Pydantic models reject values outside that interval and reject naive datetimes.

## Aggregates

### Tenant

A tenant is the hard isolation boundary.

| Field | Meaning |
| --- | --- |
| `tenant_id` | Primary identity |
| `name` | Display name, unique within the deployment |
| `created_at` | Creation time |
| `metadata` | Caller-supplied JSON. Secrets are redacted before write. |

### User

A user belongs to one tenant.

| Field | Meaning |
| --- | --- |
| `user_id` | Primary identity |
| `tenant_id` | Owning tenant |
| `external_id` | Optional identifier from the caller |
| `display_name` | Optional label |
| `memory_opt_out` | When true, dreamflow and automatic prompt injection skip this user |
| `created_at` | Creation time |
| `metadata` | Redacted caller JSON |

`external_id` is unique per tenant when present.

### Session

| Field | Meaning |
| --- | --- |
| `session_id` | Primary identity |
| `tenant_id`, `user_id` | Scope |
| `title` | Optional label |
| `status` | `active` or `ended` |
| `started_at`, `ended_at` | Lifetime |
| `dreamflow_state` | `pending`, `processed`, `skipped`, or `failed` |
| `dreamflow_run_id` | Last run that claimed the session, if any |
| `metadata` | Redacted caller JSON. `memory_opt_out: true` skips the session. |

A session is eligible for dreamflow when it is `ended`, its dreamflow state is `pending` or `failed`, the user and session are not opted out, and retention still allows it.

### SessionEvent

Events are the append-only episode log.

| Field | Meaning |
| --- | --- |
| `event_id` | Primary identity |
| `tenant_id`, `user_id`, `session_id` | Scope |
| `sequence` | Monotonic integer within the session, starting at 1 |
| `event_type` | One of the types below |
| `occurred_at` | Observation time |
| `payload` | Type-specific observable content |
| `processing_state` | `unprocessed`, `processed`, `skipped`, or `failed` |
| `metadata` | Redacted caller JSON |

Event types:

| Type | Payload |
| --- | --- |
| `user_message` | `content` |
| `assistant_message` | Observable `content` only |
| `tool_call` | `ToolCall` |
| `tool_result` | `ToolResult` |
| `final_answer` | `content` |
| `user_feedback` | Optional `target_event_id`, optional `rating`, optional `comment` |
| `explicit_memory_instruction` | Instruction fields below |
| `structured_outcome` | `outcome_type` and a JSON object |

`ToolCall` contains `tool_call_id`, `tool_name`, redacted `arguments`, and `occurred_at`. `ToolResult` contains `tool_call_id`, `tool_name`, redacted `content`, `is_error`, and `occurred_at`.

The public store API can append, list, update processing columns, and erase. It cannot edit `payload`, `event_type`, `sequence`, or scope. Sequence assignment happens inside the append transaction.

List filters are tenant, user, session, event type, processing state, and an inclusive-exclusive time range. Pagination uses an opaque cursor of `(occurred_at, event_id)` plus a limit from 1 to 200, default 50. The tenant predicate always comes from `TenantScope`, never from the cursor.

### Derived memory

Persistence uses two tables. Callers normally see one object.

`MemoryRecord` is the stable identity:

| Field | Meaning |
| --- | --- |
| `memory_id` | Stable identity across versions |
| `tenant_id` | Tenant |
| `scope` | `user` or `tenant` |
| `user_id` | Required for `user`, null for `tenant` |
| `category` | Category enum |
| `logical_path` | Sanitized index path |
| `status` | Cache of the latest version’s status |
| `current_version_number` | Latest version number |
| `content_hash` | Hash of tenant, user, category, and casefolded content |
| `created_at`, `updated_at` | Record lifetime |
| `deleted_at` | Set on soft delete |
| `metadata` | Redacted caller JSON |

`MemoryVersion` is immutable:

| Field | Meaning |
| --- | --- |
| `version_id` | Identity of this version |
| `memory_id`, `tenant_id` | Parent record and tenant |
| `version_number` | Starts at 1 |
| `predecessor_version_id` | Previous version, if any |
| `title`, `content` | Human-readable memory |
| `structured_content` | Optional JSON, used by workflows and defined facts |
| `status` | Status of this version |
| `confidence`, `importance` | Scores in `[0, 1]` |
| `valid_from` | First instant this version may be retrieved |
| `valid_until` | First instant it is no longer valid, or null |
| `created_at` | Version write time |
| `source` | `explicit_instruction`, `api`, or `dreamflow` |
| `embedding_id` | Vector row, when embedded |
| `content_hash` | Normalized hash of this version |
| `metadata` | Redacted JSON |

The public `Memory` read model joins the record to the selected version and adds `evidence`. `updated_at` on that read model is the version’s `created_at`. `created_at` remains the record’s creation time.

Categories:

| Category | Use |
| --- | --- |
| `profile` | Stable facts about a user, such as role or locale |
| `preference` | How the user wants the agent to behave |
| `domain_knowledge` | Definitions and domain rules |
| `project` | Project context |
| `workflow` | A reusable procedure |
| `correction` | A replacement of an earlier belief |
| `constraint` | A limit the agent is expected to respect |

Statuses:

| Status | Retrieval default |
| --- | --- |
| `candidate` | Hidden. Used for review and for ambiguous explicit instructions. |
| `active` | Eligible when the validity window contains `as_of` |
| `superseded` | Hidden. History of an outright replacement. |
| `stale` | Hidden. Bookkeeping after a window has closed. |
| `archived` | Hidden. Kept on purpose. |
| `deleted` | Hidden. Hash remains until user erasure so dreamflow can suppress recreation. |

### Evidence, candidates, and conflicts

`MemoryEvidence` links a version to the episode that supports it.

| Field | Meaning |
| --- | --- |
| `evidence_id` | Identity |
| `memory_id`, `version_id`, `tenant_id` | Target |
| `session_id`, `event_id` | Source event |
| `quote` | Short observable excerpt, redacted |
| `weight` | Support weight in `[0, 1]` |
| `created_at` | Link time |

Dreamflow-origin versions require at least one evidence row. Explicit API writes may have none. Explicit instruction writes cite the instruction event.

`MemoryCandidate` is pipeline state, not a public memory:

| Field | Meaning |
| --- | --- |
| `candidate_id`, `dreamflow_run_id` | Identity and run |
| `tenant_id`, `user_id`, `session_id` | Source scope |
| `category`, `title`, `content`, `structured_content` | Proposal |
| `confidence`, `importance` | Proposal scores |
| `valid_from`, `valid_until` | Proposed window |
| `evidence_event_ids` | Supporting events |
| `status` | `extracted`, `rejected`, `duplicate`, `conflict`, `accepted`, or `needs_review` |
| `proposed_action` | `create`, `new_version`, `merge`, `supersede`, `archive`, or `skip` |
| `rejection_reason` | Present when rejected |

`MemoryConflict` records a non-silent collision:

| Field | Meaning |
| --- | --- |
| `conflict_id`, `tenant_id` | Identity |
| `memory_id`, `candidate_id` | Existing memory and proposal |
| `conflict_type` | `contradiction`, `temporal_change`, `preference_override`, or `scope_clash` |
| `resolution` | `unresolved`, `versioned`, `merged`, `needs_review`, or `rejected` |
| `resolution_strategy` | `rule`, `model_assisted`, or `human` |
| `notes` | Short explanation of why the strategy was chosen |
| `created_at` | Detection time |

### Retrieval

`RetrievalQuery` includes tenant, user, query text, optional categories, memory kinds (`episodic`, `derived`, or `both`), `top_k`, `token_budget`, `as_of`, `include_shared`, `include_candidates`, and `min_confidence`.

`RetrievalHit` includes a hit id, kind, scope, source id, title, content excerpt, normalized score, `score_components`, rank, token estimate, evidence ids when applicable, and a short machine-readable explanation key such as `derived_weighted_sum` or `episodic_weighted_sum`.

The search response adds `truncated`, the rendered untrusted context, and audit metadata. Deleted, expired, unauthorized, and cross-tenant rows are not hits.

### Dreamflow runs

`DreamflowRun` includes `run_id`, tenant, optional user, status (`pending`, `running`, `succeeded`, `failed`, `partial`), timestamps, pipeline version, idempotency key, stage counts, and error summary.

`dreamflow_items` store per-session stage status for resume: item id, run id, tenant, session id, stage, status, content hash, resulting memory id, error, and a redacted payload snapshot.

### Benchmark records

`BenchmarkCase`, `BenchmarkRun`, and `MetricResult` follow [evaluation.md](evaluation.md). Persisted runs store the configuration, package version, provider name, and raw metric payloads needed to reproduce a report. They do not store provider credentials.

## Invariants

1. An event payload is immutable after append.
2. A memory version is immutable after insert. A change is a new version.
3. `update_memory` requires `expected_version`. A mismatch raises `VersionConflictError` and writes nothing.
4. An outright update marks the previous version `superseded`, copies forward its evidence, adds any new evidence, writes the new version, updates the record pointer, and appends an audit event.
5. A temporal change leaves both versions `active` in non-overlapping windows. The previous `valid_until` equals the new `valid_from`. Search selects the version whose window contains `as_of`.
6. A version is visible only when its status is `active`, `valid_from <= as_of`, and `valid_until` is null or greater than `as_of`.
7. A future `valid_from` does not hide an older version that still covers `as_of`.
8. `record.current_version_number` points at the latest version, which may be in the future. Retrieval uses the validity window, not that pointer alone.
9. User-scoped memory is visible only to that user inside the tenant. Tenant-scoped memory has a null `user_id` and is visible inside the tenant when `include_shared` is true.
10. No query returns a row from another tenant.
11. Soft delete appends a deleted version and a suppression hash. It does not rewrite the previous version. A later explicit remember of the same content clears that hash.
12. Dreamflow must not create a new active memory whose normalized hash matches a deleted memory in the same tenant, scope, user, and category. An explicit remember or `add_memory` may.
13. Active and candidate hashes are unique per tenant, scope, user, and category. The database backstop is a partial unique index. The writer also checks inside the transaction.
14. Closing a validity window or marking a version superseded never removes its evidence.
15. Opt-out skips dreamflow and automatic index injection. It does not block erasure or an explicit read.
16. Redaction runs before any payload, memory text, tool argument, or quote is stored.

## Content hash and logical path

Normalization for hashing is Unicode casefold, trim, and collapse internal whitespace. The hash is SHA-256 of tenant id, user id (nil UUID when shared), category, and that normalized content, separated by newlines. Punctuation stays, so small wording differences do not collapse accidentally. Duplicate cosine similarity is a second check for paraphrases and is configurable. A cosine match merges only when tenant, user, category, and subject key also match. The subject key is the first content-bearing token.

Logical paths use the category directory plus a slug of the title:

- casefold
- replace characters outside `[a-z0-9]` with `-`
- collapse repeated `-`
- reject empty slugs
- join as `<category>/<slug>`
- allow only `[a-z0-9/_-]`
- reject `.` segments, backslashes, and leading slashes
- limit depth to four segments
- append `-2`, `-3`, and so on on collision within the same scope

Callers cannot choose an unsanitized path. A requested path is accepted only after it passes the same rules and stays under the record’s category directory.

## Explicit instructions

`explicit_memory_instruction` payload:

| Field | Required | Meaning |
| --- | --- | --- |
| `instruction_type` | yes | `remember`, `forget`, `correct`, or `temporal_change` |
| `raw_text` | yes | Observable user text |
| `category` | no | Target category |
| `title`, `content` | no | Structured fields when the caller already parsed them |
| `target_memory_id` | no | Target of forget or correct |
| `valid_from`, `valid_until` | no | Temporal window |

When structured fields are present, the write uses them. When only `raw_text` is present, a small rule parser handles these patterns:

| Pattern | Result |
| --- | --- |
| Starts with “remember that” and contains “prefer” | `preference`, active, confidence 1, source `explicit_instruction` |
| Starts with “remember that” | `domain_knowledge`, active, confidence 1 |
| Starts with “forget” | Soft-delete the targeted memory, or the single active memory whose title or content contains the remaining text. Multiple matches become `needs_review`. |
| Contains “instead of” | `correction`, new version, previous version superseded |
| Contains “as of” and a parseable ISO date | `temporal_change`, adjacent validity windows |

Anything else is stored as a `candidate` with confidence 0.4 and is not in the default search results. The episodic event itself is searchable immediately.

Instruction writes and the triggering event commit or roll back together.

## Workflow structure

Workflow `structured_content` uses this shape when the memory is a procedure:

```json
{
  "goal": "quarterly shipped revenue",
  "steps": [
    {"tool": "run_query", "purpose": "aggregate shipped orders"}
  ],
  "tables": ["orders", "order_items"],
  "joins": ["orders.order_id = order_items.order_id"],
  "filters": ["orders.status = 'shipped'"],
  "validation": ["compare the total with the finance summary"]
}
```

Additional keys are allowed. Missing keys are allowed. The benchmark fixtures use this shape so workflow reuse can be detected without a model.

## Classification rules

The classifier uses the first matching rule:

| Signal | Category |
| --- | --- |
| Explicit instruction category | That category |
| “prefer”, “I like”, “please always” | `preference` |
| “instead of”, “do not use”, “that was wrong” | `correction` |
| “must”, “never”, “do not” as a standing limit | `constraint` |
| Steps, tables, joins, or tool sequence | `workflow` |
| Project name or repository context | `project` |
| Definition or glossary language | `domain_knowledge` |
| Stable personal fact | `profile` |

If the rule and the model disagree, the candidate status is `needs_review` and the rule’s category is stored as the proposal. The model does not silently replace the rule.

## Validity and deletion

`delete_memory` and a successful forget are soft deletes. Retrieval skips them. The hash suppresses dreamflow.

`erase_user_data` removes, for one user in one tenant:

- sessions and events
- user-scoped records, versions, evidence, embeddings, and conflicts
- dreamflow items whose payload cites that user
- benchmark rows that contain that user’s raw answers, when those rows are stored in memcave

Tenant-shared memories stay. Other users stay. The audit row records actor, tenant, user, action `erase`, and counts. It does not record memory content, event content, or the erased text.

Retention, by default, keeps data until erasure. A configured maximum episodic age makes older sessions ineligible for dreamflow and hidden from default search. Applying retention to stored bytes can be an explicit maintenance call. It is not a background daemon in 0.1.

## Schema outline

Names below are the physical tables. Types are logical.

### tenants

`tenant_id` PK, `name` unique, `created_at`, `metadata_json`

### users

`user_id` PK, `tenant_id` FK, `external_id` nullable, `display_name` nullable, `memory_opt_out`, `created_at`, `metadata_json`

Unique `(tenant_id, external_id)` where `external_id` is not null. Index `(tenant_id, user_id)`.

### sessions

`session_id` PK, `tenant_id`, `user_id`, `title`, `status`, `started_at`, `ended_at`, `dreamflow_state`, `dreamflow_run_id` nullable, `metadata_json`

Index `(tenant_id, user_id, started_at)`. Index `(tenant_id, dreamflow_state, ended_at)` for the selector.

### session_events

`event_id` PK, `tenant_id`, `user_id`, `session_id` FK, `sequence`, `event_type`, `occurred_at`, `payload_json`, `processing_state`, `metadata_json`

Unique `(session_id, sequence)`. Index `(tenant_id, user_id, occurred_at)`. Index `(session_id, processing_state, sequence)`. The adapter checks that scope columns match the parent session.

SQLite FTS5 and PostgreSQL `tsvector` index the redacted textual payload.

### memory_records

`memory_id` PK, `tenant_id`, `scope`, `user_id` nullable, `category`, `logical_path`, `status`, `current_version_number`, `content_hash`, `created_at`, `updated_at`, `deleted_at` nullable, `metadata_json`

Check: `scope = 'user'` implies `user_id` is not null, and `scope = 'tenant'` implies `user_id` is null.

Index `(tenant_id, user_id, status, category)`. Index `(tenant_id, logical_path)`.

Partial unique index on `(tenant_id, scope, coalesced_user_id, category, content_hash)` where status is `active` or `candidate`. `coalesced_user_id` uses the nil UUID for tenant scope so SQL `NULL` does not defeat uniqueness.

### memory_versions

`version_id` PK, `memory_id` FK, `tenant_id`, `version_number`, `predecessor_version_id` nullable, `title`, `content`, `structured_content_json` nullable, `status`, `confidence`, `importance`, `valid_from`, `valid_until` nullable, `created_at`, `source`, `embedding_id` nullable, `content_hash`, `metadata_json`

Unique `(memory_id, version_number)`. Index `(tenant_id, status, valid_from, valid_until)`. Index `(memory_id, version_number)`.

### memory_evidence

`evidence_id` PK, `memory_id`, `version_id` FK, `tenant_id`, `session_id`, `event_id`, `quote`, `weight`, `created_at`

Index `(version_id)`, `(event_id)`, and `(tenant_id, memory_id)`.

### memory_embeddings

`embedding_id` PK, `tenant_id`, `owner_kind` (`event` or `memory_version`), `owner_id`, `dimension`, `embedding_json`, `created_at`

Unique `(owner_kind, owner_id)`. Index `(tenant_id, owner_kind)`.

PostgreSQL migration adds nullable `embedding vector(N)` and a cosine index. `N` is `MEMCAVE_EMBEDDING_DIM`, default 64. `engine_metadata.embedding_dimension` stores `N`.

### dreamflow_runs and dreamflow_items

Runs: `run_id` PK, `tenant_id`, `user_id` nullable, `status`, `pipeline_version`, `idempotency_key`, `started_at`, `finished_at` nullable, `report_json`, `error` nullable.

Unique `(tenant_id, idempotency_key)`.

Items: `item_id` PK, `run_id` FK, `tenant_id`, `session_id`, `stage`, `status`, `content_hash` nullable, `memory_id` nullable, `error` nullable, `payload_json`, timestamps.

Unique `(run_id, session_id, stage)`.

### audit_events

`audit_id` PK, `tenant_id`, `user_id` nullable, `actor`, `action` (`read`, `write`, `update`, `delete`, `erase`, `export`, `dreamflow`), `resource_type`, `resource_id` nullable, `occurred_at`, `metadata_json`

Index `(tenant_id, user_id, occurred_at)` and `(tenant_id, resource_type, resource_id)`.

Search metadata stores the query hash and redacted query, not the raw query. Erase metadata stores counts only.

### benchmark_runs and benchmark_results

Runs: `benchmark_run_id` PK, `tenant_id` nullable for local fixtures, `created_at`, `config_json`, `package_version`, `strategies_json`, `report_json`.

Results: `result_id` PK, `benchmark_run_id` FK, `case_id`, `strategy`, `repetition`, `metrics_json`, `output_json`.

Index `(benchmark_run_id, strategy, case_id)`.

### engine_metadata

`key` PK, `value` text. Required key: `embedding_dimension`.

## Structured model output

The extractor asks the model for JSON matching:

```json
{
  "candidates": [
    {
      "category": "workflow",
      "title": "string",
      "content": "string",
      "structured_content": {},
      "confidence": 0.0,
      "importance": 0.0,
      "valid_from": null,
      "valid_until": null,
      "evidence_event_ids": [],
      "evidence_quotes": []
    }
  ]
}
```

Pydantic validates the batch. Evidence ids that are not in the selected session reject that candidate. The rule-based extractor emits the same schema for explicit instructions and structured outcomes, so the later stages have one input type.

The conflict model returns:

```json
{
  "conflict_type": "contradiction",
  "suggested_resolution": "needs_review",
  "confidence": 0.0,
  "rationale": "short text"
}
```

`rationale` is stored in conflict notes only when it passes redaction and is at most 280 characters. Longer text is replaced with `rationale_omitted`. The field is a short label, not a chain of thought.

## What is deliberately not stored

- Hidden model reasoning or private chain-of-thought
- Environment variables and process secrets
- Raw credentials, tokens, or API keys that match the redaction patterns
- A second authoritative copy of memory in Markdown
- Unscoped embeddings that could be searched without a tenant id
