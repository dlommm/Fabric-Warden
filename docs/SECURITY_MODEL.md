# Security model and threat model

## Assets

1. Controller API keys (read network configuration and, through the change engine, change it)
2. The network itself (availability of management paths)
3. User credentials, TOTP secrets, API tokens
4. Inventory/audit data (reveals topology, addresses, behaviour)

## Trust boundaries

Browser ↔ web (TLS via reverse proxy) · web ↔ api (private network) · api ↔ database/redis · api ↔ UniFi console (TLS, API key) · api ↔ AI providers (optional; TLS and API key for Anthropic).

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Unintended controller changes (bug, compromised module, AI agent) | Client refuses non-GET without a change-engine authorization; every write is an approved, audited change request; each controller starts read-only and only an owner can enable changes |
| Credential theft from DB dump | Fernet encryption with master key outside the DB (`WARDEN_SECRET_KEY` or `data/vault.key`, 0600); fingerprints only in API responses |
| Password guessing | argon2id hashes; 15-minute windows: 5 failures per address+username, 50 per username from any address, 20 per address across usernames (only when client addresses are trustworthy, `WARDEN_FORWARDED_HOPS` > 0); 5 failed password confirmations (password change, MFA disable) and 5 failed MFA activation codes per user; constant-ish timing for unknown users; all failures audited |
| Session theft / CSRF | Random 256-bit session tokens stored hashed; `httpOnly`, `SameSite=Lax`, `Secure`; custom CSRF header required on cookie writes **and** on sign-in, MFA verify, setup and sign-out (login CSRF); the half-signed-in MFA token is retired and a fresh session token issued when MFA completes; sessions revoked on deactivation, password change and restore |
| Spoofed client address (evading limits, misleading audit) | uvicorn runs without proxy headers; `client_ip` takes only the last `WARDEN_FORWARDED_HOPS` entries of `X-Forwarded-For` (default 0: the socket peer). The web app's `/api` proxy adds no entry, so only a TLS reverse proxy can be trusted |
| Privilege escalation | Permission checks on every route (a test walks every registered route and fails if one lacks authentication or, outside core, module gating); admins can't grant owner; last owner protected; API tokens can only narrow the owner's permissions and can't mint tokens; denials are audited (`authz.denied`) |
| Audit tampering | Hash chain over every record; UI and health check show chain status. Tamper-evident, not tamper-proof (a database writer can rewrite the chain) |
| AI agent exceeds its brief (prompt injection, bad model output) | Fixed tool list per agent and autonomy; schema-validated arguments; every tool checks the running person's permissions; no tool approves, applies or writes to a controller; the only change path is a proposal through the change engine, and at "act after approval" the apply is done by the approving person's own request; global, per-agent and per-run stops; budgets including a daily cap per schedule; agent-suggested notes are held pending until a person accepts them; schedules run as their creator (session only, never a token) with that person's current roles; every run, refusal, denied tool call, cancel, schedule and note change audited |
| SSRF via user-supplied URLs (controllers, servers, Protect/Home Assistant, AI providers, webhooks/ntfy/SMTP, WAN probe, SNMP) | Only managers of each area can set addresses. `security/outbound.py`: http/https only (Docker `unix://` where allowed), no credentials in URLs, link-local (incl. 169.254.169.254) / metadata host names / unspecified / multicast / reserved refused unless `WARDEN_OUTBOUND_ALLOW_LINK_LOCAL` or `WARDEN_OUTBOUND_ALLOWED_CIDRS`; checked on save, and for HTTP again at connect time after DNS resolution and on every redirect. Private and loopback addresses stay allowed (equipment lives there) |
| Oversized requests (memory exhaustion) | 2 MiB request-body limit enforced on `Content-Length` and while streaming (413); restore has its own 512 MiB limit; list fields bounded |
| Secrets in logs and audit | Audit details are redacted before hashing (keys naming a password, secret, token, API key, community, credential…); a log filter masks bearer tokens, `X-API-KEY`, `password=`/`token=`, `wdn_` tokens and URL credentials |
| Backup theft | Archives are AES-256-GCM encrypted under a scrypt-derived passphrase key (≥ 12 characters); export is owner-only and audited; restore needs an owner session, the current password and an explicit confirmation |
| AI provider key theft / data leaving the network | Keys in the vault, fingerprint only; local providers keep data on the LAN; the UI states where prompts go for each provider |
| Demo data mistaken for real | `is_demo` provenance on every row; brown *Demo data* tag everywhere it renders |
| Clickjacking / sniffing / injected content | API: `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-origin`, `Content-Security-Policy: default-src 'none'`, `Cache-Control: no-store`. Web: CSP (`default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri`/`form-action 'self'`), frame/referrer/COOP/permissions headers |

## Known limitations

- Roles apply to every site.
- Sign-in uses local accounts (no OIDC/SSO).
- Rate limiting is in-process (per API replica) and resets on restart. Per-account limits allow a targeted lockout (denial of service) of a known username for 15 minutes.
- The web CSP allows `'unsafe-inline'` scripts and styles (Next.js inlines its bootstrap; nonces would force dynamic rendering of every page).
- Outbound checks at connect time resolve DNS once before connecting; a hostile DNS server could still race the resolution (DNS rebinding window). SNMP and WAN-probe hosts are only checked when saved.
- Without a TLS reverse proxy, every request is attributed to `127.0.0.1` (the web app inside the container) in the audit trail.
- The audit chain is tamper-evident, not tamper-proof, and lives in the same database.
- AI tool results are treated as untrusted input; prompt-injection tests cover injected tool calls.
