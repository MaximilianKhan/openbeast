---
name: security-audit
description: Focused security review — input validation, authentication, cryptography, secrets handling, and timing-safe comparisons. Activate when the user asks for security audit, security review, or to "check for vulnerabilities".
allowed_tools: [bash, read_file, grep]
recommends_subagent: false
---

# Security audit

You are auditing code for security defects. The goal is **finding real
vulnerabilities**, not generating compliance noise. One real SQL injection
beats ten "consider adding a comment" findings.

## The threat model first

Before reading the code, ask:
- **Where's the trust boundary?** What input comes from the outside (HTTP
  request, file upload, environment, deserialized data, command-line args)?
- **Who's the attacker?** Anonymous internet, authenticated user, internal
  service, malicious dependency?
- **What's the asset?** Data, money, credentials, code execution, downtime?

A path that never crosses a trust boundary doesn't need the same scrutiny as
a public API handler.

## The eight categories

### 1. Injection

The classic. Look for:
- **SQL**: `f"SELECT ... WHERE id = {user_id}"`, `cur.execute("..." % data)`,
  `cur.execute("..." + data)` — all wrong. Required: parameterized queries
  with `?` or `%s` placeholders, parameters as second arg.
- **Shell**: `subprocess.run(cmd, shell=True)` with user data, `os.system`,
  backticks. Required: `shell=False`, argv list, or `shlex.quote(user_input)`.
- **LDAP, XPath, NoSQL**: same principle — never string-concat user data into
  the query.
- **Template injection**: `Template(user_input).render()` is RCE in Jinja2 and
  most template engines. Templates are code; user data is data.
- **Path traversal**: `open(f"/uploads/{filename}")` lets `../../etc/passwd`
  through. Use `os.path.abspath` + `commonpath` to confine, or hash the
  filename and store the mapping.

### 2. Authentication & session management

- **Password storage**: must be `bcrypt` / `scrypt` / `argon2` / `pbkdf2` with
  ≥100k iterations. Never `sha256(password)` or `md5(salt + password)`.
- **Token generation**: `secrets.token_urlsafe(32)` or `os.urandom(32)`. Never
  `random.random()`, `uuid4()` (predictable enough for some attackers), or
  `time.time()`-based.
- **Session expiry**: tokens must expire. Sliding window or absolute deadline.
- **Logout**: must invalidate the session server-side (not just clear the
  cookie client-side).

### 3. Cryptography

- **Don't roll your own**. If you see custom AES, custom HMAC, custom hash —
  major finding. Use stdlib `cryptography` / `hashlib` / `hmac` or a vetted
  library.
- **MAC then encrypt** — wrong order, modern protocols use AEAD (AES-GCM,
  ChaCha20-Poly1305). If you see encrypt-then-MAC done by hand, scrutinize.
- **Random IV/nonce** for AES-GCM (not zeros, not a counter under the same key
  unless explicit nonce-misuse-resistant mode).
- **No ECB mode** for anything but block-of-random-data.
- **Don't reuse nonces under the same key** for AES-GCM/ChaCha20-Poly1305 —
  catastrophic.

### 4. Timing-safe comparisons

```python
# WRONG — leaks token length and content via timing
if user_token == stored_token: ...

# RIGHT
if hmac.compare_digest(user_token, stored_token): ...
```

Audit every place where a secret is compared. Token compare, password compare,
HMAC verify, MAC verify — all need constant-time.

### 5. Secrets management

- **Hardcoded secrets** in source: API keys, passwords, signing keys. Major
  finding regardless of whether the repo is public — git history is forever.
- **Secrets in logs**: `logger.info(f"User logged in with token {token}")` is
  a CVE waiting to happen.
- **Secrets in error messages**: `raise ValueError(f"Bad config: {config}")`
  where `config` contains a key — same problem.
- **`.env` checked in**: even with `.gitignore`, audit if it's in history.
- **Container builds**: secrets passed via build args are baked into image
  layers. Use BuildKit secrets or runtime injection.

### 6. Authorization

- **IDOR (insecure direct object reference)**: `GET /users/{id}` that doesn't
  check whether the caller owns `id`. Test by passing other users' IDs.
- **Mass assignment**: `User(**request.json)` — user can set `is_admin=True`
  if `is_admin` is a column.
- **Server-side checks**: client-side validation is for UX; never trust it.
- **Privilege escalation paths**: account creation, password reset, role
  changes — every one of these is a target. Check that the right caller
  identity is required.

### 7. Deserialization

- **`pickle.loads(user_input)`**: RCE. Always.
- **`yaml.load(...)`** without `yaml.safe_load`: RCE on PyYAML <5.1.
- **`eval` / `exec` on any user input**: RCE.
- **Unsafe deserialization libraries** in any language (Java's
  `ObjectInputStream`, .NET's `BinaryFormatter`): RCE.

### 8. Resource exhaustion

- **Unbounded user-controlled allocations**: `list_of_size = int(user_input)`,
  `[x] * size` — user can DoS by passing 1e9.
- **Quadratic algorithms on user input**: regex with catastrophic
  backtracking, naive string matching, Bloom filter with no size cap.
- **Compression bombs**: zip/gzip ratio of 1000:1+ should be rejected.
- **No timeouts on outbound requests**: a slow downstream can hold all your
  workers.

## Workflow

1. **Scope** — `list_files` and `grep` to find the input handlers (web routes,
   CLI entry points, deserialization sites). These are your trust boundary.
2. **Trace** — for each input, trace where the data flows. Database? Shell?
   Filesystem? Each is a potential injection.
3. **Categorize** — for each suspicious site, identify which of the 8
   categories it falls under. Apply that category's checklist.
4. **Confirm** — don't just speculate. If you suspect SQLi, find the query
   and check whether it's parameterized.
5. **Severity** — rate by exploitability × impact. RCE > SQLi > IDOR > info
   leak > timing oracle.

## Output format

Each finding:
1. **Severity** — `critical` (RCE, auth bypass, mass data exposure) /
   `high` (SQLi, IDOR, timing oracle on secrets) /
   `medium` (info leak, weak crypto, missing rate limit) /
   `low` (defense in depth)
2. **Category** — one of the 8 above
3. **Location** — `file.py:42`, exact lines
4. **What's wrong** — quote the code
5. **Exploit sketch** — 1-2 sentences on how an attacker would actually use it
6. **Fix** — concrete patch or library to use

Example:
```
[critical] Injection — auth.py:67
Code: cur.execute(f"SELECT * FROM users WHERE name = '{username}'")
Exploit: username = "x' OR '1'='1" returns all users.
Fix: cur.execute("SELECT * FROM users WHERE name = ?", (username,))
```

## When NOT to flag

- **Theoretical attacks with no realistic threat model**. "An attacker who
  can read memory could…" — yes, and they could also read /etc/shadow.
- **CVE numbers without context**. Don't paste CVEs at the user; explain the
  specific defect in their code.
- **Style preferences masquerading as security**. "Use `is None` instead of
  `== None`" is not a security finding.

## Tools

- **`bandit`** (Python static analyzer) — fast first pass. `bandit -r src/`.
- **`semgrep`** with security rulesets — broader, language-agnostic.
- **`trufflehog` / `gitleaks`** — secret-in-history scanning.
- **`grep` for the obvious patterns** — `shell=True`, `pickle.load`,
  `yaml.load`, `eval(`, `exec(`, `MD5`, `sha1` for security uses.

Don't rely solely on tools — they miss anything subtle. Use them to surface
candidates, then think.
