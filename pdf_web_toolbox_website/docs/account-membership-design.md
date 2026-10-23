# PDF Web Toolbox — Accounts, Cloud Templates and Membership Architecture

Status: **design only**. No user account, payment, or subscription is enabled by this document.

## 1. Rollout order and guards

1. **Current release**: no login required; original document bytes remain task-scoped; browser-local workflow templates only. Completed PDF and ZIP results can be inspected by an unguessable job ID until temporary cleanup.
2. **Identity and tenant isolation**: choose a production-grade managed identity provider; verify signed tokens on the backend. Avoid custom password storage. Every persistent table has an explicit immutable `user_id` owner and database-side access policy. Audit APIs, request limits, deletion/retention policy, and GDPR/PIPL implications based on operating jurisdiction.
3. **Cloud templates**: persist only validated, versioned workflow parameters and names in the account database. No source documents, screenshots, or blank-page previews in templates. Permit export/import and local-to-cloud opt-in migration, conflict handling, and full deletion.
4. **Entitlement layer**: a backend `EntitlementService` resolves an effective `plan_id`, per-job limits, monthly quotas, concurrent jobs, number of saved workflows, and feature access. It must be the only authority for permitting high-cost tasks. UI displays limits returned by the backend; it does not enforce them.
5. **Payments**: choose an authorized provider fitting the seller's country and merchant eligibility. Verify webhook signatures, dedupe provider event IDs, maintain an append-only ledger for grants/refunds, handle grace periods and chargebacks, and never unlock quotas from client-side redirects alone.
6. **Launch gate**: threat model, load tests, billing sandboxes, successful webhook/rollback tests, deletion/export procedures, error monitoring, transparent Terms/Privacy and actual payment settlement checks.

## 2. Suggested data model (not migrated)

- `users(id, identity_subject UNIQUE, created_at, status)`
- `workflow_templates(id, user_id, name, schema_version, settings_json, updated_at)`
- `plans(id, title, is_active, version)`
- `entitlements(user_id, plan_id, valid_from, valid_until, source_order_id)`
- `billing_orders(id, user_id, provider, provider_order_id UNIQUE, state, amount, currency)`
- `billing_events(id, provider_event_id UNIQUE, order_id, event_type, received_at)`
- `usage_ledger(id, user_id, job_id UNIQUE, quota_class, reserved, consumed, timestamp)`
- `jobs(id, user_id, status, created_at, expires_at, result_storage_key)`

Do not store full PDF text, input passwords, or document images in account tables. Establish explicit file TTL and per-user controls for cloud job result retention, with deletion verified in object storage.

## 3. Draft API contracts

- `GET /api/me` — verified user identity and effective limits.
- `GET/POST /api/me/templates`, `PATCH/DELETE /api/me/templates/{id}` — templates owned by the current account; schema validation, size and count limits.
- `GET /api/me/usage` — server-derived quota balance.
- `POST /api/billing/checkout` — authenticated session creation, provider-side reference and idempotency key.
- `POST /api/billing/webhook` — signed webhook ingestion and transaction-safe event deduplication.
- `POST /api/jobs` — validate parameters and source ownership; atomically reserve quota; refund reservation on failure or cancellation.

Anonymous use should remain possible for the free tools, with a *separate* resource pool and strict rate limits. Sharing a result link requires a short-lived, scoped signed token (not a raw storage key), and should be disabled by default.

## 4. Proposed nonbinding packaging

Free: limited basic operations, nonpersistent jobs, local templates.
Pro: higher workflow quotas and saved cloud templates.
Business: organizational use, batch capacity, private deployment/onboarding by agreement.

**No exact prices or quota numbers are fixed** until workload costs, conversion rates, provider fees, and local legal obligations are verified.

## 5. Security release blockers

Never ship billing before identity ownership, idempotent metering, webhook verification, tenant isolation, retention/deletion, anti-abuse and customer support flows are tested. Protected PDFs, signed documents, and personally identifying data need separate handling. The current in-memory job service is **not** suitable for a multi-replica paid service: move it to a transactional durable queue and durable job record store before offering paid SLAs.
