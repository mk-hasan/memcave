# API stubs

These are the public coroutine methods on `MemoryManager`. Requests and responses are Pydantic models in `memcave.domain.api`.

| Method | Purpose |
| --- | --- |
| `start_session` | Open a session for a tenant and user |
| `append_event` | Append an episodic event and apply an explicit instruction when one is present |
| `end_session` | Close a session |
| `list_sessions` | Page sessions |
| `list_events` | Page events |
| `search_memory` | Hybrid search with a fenced context block |
| `fetch_memory` | Read one memory, optionally at a version or time |
| `add_memory` | Create an explicit memory |
| `update_memory` | Version a memory when `expected_version` matches |
| `delete_memory` | Soft-delete and retain the content hash |
| `get_memory_index` | Compact Markdown index of active memories |
| `run_dreamflow` | Run the dreamflow pipeline |
| `export_markdown` | Write the index and one file per memory |
| `erase_user_data` | Hard-delete one user's data in one tenant |

Errors: `MemcaveError`, `NotFoundError`, `TenantIsolationError`, `ScopeError`, `VersionConflictError`, `PolicyRejectedError`, `ValidationError`, `ProviderError`, `DreamflowError`.

Composition: `in_memory_manager()` and `build_manager(Settings)`. Core modules do not import adapters. Adapters are loaded from `memcave.bootstrap` and the CLI.
