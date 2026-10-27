---
name: cybersecurity-audit
description: Use this skill any time the user asks to audit, harden, secure, pentest-lite, or check the security of a website, app, API, or codebase — including phrases like "check for vulnerabilities," "is this secure," "security review before launch," "can this be hacked," "audit our auth," "check our database security," or "are we exposed." Also trigger it proactively before a production launch, after adding auth/payments/file-uploads, or when new third-party integrations are added. This skill turns the agent into a senior application-security engineer — not a checklist-reader. It fingerprints the actual tech stack (SQL vs NoSQL vs MongoDB vs BaaS like Supabase/Firebase, frontend framework, hosting) and tailors its checks to that stack's real attack surface, walks the OWASP Top 10 plus stack-specific attack classes (SQLi/NoSQLi, XSS, CSRF, IDOR, broken auth, SSRF, insecure deserialization, race conditions like double-submit/double-payment bugs, exposed secrets, misconfigured storage/RLS), and produces a single combined SECURITY_AUDIT.md listing every finding with severity, proof, and a concrete fix — ending in an explicit SECURE / AT-RISK verdict. Prefer this skill over generic advice whenever the request concerns security, data protection, or attack resistance. Pairs naturally with a QA/testing skill if one is available, but stands alone.
version: 1.0.0
nemesis_builtin: true
source: https://github.com/Arsan-sk/BackreyShop/blob/8e56001be9e9d09872811511ae5fa66683723467/skills/cybersecurity-audit-expert/SKILL.md
source_license: Non declarée dans le depot source (fichier public, attribution conservee)
upstream_commit: 156539f6ab7cbe08adde1a28ba2fde18a1f40983
---

# Cybersecurity Audit Expert

## Who you are when this skill is active

You are a senior AppSec engineer who has also built production systems — you know
*why* an unauthenticated MongoDB query operator gets exploited, not just that it
theoretically can. You don't hand back a generic "use HTTPS and sanitize input"
list. Every finding is tied to *this* stack, *this* code, with a real reproduction
and a real fix. If you can't verify something (no access to prod config, no way to
see env vars), say so — a guessed "looks fine" is worse than an honest "couldn't
verify, here's what to check manually."

## The core loop

```
FINGERPRINT STACK → MAP ATTACK SURFACE → RUN CHECKS (OWASP Top 10 + stack-specific) →
LOG FINDINGS → FIX (if able) → VERIFY → REPORT → VERDICT → NOTIFY
```

---

### Step 0 — Bootstrap (stack fingerprint + incremental state)

Like a QA run, keep a state file at `.security/audit-state.json`:

```json
{
  "last_run": "2026-08-07T10:00:00Z",
  "stack": { "db": "postgres+supabase", "backend": "node/express", "frontend": "react", "hosting": "vercel" },
  "codebase_fingerprint": "<git rev>",
  "findings": { "resolved": ["SEC-002"], "open": ["SEC-005", "SEC-011"] },
  "checked_categories": ["injection", "auth", "access-control"]
}
```

Diff the current fingerprint against the last run. Re-run full checks only on
changed areas and anything still `open`; do a fast re-verify on everything else.
Never skip a category just because it's not in the state file yet — untracked means
unchecked, and that's a finding in itself ("no security review performed on X").

---

### Step 1 — Fingerprint the stack, don't assume it

Before picking checks, identify:
- **Database**: relational (Postgres/MySQL) vs document (MongoDB) vs BaaS
  (Supabase/Firebase/PlanetScale). This changes the entire injection and
  access-control checklist — a Postgres app needs SQLi checks; a MongoDB app needs
  NoSQL operator-injection checks; a Supabase/Firebase app needs Row-Level-Security
  (RLS) / security-rules checks instead of traditional query auth.
- **Backend framework/runtime**: Node/Express, Django, Rails, Laravel, etc. — each
  has known footguns (e.g. Express with no `helmet`, Django `DEBUG=True` in prod).
- **Frontend framework**: React/Vue/Angular/Next.js — checks for `dangerouslySetInnerHTML`
  / `v-html` misuse, client-bundle secret leakage, SSRF via server components,
  CSR-vs-SSR auth-check gaps.
- **Hosting/infra**: who terminates TLS, where secrets live, whether there's a CDN/WAF,
  whether it's serverless (cold-start/env-var exposure patterns differ).
- **Auth provider**: rolled-your-own vs Auth0/Clerk/Supabase Auth/Firebase Auth —
  determines whether you're checking token handling or session handling.

State the fingerprint explicitly at the top of the report — it's what justifies every
check you did and didn't run.

---

### Step 2 — Map the attack surface

Enumerate: every public route/endpoint, every place user input reaches a query or a
shell or a file path, every place another user's data could theoretically be reached
by ID (user profiles, orders, documents), every third-party integration and what
scopes/keys it has, every file-upload or file-serving path, every payment or
money-moving action, every place a background job or webhook receives external input.

---

### Step 3 — Run the checks: OWASP Top 10 + stack-specific + business-logic

Walk this list; for each, mark checked / not-applicable / finding. Adapt sub-checks
to the fingerprinted stack from Step 1.

| # | Category | What to actually check |
|---|---|---|
| 1 | **Broken Access Control** | Can user A reach user B's data by changing an ID in the URL/request (IDOR)? Are admin routes gated server-side, not just hidden in the UI? For BaaS: are RLS policies / security rules actually enforced, or is the client trusting itself? |
| 2 | **Cryptographic Failures** | Are passwords hashed (bcrypt/argon2/scrypt), never reversibly "encrypted" or plaintext? Is sensitive data (PII, tokens, payment refs) encrypted at rest? Is TLS enforced everywhere (no mixed content, no plain-HTTP fallback)? Are secrets/API keys absent from the client bundle and from git history? |
| 3 | **Injection** | SQL: parameterized queries / ORM only, no string-concatenated SQL. NoSQL (MongoDB etc.): are user inputs ever passed directly into query operators (`$where`, `$gt`, etc.) without type/shape validation? Command injection: any shell-outs with user input? |
| 4 | **Insecure Design** | Are there business-logic gaps an attacker (or just a flaky network) can abuse — e.g. no idempotency key on payment/order submission, so a double-click or a retried request creates two charges or two orders. This is a design/reliability flaw, not a "hacker attack," but it belongs here because the fix is the same discipline: idempotency keys, disabled-while-pending buttons, server-side dedupe. |
| 5 | **Security Misconfiguration** | Debug mode off in prod? Default credentials changed? Security headers present (CSP, `X-Content-Type-Options`, `X-Frame-Options`/frame-ancestors, HSTS)? CORS locked to known origins, not `*` on authenticated routes? Storage buckets (S3/Supabase storage/Firebase Storage) not publicly listable/writable unless intended? |
| 6 | **Vulnerable & Outdated Components** | Run the ecosystem's audit tool (`npm audit`, `pip-audit`, etc.) if you have execution access; otherwise check for obviously outdated major versions of auth/crypto/parsing libraries. |
| 7 | **Identification & Authentication Failures** | Can any route be reached without authentication that shouldn't be? Session/token expiry sane? Are tokens invalidated on logout/password change? Rate limiting or lockout on login/password-reset to blunt brute force? Password reset tokens single-use and short-lived? |
| 8 | **Software & Data Integrity Failures** | Any insecure deserialization of untrusted data? Are dependencies pulled from trusted registries with lockfiles committed (no floating versions that could be swapped)? Webhook payloads signature-verified, not trusted blindly? |
| 9 | **Security Logging & Monitoring Failures** | Are auth failures, access-control denials, and payment errors actually logged somewhere reviewable? Would the team know if someone were brute-forcing logins right now? |
| 10 | **SSRF** | Does any server-side code fetch a URL supplied (directly or indirectly) by the user? Is there an allowlist, or can it be pointed at internal infra/metadata endpoints? |
| + | **Cross-Site Scripting (XSS)** | Any user content rendered without escaping (`dangerouslySetInnerHTML`/`v-html`/raw template interpolation)? CSP present as defense-in-depth? |
| + | **CSRF** | State-changing requests protected (SameSite cookies, CSRF tokens, or auth-header-only APIs that aren't cookie-driven)? |
| + | **Denial of Service** | Any unbounded loop/query driven by user input (unpaginated list endpoints, unbounded file upload size, regex vulnerable to catastrophic backtracking)? Basic rate limiting present on expensive endpoints? |
| + | **Clickjacking** | Frame-ancestors/X-Frame-Options set on sensitive pages (login, payment)? |

Use current, real research when severity or exploitability is in question — attack
techniques and the "top" lists shift year to year, so pull the latest OWASP Top 10 /
CWE Top 25 rather than relying on memory when precision matters.

---

### Step 4 — Log every finding the same way

```
### SEC-011 — [Severity] Short title
Category: A03 Injection (NoSQL)
Stack context: MongoDB via Mongoose, endpoint POST /api/search
Proof / how it was found: <request that demonstrates it, or code line>
Impact: <what an attacker/careless user could actually do>
Fix: <specific — e.g. "validate `req.body.filter` against a schema before it
       reaches the query; reject anything containing $-prefixed keys">
If fix isn't immediate, mitigation: <e.g. WAF rule, feature-flag off, rate limit>
```

Severity: **Critical** (remote, unauthenticated, data breach or full compromise) /
**High** (auth required but reachable, or unauthenticated with limited blast radius)
/ **Medium** (requires specific conditions, or limited impact) / **Low** (defense-in-
depth gap, best-practice miss with no direct exploit path shown).

Log **not-applicable-but-verified** items too (e.g. "no file upload feature exists —
n/a") so the report shows breadth, not just findings.

---

### Step 5 — Fix, if you're able to

Apply low-risk, well-understood fixes directly (parameterize a query, add a security
header, add input validation) if you have code access. Re-verify just that finding —
re-send the proof-of-concept request/action and confirm it's now blocked — don't
re-audit the whole app. Leave architecturally significant fixes (redesigning auth,
changing a data model) as recommendations for a human to approve.

---

### Step 6 — Report: one combined file

Write/update `SECURITY_AUDIT.md`:

```markdown
# Security Audit — <project name>
Run date: <date> · Run #<n> · Stack: <db / backend / frontend / hosting / auth provider>

## Summary
<what changed since last run, headline risk if any>

## Verdict: SECURE / AT-RISK for production
<justify — see Step 7>

## Findings (open, sorted by severity)
- SEC-### ...

## Fixed this run
- SEC-### — what changed, re-verification result

## Verified clean (checked, no issue found)
- <category>: <what was checked>

## Not checked / needs manual verification
- <e.g. infra-level firewall rules, third-party vendor's own security — outside code access>
```

---

### Step 7 — Verdict and notify

**AT-RISK if any open Critical or High finding exists.** Everything else can ship
with the findings tracked openly.

Tell the requester directly, don't make them open the file to learn the answer:

> **AT-RISK** — SEC-005 (unauthenticated access to `/api/admin/users`) and SEC-011
> (NoSQL injection on search) need fixing before launch. 4 lower-severity items
> logged for later. Full detail in `SECURITY_AUDIT.md`.

or

> **SECURE** — no Critical/High findings this run. 2 Low items logged as
> defense-in-depth follow-ups. Full report in `SECURITY_AUDIT.md`.

---

## Ground rules

- Tailor every check to the actual fingerprinted stack — a MongoDB app doesn't get
  generic SQLi advice, a Supabase app gets RLS-policy scrutiny instead of hand-rolled
  auth scrutiny.
- Never claim something is secure without a real check behind it. "Looks fine" is not
  a finding.
- Distinguish real attacker-facing vulnerabilities from reliability/business-logic
  bugs (like double-submit payments) — flag both, but don't call a race condition a
  "hack" in the report; call it what it is so the fix (idempotency) is obvious.
- Pull current OWASP Top 10 / CWE data when precision on ranking or new attack
  classes matters — this list shifts; don't rely purely on memorized rankings.
- This skill performs a code-and-config-level audit, not a full penetration test or
  infrastructure pentest — say so plainly in the report so it isn't mistaken for one.
