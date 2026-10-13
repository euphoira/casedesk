# CaseDesk HTTP contract

Local single-user support inbox. Every action is a **local simulation**; no payments,
messages, or external account changes occur. Bind 127.0.0.1:8012. JSON uses snake_case.

- GET `/api/config` → `{mode:'demo'|'live',model,simulation:true,corpus_revision,policy_revision}`.
- GET `/api/tickets` → Ticket array.
- GET `/api/contexts` → `{id,label,account_id,order_id,account,order}[]`, five synthetic
  server-owned account/order contexts. IDs are ORDER-01 through ORDER-05.
- POST `/api/tickets` body `{subject,body,category,context_id}` → Ticket, 201. Subject 3..160,
  body 10..4000 chars; category billing/shipping/account/other. Maximum 200 local tickets.
  Account and order facts are copied from the chosen context; client amounts are forbidden.
- GET `/api/tickets/{id}` → Ticket.
- POST `/api/tickets/{id}/analyze` → Analysis, synchronous await (can take up to 6 model calls).
- GET `/api/tickets/{id}/analysis` → latest Analysis, or 404.
- GET `/api/tickets/{id}/audit` → array `{id,ticket_id,event,data,created_at}`.
- GET `/api/search?q=...` → `{revision,chunks:[Chunk]}` (ranked, max 5).
- POST `/api/proposals/{id}/approve` body `{ticket_revision:int,idempotency_key:str}` →
  `{proposal:Proposal,effect:Effect,replayed:bool}`. Conflict/stale/state violation:409;
  policy denied:403. Key is 8..100 chars. Retry same approval with same key is idempotent.
- POST `/api/proposals/{id}/reject` body `{ticket_revision:int}` → Proposal.
- PATCH `/api/tickets/{id}` body `{expected_revision:int,status:'open'|'escalated'|'closed'}`
  → updated Ticket. Every update increments revision; stale revisions:409.
- GET `/health` → `{status:'ok'}`.

Ticket = `{id,subject,body,category,account_id,order_id,revision,status,created_at,
account:{tier,verified},order:{amount_cents,days_since_purchase,delivered,refunded}}`.
Server-owned account/order facts are never editable through the API.

Chunk = `{id,document_id,title,revision,text,score}`.
Analysis = `{id,ticket_id,ticket_revision,status:'running'|'completed'|'failed',mode,classification,
draft,confidence:'low'|'medium'|'high',citations:[Citation],retrieved:[Chunk],
proposal:Proposal|null,limitations:[str],error:null|str,model_calls,created_at}`.
Citation = `{chunk_id,revision,quote}`; quotes must be exact substrings of retrieved chunks.
Proposal = `{id,ticket_id,ticket_revision,analysis_id,action:'refund'|'coupon'|'request_info'|'escalate',
parameters:dict,status:'pending'|'approved'|'rejected',policy:PolicyDecision,created_at}`.
PolicyDecision = `{allowed:bool,reason,policy_revision,normalized_parameters:dict}`.
Effect = `{id,proposal_id,ticket_id,action,parameters:dict,simulation:true,created_at}`.

Analyze only writes a draft/proposal. Approval rechecks ticket revision, policy revision,
corpus revision and current server-owned facts inside an atomic SQLite transaction.
After approval, ticket revision increments; effect parameters come from deterministic policy.
Analysis failure returns 200 with status failed and sanitized error. No pending proposal is
created on provider/protocol/evidence failure. Existing pending proposals become stale after
new analysis (analysis ownership check). JSON errors use `{detail:string}`.
