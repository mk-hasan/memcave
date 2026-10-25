# Security

memcave stores text that may be sensitive. Report vulnerabilities privately to the repository owner before opening a public issue.

## What this version protects

- Tenant and user filters on store queries
- Secret-pattern redaction before persistence and again when rendering queries
- An untrusted-memory fence around retrieved text
- Soft delete with hash suppression, and hard delete of one user's rows
- Path checks so Markdown export cannot leave the output directory
- Audit events that store query hashes and counts rather than raw erased content

## What remains your responsibility

Application-level isolation fails if a new query forgets the tenant predicate. PostgreSQL row-level security is not enabled. The redaction patterns miss new credential formats. A model can still follow text inside the fence. Do not store secrets you cannot afford to redact imperfectly. Do not treat the benchmark as a security certification.

See [docs/threat-model.md](docs/threat-model.md).
