# Memory lifecycle

1. `start_session` records a tenant, a user, and a session.
2. `append_event` appends an episodic event. Unambiguous explicit instructions also write a derived memory immediately. Other messages wait.
3. `end_session` closes the session so dreamflow can select it.
4. `run_dreamflow` extracts evidence-backed candidates, validates them, and writes, merges, or versions memories. A second successful run with the same sessions does not duplicate them.
5. `search_memory` returns hybrid hits and an untrusted context block.
6. `update_memory` requires the latest version number. A conflicting update raises `VersionConflictError` instead of a silent overwrite.
7. `delete_memory` soft-deletes and keeps the content hash so dreamflow will not recreate it. A later explicit remember may.
8. `export_markdown` writes `memory.md` and one file per active memory under the output directory.
9. `erase_user_data` hard-deletes that user's rows in that tenant. The command-line form is a dry run until `--yes`.

Episodic events stay append-only except for erasure. Derived memories are versioned. Candidates below the review threshold stay candidates and are omitted from the default search.
