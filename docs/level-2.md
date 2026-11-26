# Level 2 — Production auth with Amazon Cognito (+ path to deployment)

Level 1 (see CLAUDE.md) is frozen: the two-drivers-one-slot demo. Everything in this
file is **Level 2**. Every code, config or AWS change made for Level 2 is listed here.

## Goal of Level 2
Replace the email-only demo login with real authentication (Amazon Cognito,
passwordless email OTP), keep the Level 1 demo working offline, and prepare for deployment.

## Rules (in addition to the Level 1 architecture rules)
- Cognito answers "who is this person" (`sub`). Our DB answers "which driver is this"
  (`app_users`). Never store `driver_id` in Cognito.
- The API accepts only Cognito **access tokens** (`token_use == "access"`), never ID tokens.
- Identity still enters through one place: `current_driver_id()` in `api/deps.py`.
- `AUTH_MODE=local` keeps the Level 1 itsdangerous login for tests and offline demos.
  `AUTH_MODE=cognito` uses Cognito. Tests never call AWS.
- Region for everything: `ap-southeast-2` (Sydney). The account's SCP (`p-mi6wffpx`) denies all other regions.
- How to mark Level 2 work: commits are prefixed `feat(l2):` / `fix(l2):`, and each
  change gets a line in the change log below.

## Decisions
| # | Decision | Choice | Why |
|---|----------|--------|-----|
| D1 | Auth provider | Amazon Cognito (Essentials plan) | AWS-native, cheap, passwordless OTP built in |
| D2 | Login method | Passwordless email OTP (`USER_AUTH` + `EMAIL_OTP`) | Closest to Level 1 email login; drivers won't remember passwords |
| D3 | Keep Level 1 login | Yes, as `AUTH_MODE=local` | Tests and the scenario demo must run without AWS |
| D4 | Demo emails (`*.setuhaul.test` can't receive mail) | C: Cognito users get real inboxes; `app_users` links them by `cognito_sub`. The `.test` emails stay for `AUTH_MODE=local` | Email is only a login handle, not the identity key; no personal emails in git |
| D5 | Cognito threat protection | Deferred | Needs the Plus plan (no free tier, billed per MAU); mostly protects passwords, and we have none. OTP guessing is limited by Cognito; WAF rate rules at Step 14. Revisit at >100 drivers or on a client/compliance requirement |
| D6 | Passkeys (WebAuthn) | Deferred | Available on Essentials, but enrollment needs the browser WebAuthn API on an HTTPS domain; server-side Streamlit can't call it. Revisit with a real frontend + domain |

## Known constraints (from AWS docs)
- Email OTP needs the **Essentials** feature plan or higher, and **SES** email configuration.
- OTP sign-in does not work with **required MFA**. Leave MFA off; harden later with passkeys and threat protection.
- The app client must allow `ALLOW_USER_AUTH`. The pool's `SignInPolicy.AllowedFirstAuthFactors` must include `EMAIL_OTP`.
- SES sandbox: you can send only to verified addresses. Production needs a verified domain (DKIM/SPF/DMARC) and production access.

## Cost (checked 2026-11-26, AWS pricing pages)
- Cognito Essentials: **10,000 MAU/month free**, permanently (doesn't expire after 12 months), then $0.015 per MAU. The Plus plan has no free tier, so avoid it unless needed.
- Email OTP has no Cognito charge of its own; the emails are billed by SES: $0.10 per 1,000 (à la carte) or $0.16 per 1,000 (SES Essentials plan). New accounts get up to $200 in credits.
- Level 2 dev usage (2 drivers, a few hundred emails) ≈ $0. Set an AWS Budget alert anyway.

## Steps
| Step | What | Status |
|------|------|--------|
| 1 | Concepts: ID / access / refresh tokens, JWKS; choose login method | ✅ done |
| 2 | SES: verify the sender and driver inboxes in `ap-southeast-2` | ✅ done |
| 3 | Cognito user pool (Essentials, email OTP, self-signup off) + public app client | ✅ done |
| 4 | Create the two demo drivers as Cognito users | ✅ done |
| 4b | CLI OTP login (`initiate-auth` → `respond-to-auth-challenge`), decode the access token | ✅ done |
| 5 | Config: `AUTH_MODE`, `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID` | ✅ done |
| 6 | JWT verifier service (PyJWT + PyJWKClient): sig, `iss`, `exp`, `token_use`, `client_id` | ✅ done |
| 7 | `app_users.cognito_sub` column + map `sub` → `driver_id` (403 if not a driver) | ✅ done |
| 8 | Switch `current_driver_id()` by `AUTH_MODE` (8b: disable email-only `/login` in cognito mode; 8c: `POST /threads`) | ✅ done |
| 9 | Tests: fake RSA key + fake JWKS, no AWS calls | ✅ done |
| 10 | Streamlit OTP login (`InitiateAuth` → `RespondToAuthChallenge`). 10a `GET /auth/config` · 10b UI Cognito helper · 10c two-screen login | ✅ done |
| 11 | Token expiry, refresh, logout (revoke). 11a refresh on 401 · 11b revoke on logout | ✅ done |
| 12 | Hardening: 12a JWKS refetch cap · 12b rejection logging · 12c /chat rate limit · 12d console tightening (no SRP, 15-min tokens; PASSWORD factor can't be removed = accepted risk) · 12e threat protection + passkeys deferred (D5, D6) | ✅ done |
| 13 | Infrastructure as code (CDK Python) with one pool each for dev, staging and prod. 13a app + stack + synth ✅ · 13b bootstrap + deploy dev · 13c switch .env to the CDK pool, retire the hand-made one | ⏳ in progress |
| 14 | Deployment: HTTPS, Secrets Manager, hosting, SQLite → Postgres | ⬜ |

## Change log (every Level 2 change)
| Date | Step | Change | Where |
|------|------|--------|-------|
| 2026-11-26 | — | Created this doc; linked from CLAUDE.md and production-checklist.md | `docs/` |
| 2026-11-26 | 2 | Blocker: SES/Cognito AccessDenied by org SCP `p-mi6wffpx`. Cause: region lock, only `ap-southeast-2` allowed. Switched region from ap-south-1. | AWS account |
| 2026-11-26 | 2 | SES: 2 email identities verified (sandbox). D4 decided: option C | AWS SES, this doc |
| 2026-11-26 | 3 | User pool + app client created (Essentials, EMAIL_OTP, self-signup off, MFA off). Pending: switch email sending to SES | AWS Cognito |
| 2026-11-26 | 3 | Email sending switched to SES (`DEVELOPER`). Took two saves: the first saved "Send with Cognito" + custom FROM (found via CloudTrail) | AWS Cognito |
| 2026-11-26 | 4 | Created 2 admin users (email_verified=true, invite suppressed). Status CONFIRMED with no password (passwordless pool) | AWS Cognito |
| 2026-11-26 | 4b | First OTP login from the CLI worked. Access token claims: sub, iss, client_id, token_use=access, exp (60 min), amr=[email_otp]. No email in the access token | — |
| 2026-11-26 | 5 | Settings: `auth_mode` (Literal, default local), `cognito_region/user_pool_id/client_id`, `cognito_issuer` / `cognito_jwks_url` properties, startup validator (missing/blank fields, region prefix). Added `hide_input_in_errors=True` after finding the LLM API key in validation error output | `config.py`, `.env.example` |
| 2026-11-26 | 6 | `verify_access_token()`: RS256 pinned, iss, exp (30s leeway), required claims, token_use=access, client_id. `AuthUnavailable` for JWKS fetch failures (→ 503). Probed: alg=none, HS256 confusion, forged key, ID token, other client, wrong issuer, expired, missing claims, all rejected. Added `pyjwt[crypto]` | `services/cognito_auth.py`, `pyproject.toml` |
| 2026-11-26 | 7a | `app_users.cognito_sub TEXT UNIQUE` (nullable; UNIQUE = one Cognito user can't map to two drivers). Scenario YAML has an optional `cognito_sub: UUID` per user; loader stores it as text | `app_schema.sql`, `db/scenario.py`, scenario YAML, `load_scenario.py` |
| 2026-11-26 | 7b | `queries.driver_for_cognito_sub()`; `driver_id_for_access_token()` = verify token → sub → ACTIVE driver, else `NotADriver` (→ 403). ACTIVE re-checked on every request (instant lockout) | `db/queries.py`, `services/cognito_auth.py` |
| 2026-11-26 | 8 | `current_driver_id()` reads AUTH_MODE per request. Errors: InvalidSession→401 (+`WWW-Authenticate: Bearer`), NotADriver→403, AuthUnavailable→503 (+`Retry-After: 30`, generic message). Shares the request's DB connection via FastAPI's dependency cache | `api/deps.py` |
| 2026-11-26 | 8b | `/login` returns 404 unless AUTH_MODE=local (fail closed; same answer for real and fake emails, so no enumeration) | `api/main.py` |
| 2026-11-26 | 8c | `POST /threads` (201, authenticated, no body) → `{thread_id, driver_name}`; works in both modes. `identity.driver_for_session()` (missing driver → 401) | `api/main.py`, `api/schemas.py`, `services/identity.py` |
| 2026-11-26 | 9a | `tests/test_cognito_auth.py`: own RSA key pair plays Cognito, `FakeJWKS` replaces `_jwks_client`. 16 tests: valid, leeway edge, 12 parametrized attacks, expired message, JWKS down → AuthUnavailable. Total 38 passed | `tests/` |
| 2026-11-26 | 9b | 6 API tests through TestClient on a temp scenario DB: linked → 201, unlinked → 403, suspended mid-session → 403, forged → 401 + `WWW-Authenticate`, JWKS down → 503 + `Retry-After`, `/login` → 404. Total 44 passed | `tests/test_cognito_auth.py` |
| 2026-11-26 | 10a | `GET /auth/config` (unauthenticated) → `{mode, region, client_id}`; public values only, explicit allow-list (no pool id, no secrets). 2 tests with exact-match responses. Total 46 passed | `api/main.py`, `api/schemas.py`, tests |
| 2026-11-26 | 10b | `ui/cognito_login.py`: `CognitoLogin.start()/verify()`, unsigned boto3 (UI server holds no AWS creds), explicit timeouts/retries, AWS error codes → safe messages. Unknown email → `session=None` (pretend a code was sent; no enumeration in the UI). Added `boto3` | `ui/`, `pyproject.toml` |
| 2026-11-26 | 10c | Streamlit: mode from `/auth/config` (cached 5 min), shared `CognitoLogin` via `cache_resource`, screen 1 email → screen 2 code → `POST /threads` → chat. 403 → "contact ops". Tokens stored only after the API accepts them. **First real browser OTP login worked** (DRV006 chatted on a Cognito access token) | `ui/app.py` |
| 2026-11-27 | 11a | `CognitoLogin.refresh()` (REFRESH_TOKEN_AUTH, unsigned). UI `authed()`: on 401 refresh once + retry, else logout; used by `/history` and `/chat` (retry safe thanks to `client_msg_id` idempotency). Verified with AppTest: 4 cases | `ui/` |
| 2026-11-27 | 11b | `CognitoLogin.revoke()` (RevokeToken, unsigned, best effort: failures only logged). UI `logout()` revokes the refresh token, then clears the session. Note: access tokens stay valid at our API until `exp` (stateless JWT); see Step 12. Verified with AppTest against `ui/app.py`: logout revokes; failed refresh → logout + revoke | `ui/` |
| 2026-11-27 | 12a | JWKS refetch cap: `cooldown_duration=30` made explicit (PyJWT ≥2.15 default). Probe: 1000 junk-`kid` tokens → 2 JWKS fetches (1001 with cooldown 0). Guard test fakes only `urllib` so the real PyJWKClient caching runs; also asserts real tokens still pass. Total 47 passed | `services/cognito_auth.py`, tests |
| 2026-11-27 | 12b | Rejection reasons logged (`_reject()`: warning with exception class or fixed reason; expired = info; JWKS down = error; NotADriver logs the verified `sub`). Caller still gets a vague "Invalid session". Never logs the token (tested with caplog). Total 50 passed | `services/cognito_auth.py`, tests |
| 2026-11-27 | 12c | `/chat` rate limit per driver: `RateLimiter` (in-memory sliding window, monotonic clock, thread lock, blocked hits not counted). `chat_driver_id` dependency runs after auth → 429 + `Retry-After`. Config `CHAT_RATE_LIMIT=10` / `CHAT_RATE_WINDOW_SECONDS=60`. OTP-request limits stay with Cognito + WAF (our API never sees them). Per-process only; Redis/API Gateway at Step 14. Total 55 passed | `services/rate_limit.py`, `api/deps.py`, `api/main.py`, `config.py`, tests |
| 2026-11-27 | 12d | Console: app client flows now `ALLOW_REFRESH_TOKEN_AUTH` + `ALLOW_USER_AUTH` (removed `ALLOW_USER_SRP_AUTH`); access + ID tokens 60 → 15 min (refresh 5 days, revocation on, unchanged; verified via CLI). `PASSWORD` first factor can't be removed (console: "always available"). Probe: unknown email + `PREFERRED_CHALLENGE=EMAIL_OTP` now gets a fake `EMAIL_OTP` challenge, so the UI can't tell users apart. Remaining signal: raw `AvailableChallenges` (unknown = PASSWORD_SRP, PASSWORD, EMAIL_OTP; real = EMAIL_OTP). **Accepted risk**: users have no passwords; mitigate with WAF rate rules (Step 14). Note: an unprompted probe of a real user sends them an OTP email | AWS Cognito |
| 2026-11-27 | 12e | Decision D5/D6: threat protection and passkeys deferred (see Decisions). Step 12 complete | this doc |
| 2026-11-27 | 13a | CDK app (Python, aws-cdk-lib 2.278, `infra` uv dependency group so the API never ships CDK). `AuthStack(env_name)`: Essentials pool, admin-only, email alias, EMAIL_OTP (+ mandatory PASSWORD), MFA off, email-only recovery, SES sender from `SES_FROM_EMAIL` env (not in git); client USER_AUTH + refresh only, no secret, OAuth off, 15-min tokens, revocation on. Prod: deletion protection + RETAIN; dev/staging: DESTROY. Outputs = the 3 `COGNITO_*` values. 4 template tests (`uv run --group infra pytest infra/tests`). `cdk synth` OK; nothing deployed | `infra/`, `pyproject.toml`, `.gitignore` |

## Demo driver ↔ Cognito link (Level 2, dev pool)
Real inboxes are kept out of git; see the Cognito console. Step 7 writes these into `app_users.cognito_sub`.

| driver_id | Driver | Cognito `sub` |
|-----------|--------|---------------|
| DRV006 | Manoj Sharma | `39fed408-50d1-70d2-4757-8061ba371968` |
| DRV012 | Arjun Das | `392ec498-50b1-7060-2970-3b51f9d9169d` |

## AWS resources created (for cost tracking and teardown)
| Resource | Name / ID | Region | Created |
|----------|-----------|--------|---------|
| SES email identities | 2 verified Gmail inboxes (sender + driver logins) | ap-southeast-2 | Step 2 |
| Cognito user pool | `ap-southeast-2_2ZaRmCWOi` ("User pool - larou1"), Essentials, deletion protection on | ap-southeast-2 | Step 3 |
| Cognito users | 2 demo drivers (see table above) | ap-southeast-2 | Step 4 |
| Cognito app client | `71086m73mar3bvpe630un61b34` (`setuhaul-front`), public, no secret | ap-southeast-2 | Step 3 |
