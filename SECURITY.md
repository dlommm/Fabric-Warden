# Security policy

Fabric Warden manages credentials for network infrastructure. Please report vulnerabilities privately to the maintainer (open a GitHub security advisory once the repository is public) rather than in a public issue.

Design and threat model: [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md). Highlights: read-only controller access by construction, encrypted credential vault, argon2 passwords, TOTP MFA, hash-chained audit trail.
