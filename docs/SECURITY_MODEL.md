# Security model and threat model

## Assets

1. Controller API keys (can read — and in future write — network configuration)
2. The network itself (availability of management paths)
3. User credentials, TOTP secrets, API tokens
4. Inventory/audit data (reveals topology, addresses, behaviour)

## Trust boundaries

Browser ↔ web (TLS via reverse proxy) · web ↔ api (private network) · api ↔ database/redis · api ↔ UniFi console (TLS, API key) · future: api ↔ AI providers, collectors.

## Threats and mitigations (implemented)

| Threat | Mitigation |
|---|---|
| Unintended controller changes (bug, compromised module, AI agent) | Client refuses non-GET without change-engine authorization; capability matrix marks writes disabled; no write endpoints exist |
| Credential theft from DB dump | Fernet encryption with master key outside the DB (`WARDEN_SECRET_KEY` or `data/vault.key`, 0600); fingerprints only in API responses |
| Password guessing | argon2id hashes; 5 failures / 15 min throttle per IP+username; constant-ish timing for unknown users; all failures audited |
| Session theft / CSRF | Random 256-bit session tokens stored hashed; `httpOnly`, `SameSite=Lax`, `Secure`; custom CSRF header required on cookie writes; sessions revoked on deactivation and password change |
| Privilege escalation | Permission checks on every route; admins can't grant owner; last owner protected; API tokens can only narrow the owner's permissions and can't mint tokens |
| Audit tampering | Hash chain over every record; UI and health check show chain status. Tamper-evident, not tamper-proof (DB writer can rewrite the chain); off-host export is roadmap |
| SSRF via controller URL | Only users with `controllers:manage` can set URLs; scheme validated. Residual risk accepted (admin-trusted) |
| Demo data mistaken for real | `is_demo` provenance on every row; brown *Demo data* tag everywhere it renders |
| Clickjacking / sniffing | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin` |

## Known gaps (tracked)

- Authorization denials (403) are not yet written to the audit trail; login/MFA/password denials are.
- Site-scoped role assignments exist in the schema but are not enforced.
- No OIDC/SSO yet; no secrets-rotation scheduler for the vault master key (MultiFernet supports it).
- Rate limiting is in-process (per API replica).
- Future AI/log content must be treated as untrusted input (prompt-injection tests are planned with the AI runtime).
