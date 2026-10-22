"""Keep secrets out of audit details and log lines.

Audit details are stored forever and shown to auditors; log lines go to
container logs and log shippers. Neither should ever carry a password, API key
or token. ``redact`` walks a JSON-like value and masks values whose key names a
secret; ``SecretFilter`` masks secret-looking substrings in log records.
"""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[redacted]"

# Key names that hold secrets. Matched against the lower-cased key.
_SECRET_KEY = re.compile(
    r"(pass(word|phrase|wd)?|secret|token|api[_-]?key|apikey|authorization|auth[_-]?header|"
    r"private[_-]?key|community|cookie|session[_-]?key|credential|signing)"
)
# Keys that look like secrets but only reference or describe one.
_SAFE_SUFFIXES = ("_id", "_ids", "_ref", "_fingerprint", "fingerprint", "_prefix", "_name", "_kind", "_type", "_count", "_at", "_set")
_SAFE_KEYS = frozenset({"token_limit", "monthly_token_limit", "tokens", "input_tokens", "output_tokens", "max_tokens", "has_secret"})


def is_secret_key(key: str) -> bool:
    k = key.lower()
    if k in _SAFE_KEYS or k.endswith(_SAFE_SUFFIXES):
        return False
    return bool(_SECRET_KEY.search(k))


def redact(value: Any, _depth: int = 0) -> Any:
    """A copy of ``value`` with secret-named entries masked (dicts, lists, tuples)."""
    if _depth > 20:
        return value
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and is_secret_key(k) and v not in (None, "", [], {}) and not isinstance(v, bool | int | float):
                out[k] = REDACTED
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


_TEXT_PATTERNS = (
    # Authorization: Bearer xyz / X-API-KEY: xyz
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 " + REDACTED),
    (re.compile(r"(?i)(x-api-key|authorization|api[_-]?key|token|password|passwd|secret)(\s*[:=]\s*)([^\s&,;\"']+)"), r"\1\2" + REDACTED),
    # Fabric Warden personal tokens
    (re.compile(r"\bwdn_[A-Za-z0-9_-]{8,}"), "wdn_" + REDACTED),
    # Credentials in URLs: scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + REDACTED + "@"),
)


def redact_text(text: str) -> str:
    for pattern, repl in _TEXT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class SecretFilter(logging.Filter):
    """Logging filter that masks secrets in the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must not drop the record
            return True
        clean = redact_text(msg)
        if clean != msg:
            record.msg = clean
            record.args = ()
        return True


def install_log_filter() -> None:
    """Attach the filter to the root handlers and the loggers Warden and its servers use."""
    flt = SecretFilter()
    for name in ("", "warden", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "sqlalchemy"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, SecretFilter) for f in logger.filters):
            logger.addFilter(flt)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, SecretFilter) for f in handler.filters):
            handler.addFilter(flt)
