# Contributing to CaseDesk

CaseDesk is a local support-workflow reference implementation. All business effects are
simulations, which makes approval and failure behavior reproducible without external accounts.

## Development

Use Python 3.11 or newer in a dedicated virtual environment. From the repository root:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
casedesk eval
```

Run `casedesk serve` to inspect the inbox. Follow README instructions for your shell.
Offline demos and tests need no API key. Exercise live-provider protocol changes with
`httpx` mocks; do not introduce network-dependent or paid CI tests.

## Changes worth contributing

- Add bilingual retrieval fixtures and explain the expected policy evidence.
- Add a business-policy boundary case with server-owned account and order facts.
- Improve draft review, rejection, audit history, or conflict feedback in the interface.
- Add a regression test for stale revisions, duplicate requests, or concurrent approval.

Policy authorization must remain deterministic and separate from model proposals.
Approval must revalidate evidence and revisions in the same transaction as the local
effect. Do not replace the simulated executor with real payments or outbound messages as
part of an unrelated change.

Use synthetic tickets when reporting issues. Never include credentials, private customer
messages, `.env` files, or local SQLite databases in issues or pull requests. The project
uses the MIT license; contributions are provided under the same license.
