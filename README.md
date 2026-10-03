# Dataset Request Desk

A small internal request desk built with Python/Django, SQLite, and server-rendered templates with HTMX filtering. The API and UI share workflow services. No Node build, external API, Redis, or background worker is needed.

Implemented: client ownership, operator workflow, administrator user management through the API, audited status changes, exclusive assignments, CSV import, SQL analytics, structured request logging, migrations, and tests. No optional stretch item was selected. See [NOTES.md](NOTES.md) for decisions and limitations.

## Run from a clean clone

Requires Docker with Compose:

```sh
docker compose up --build
```

Open **http://localhost:8000**. First startup migrates the database, hashes the supplied demo passwords, and imports the supplied CSV. SQLite lives in the persistent `desk-data` volume. The container runs as an unprivileged user with two Gunicorn workers; the port binds only to localhost. Dependencies and HTMX are pinned; static assets are served locally.

| Email | Password | Role |
|---|---|---|
| admin@example.com | admin123 | admin |
| ops1@example.com | ops123 | operator |
| ops2@example.com | ops123 | operator |
| client-a@example.com | client123 | client |
| client-b@example.com | client123 | client |

These credentials and the default secret are **for local review only**. Account seeding never resets passwords, roles, or activation state. Demo episodes load when `ops1` is first created; ordinary restarts do not reimport them.

Without Docker, with Python 3.12+ and `venv` available:

```sh
sh scripts/dev.sh
```

This installs pinned dependencies into `.venv`, migrates/seeds a local database in `data/`, collects static assets, and starts Gunicorn. Tested on Python 3.13 in Docker and 3.14 locally.

Stop with `docker compose down`; data is preserved. **`docker compose down -v` deletes the review database.** Use that only for an intentional fresh start. Don't run multiple startup/migration processes against the same database simultaneously.

## Tests

One command after building the image:

```sh
docker compose run --rm web sh scripts/test.sh
```

Or after local setup:

```sh
sh scripts/test.sh
```

Tests use a separate database and don't modify demo data. They cover ownership and role changes, real CSRF enforcement, login throttling, the rework cycle, audit rollback, delivery thresholds, bad/mismatched episodes, database guards, seed preservation, actual messy-file idempotency, SQL median boundaries, pagination, and HTML flows. A separate process exercises concurrent writes with committed SQLite transactions and independent connections, rather than relying on an in-memory database or mocked locks. GitHub Actions runs the suite and checks for missing migrations.

Verification is recorded after each implementation change by the test command above. The suite includes the complete request cycle, extra-clip delivery, rejection feedback, import reporting, authorization, and concurrent database writes. These checks do not constitute a production load test.

## Try the workflow

1. Sign in as client A and choose `pick cup` from the actions found in the imported clip library, request one episode, and select a future deadline.
2. Sign out and sign in as an operator. Open the request, **Start selecting clips**, filter matching available episodes, and **Add clip**. The request can contain extra clips: the screen shows selected, required, and extra counts. It can be sent once it has at least the requested number.
3. Sign in as client A to accept or request changes. A short explanation is required when requesting changes; it appears in the activity timeline for the operator. The operator can reopen work, change the selection, and redeliver. Client B cannot see or act on A's request.
4. A client can cancel a request with a reason until clips have been selected. This includes an in-progress request that an operator has opened but not yet worked on. The cancelled request remains in history and offers **Create revised request**, pre-filled with its original details.

The request detail activity explains each step in plain language. Operators also have **Import clips** and **Analytics** in the top navigation. The import screen shows row-level outcomes; the analytics screen is a compact view of the required database reports. User administration remains API-only.

Large requests are accepted rather than silently capped. Before work starts, the request and operator queue compare its requested count with matching, unreserved `good`/`usable` clips plus clips already selected for that request. A shortfall is labelled **Needs feasibility review** so an operator can agree a revised scope or deadline with the client. This is an inventory signal, not a promise about future recording capacity.

## API

JSON endpoints use Django's server-side session cookie and CSRF protection. Obtain a token before login; login rotates it. Send the returned token as `X-CSRFToken` on every POST/PATCH/DELETE, with the session cookie. JSON bodies require `Content-Type: application/json`; imports require `text/csv`. Browser forms use the same CSRF/session mechanism.

| Method and path | Purpose / access |
|---|---|
| `GET /api/csrf/` | Public token bootstrap for login |
| `POST /api/login/` | `{email, password}` → user and new CSRF token |
| `POST /api/logout/`, `GET /api/me/` | Current session |
| `GET /api/requests/` | Own requests for clients; all for operators/admins |
| `POST /api/requests/` | Client only: `{task_name, episodes_requested, deadline, notes?}` |
| `GET /api/requests/{id}/` | Request and assignment count |
| `POST /api/requests/{id}/transition/` | `{status, feedback?}`; feedback is required for `rejected` and `cancelled`; roles and transition checked server-side |
| `GET /api/requests/{id}/events/` | Audited history within the same ownership boundary |
| `GET /api/requests/{id}/assignments/` | Assignments within the same ownership boundary |
| `POST /api/requests/{id}/assignments/` | Operator/admin: `{episode_id}` |
| `DELETE /api/requests/{id}/assignments/{episode_id}/` | Operator/admin: remove while in progress |
| `GET /api/episodes/?task_name=pick%20cup&quality=good` | Operator/admin: eligible unassigned episodes |
| `POST /api/episodes/import/` | Operator/admin: raw UTF-8 CSV, maximum 2 MiB |
| `GET /api/analytics/?start=2026-08-01&end=2026-09-30` | Operator/admin analytics |
| `GET/POST /api/users/` | Admin: list/create; creation fields `email, name, password, role, organisation?` |
| `PATCH /api/users/{id}/` | Admin: `{role?, is_active?}`; boolean activation, no deletion |
| `GET /health` | Authenticated database/schema probe |

List endpoints return `{results, next_after}` (50 entries); pass `?after=<next_after>` for the next page. UI tables use 25 entries per page. API errors return `{error: ...}`: 400 validation, 401 unauthenticated, 403 forbidden/CSRF, 404 inaccessible/missing object, 409 domain conflict, 413 oversized input, 415 wrong content type, 429 login throttle, or 503 database busy with `Retry-After`. Stale transition commands return 409; retrying an identical assignment is harmless.

Example using curl and Python for JSON extraction (demo credentials only):

```sh
csrf=$(curl -fsS -c /tmp/desk-cookies http://localhost:8000/api/csrf/ \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')
csrf=$(curl -fsS -b /tmp/desk-cookies -c /tmp/desk-cookies \
  -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' \
  -d '{"email":"ops1@example.com","password":"ops123"}' \
  http://localhost:8000/api/login/ \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')
curl -fsS -b /tmp/desk-cookies \
  'http://localhost:8000/api/analytics/?start=2026-08-01&end=2026-09-30'
curl -fsS -b /tmp/desk-cookies -H "X-CSRFToken: $csrf" \
  -H 'Content-Type: text/csv' --data-binary @seed/episodes.csv \
  http://localhost:8000/api/episodes/import/
```

## Import and analytics semantics

For large files, use the streaming CLI (host/database access is trusted; the actor must still be an active operator/admin):

```sh
docker compose exec web python manage.py import_episodes seed/episodes.csv --actor ops1@example.com
# Local alternative:
.venv/bin/python manage.py import_episodes seed/episodes.csv --actor ops1@example.com
```

The supplied file initially produces **173 imported, 18 skipped** with an October 2026 clock. Reimport produces **0 imported, 191 skipped** and leaves all episode metadata unchanged. Each report includes counts by reason and physical CSV line numbers. Full counts are retained; per-row diagnostics are capped at 1,000. The importer holds at most 500 parsed rows plus those diagnostics, uses bulk lookups/inserts, and commits each batch atomically. Interrupted imports can be rerun. Invalid headers fail before writes; malformed quoting stops further reading and reports partial progress. Details and policy for every messy-data case are in NOTES.

Analytics use **inclusive UTC dates** (implemented as half-open timestamp bounds), up to 367 days:

- Episode counts and top five good tasks filter by `recorded_at`. Days/robots with no episodes are omitted. Ties in the top five are resolved alphabetically.
- Status counts use requests **submitted in the range**, with their current statuses.
- Median uses that submission cohort's **first delivery**, even if it happened outside the range. Undelivered requests are excluded; an empty cohort returns `null`; rework deliveries aren't counted again. The SQL window calculation averages both middle values for even cohorts. Seconds may have minor SQLite floating-point error.

At **5 million episodes**, timestamp indexes restrict narrow ranges, the partial good-episode index supports top tasks, and aggregation stays in SQL. A year-wide grouping still scans/sorts a large result set; this is not constant-time analytics. Move to PostgreSQL, inspect `EXPLAIN ANALYZE` on representative data, and add daily rollups when measured latency justifies them. SQLite's single writer will constrain import and operator throughput before Python memory does. The API uses keyset pagination; UI offset pagination and count queries are intentionally simpler and should also be revisited at volume. See NOTES for transaction/trigger migration implications. No five-million-row benchmark is claimed.

## Operations and code map

`docker compose logs -f web` includes one JSON line per request with method, path (without query strings), HTTP status, duration, and authenticated user ID. Credentials, cookies, and bodies are not logged. `/health` requires authentication because the brief makes login the sole public application action; CSRF bootstrap and static assets are necessary login resources.

Configuration: `DATABASE_PATH`, `SECRET_KEY`, `ALLOWED_HOSTS`, `COOKIE_SECURE=1`, `DEBUG` (off by default), and `SEED_DEMO` (startup script). For an actual hosted environment, use a random secret, disable demo seeding, provision real users, terminate HTTPS with secure cookies, configure trusted origins/proxy handling deliberately, and add backups and ingress rate limits. The local configuration intentionally fails Django's HTTPS deployment checks; it is not a public deployment.

- `desk/models.py`, `desk/migrations/`: relational schema and database guards.
- `desk/services.py`: authorized transactional domain mutations.
- `desk/importing.py`, `desk/analytics.py`: bounded import and database aggregation.
- `desk/api.py`, `desk/views.py`: JSON/HTML adapters; no separate frontend business logic.
- `tests/`: behavioral, database, and concurrency tests.

SQLite `IMMEDIATE` transaction behavior follows the [Django database documentation](https://docs.djangoproject.com/en/5.2/ref/databases/#transactions-behavior). HTMX 2.0.8 is vendored with its license; normal forms continue to work without JavaScript.
