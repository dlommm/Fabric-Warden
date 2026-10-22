# Security policy

Fabric Warden manages credentials for network infrastructure. Please report vulnerabilities privately through GitHub: **Security → Report a vulnerability** on this repository (private vulnerability reporting), not in a public issue. Include the version (System page or `/api/system/health`), the deployment shape (compose, reverse proxy) and steps to reproduce. Please do not test against equipment you don't own.

Supported versions: the latest release candidate / release only (currently 1.0.0-rc.1).

Design and threat model: [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md). Highlights: read-only controller access by construction (writes only through the audited change engine), encrypted credential vault, argon2 passwords, TOTP MFA, CSRF header on every cookie write and sign-in, layered sign-in limits, SSRF guard on every user-supplied address, request size limits, secret redaction in logs and audit, hash-chained audit trail including authorization denials, encrypted owner-only backups, hardened non-root containers.

Hardening checklist for operators: put a TLS reverse proxy in front (and set `WARDEN_FORWARDED_HOPS=1`), keep `WARDEN_COOKIE_SECURE=true`, set a strong `WARDEN_SECRET_KEY` and keep it out of backups you store with the database, enable MFA for owners and admins, keep the API port unpublished, keep syslog bound to localhost unless you need it on the LAN.
