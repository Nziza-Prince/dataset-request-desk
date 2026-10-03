# Engineering notes

## Model and difficult decisions

`User → DatasetRequest → StatusEvent`; `Robot → Episode → Assignment → DatasetRequest`. An assignment's episode is its primary key, enforcing exclusive use. Foreign keys protect referenced records; counts, qualities and statuses have checks. Status is stored on the request; audit events are appended in the same transaction, including submission. Episode metadata and request specifications are immutable in this version.

**SQLite versus PostgreSQL.** I chose SQLite to keep the reviewer setup to one container image and one persistent volume. Every domain mutation starts an `IMMEDIATE` transaction **before** reading current state. SQLite serializes writers; I do not rely on `select_for_update()`, which does nothing on SQLite. Database triggers additionally reject invalid state edges, underfilled delivery, mismatched/bad assignments, edits to frozen assignments, and metadata changes that could invalidate them. Role checks and audit creation belong to the service layer: a person with direct SQL access can bypass actor authorization or insert misleading audit records. This is not a tamper-proof ledger. Porting requires PostgreSQL equivalents for the triggers, request/episode row locks in a consistent order, and rerunning concurrency tests; swapping the database URL alone is insufficient.

**What counts as fulfilment?** Episodes must match the normalized task exactly; otherwise a cup request could be fulfilled with towels. Assignment/removal is allowed only during `in_progress`. Delivered and accepted contents remain frozen; rejected contents remain reserved until an operator reopens work. Accepted assignments remain reserved indefinitely because the brief does not define a release lifecycle. Delivery requires at least the requested count, matching the brief. Extra clips are allowed and clearly labelled in the UI because the client may benefit from additional valid examples. A client can cancel a `submitted` request, or an `in_progress` request with no selected clips, with a reason; a cancelled request is final and remains in the audit history. Once a clip is selected, cancellation is blocked so reserved inventory is not silently released. Admin inherits operator powers, not the right to accept or cancel on a client's behalf. Requests need a positive integer count and a deadline today or later (Kigali local date); existing requests can become overdue. Clients with requests cannot change role, preserving ownership semantics; deactivation is available. Admins cannot demote/deactivate themselves. There is no request editing or user deletion.

**Large requests and feasibility.** The broad upper bound of one million protects the database from absurd numeric values but is not a promise that a request can be fulfilled. Rejecting large requests outright would block legitimate bulk work, so the UI calculates a live inventory signal instead: clips already assigned to the request plus currently unassigned matching `good`/`usable` clips. An active request with a shortfall is marked “Needs feasibility review” in both the client view and operator queue. It does not invent a new workflow status, because feasibility can change as operators reserve clips or import new recordings. This is an inventory snapshot, not a prediction of future collection capacity.

**Requestable actions.** Clients choose from normalized task names represented by at least one imported `good` or `usable` episode. The choices are generated from the database, and the same form validation rejects forged UI or API values. Existing assignments do not remove an action from the list; feasibility is reported separately because an action may remain supported even when all current clips are reserved. An empty database accepts a task through the API to avoid making initial bootstrap impossible, while the UI has no selectable action until an eligible episode is imported.

**Rework feedback.** A client must explain a rejection in up to 2,000 characters. The text is stored on the status event that records the rejection and is visible in the request activity feed. This avoids a separate comment system while giving the operator a concrete next action. The database protects transition edges and delivery count; feedback content and actor authorization remain service-layer responsibilities because this SQLite trigger layer is not intended to police privileged direct SQL.

**Conflicting imports.** Canonical episode ID is the identity. First valid occurrence wins, including previously committed imports; identical repeats are skipped, conflicting repeats are reported without updates. File order therefore matters for new conflicting IDs. This favors preserving known data over guessing which export row is newer. A correction workflow needs provenance/versioning and is deliberately absent. Two concurrent imports share the same serialization and uniqueness rules. A killed run may have committed earlier batches; rerunning is safe, not an all-file rollback.

## Every seed anomaly

| Case | Decision |
|---|---|
| Exact repeats `EP-00074`, `EP-00030` | Skip as duplicates. |
| `EP-00011` bad then good | Keep bad; report conflicting duplicate. |
| `ep-00003` with different metadata | Uppercase IDs; preserve original `EP-00003`, report conflict. |
| Blank episode ID | Reject; never manufacture identity. |
| Task casing/spacing (`EP-00006/7`), robot whitespace (`8`), quality casing (`9/10`) | Trim fields; collapse/casefold tasks; lowercase robot and quality. |
| Space-separated ISO (`13`), day-first (`14`), `Z` (`15`) | Parse explicit formats; naive dates are assumed UTC, offsets converted to UTC. |
| Invalid date (`23`), future recording (`25`, year 2031) | Reject; future checks use import time. A future row may become eligible years later, but can never duplicate an existing ID. |
| Empty/negative duration (`16/17`), `N/A` (`90003`), huge duration (`90004`) | Reject non-finite values and durations outside `(0, 3600]` seconds. The one-hour bound is an explicit short-clip assumption needing domain confirmation. |
| Fractional duration (`18`) | Preserve `45.5`; do not round. |
| Blank/unknown quality (`19/20`) | Reject; no guessed quality. |
| Missing/unknown robot (`21/24`) | Reject against the supplied five-robot registry; do not create robots implicitly. |
| Short row (`90001`) | Reject wrong column count; report physical line number. |
| Quoted comma in task (`90002`) | Preserve with the CSV parser. |
| Blank operator (`90005`) | Accept an empty historical label; it is not an authenticated user identity. |
| Final blank/whitespace lines | Count and report as skipped blank rows. |

Result at the test's fixed 2026-10-03 clock: 173 inserted, 18 skipped. A repeat inserts zero. Tests compare all stored episode fields before/after, not just row counts.

## Problems actually encountered

The first logging test failed although stderr visibly contained the correct JSON. The application logger intentionally disabled propagation, so pytest's root capture handler saw nothing. I enabled propagation only in that test and asserted the actual fields; production logging remains single-output. During startup review, I found that importing as the seed operator on every restart would fail after legitimate deactivation. Bootstrap now imports only when creating that operator, and a regression test checks password/role/deactivation preservation. After adding cancellation, the request list initially raised a 500 because its status-to-guidance mapping did not include `cancelled`; I added the missing client/operator text and a regression test that renders every request status for both roles. Analytics dates also initially rendered as unrestricted text fields and hid field-level parsing errors. They now use native calendar inputs and display invalid-date errors. The browser runtime download timed out; an already-installed Chrome binary was available and was used instead. These are development findings, not claims of production incidents.

## Security and limits

Django hashes passwords with PBKDF2, stores sessions server-side, rotates the session/CSRF token at login, checks current roles on requests, and rejects inactive sessions. Mutations recheck role/activation under the write lock. CSRF covers login and all writes; cookies are HttpOnly/SameSite, templates escape input, SQL is parameterized, and a restrictive CSP disallows inline scripts. Inputs, import bodies, pagination and diagnostics are bounded. Login attempts are throttled per normalized account in the database (10/15 minutes). This is basic protection, susceptible to deliberate account lockout and distributed attempts across many accounts; it is not a replacement for ingress controls or SSO.

The two vulnerabilities I would most worry about are **cross-client authorization mistakes** and **session/account takeover**. Ownership tests include detail/history/assignment routes and forged mutations. Session tests exercise CSRF, deactivation and immediate role changes. Public hosting additionally needs HTTPS, secure cookies, non-demo secrets/accounts and operational rate limits. The supplied short passwords are accepted only by demo seeding; newly created users must pass password validation. DB/CLI access is privileged. No password reset, MFA, SSO, or complete administrator audit log is provided.

## Scope, scale, next steps, and AI

No stretch item: time went into invariants, import policy and verification. No SPA, queue, real-time updates, video storage/export, full audit of assignment removals, or admin user-management screen. Operators can upload a small CSV and inspect its report in the UI; larger files remain a streaming CLI operation. The activity feed is paginated in the UI and API. Analytics comprise four database queries and are an operational report, not a cross-query point-in-time snapshot.

At 10× users, SQLite writer contention and synchronous imports are the first likely bottlenecks; lock timeouts return retryable errors. At 100× episodes, broad analytics scans, UI offsets and growing audit history matter. With two more days I would prioritize PostgreSQL with contention tests, measured query plans and representative load tests, an approved metadata-correction/release policy, backups/restore checks, and administrator change auditing. Only after measurements would I add rollups or a background import queue. No volume benchmark or production readiness is claimed.

OpenAI Codex was used to inspect the complete brief/seed data, choose and implement the design, write tests/documentation, run Docker and browser checks, and perform the final review. Agent-browser controlled local Chrome; official Django/HTMX documentation was consulted. The repository was generated with substantial AI assistance, not represented as independently hand-written candidate work. The submitter should review and be able to explain the transaction mode, SQL median, trigger boundaries and conflict policy before an interview. No claim is made here about a particular commit sequence.
