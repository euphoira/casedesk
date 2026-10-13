# Security boundaries

CaseDesk is a local, single-user demonstration. Run one worker on `127.0.0.1`; the CLI uses
this bind address by default. Host/origin checks and body bounds do not authenticate a user.
A local process can call the API, so human approvals are not identity-attested signatures.

- All accounts/orders are synthetic. Effects are local SQLite records only. No real refund,
  coupon delivery, message sending or customer contact capability exists.
- Model tools can only search the packaged knowledge and read the current ticket. They cannot
  upload/read arbitrary files, modify account facts, execute code or call business APIs.
- Ticket intake selects a server-owned synthetic context. Client amounts and arbitrary
  action parameters are rejected. Deterministic policy recomputes eligibility and parameters.
- Human approval is required separately. Policy denial, stale ticket/policy/corpus versions,
  a replaced analysis or conflicting idempotency key blocks effects. Idempotent replay returns
  an already committed effect, rather than executing the business operation again.
- Quote validation establishes retrieved source provenance, not semantic truth. A model can
  still misclassify a ticket or write an unsupported draft; human review remains necessary.
  Ticket category is human-supplied metadata, not a security-intent classifier.
- Live mode sends ticket text, synthetic context and retrieved knowledge to the configured
  provider. Do not enter private data or credentials into this demo without appropriate consent.
- Provider errors omit raw server bodies and credentials. The public config response omits
  API keys and endpoint URLs. `.env` is excluded from version control.
- SQLite stores ticket bodies, retrieved text and audit data unencrypted in the configured
  data directory (default `.casedesk/`). Protect or remove the actual configured directory
  according to local requirements. The audit is not tamper-proof against local disk access.
- Do not expose the app to untrusted networks. No role-based access control, abuse protection,
  external compliance guarantee or production migration/reconciliation system is included.

Report suspected vulnerabilities without real customer data or credentials. Use the repository
owner's private security reporting channel when enabled; otherwise request a private contact
route in a minimal public issue.
