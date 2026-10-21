"""Sentinel used to mint WriteAuthorization objects.

Only the change engine (roadmap phase 6) is allowed to import ISSUER. A test
enforces that no other module references it.
"""

ISSUER = object()
