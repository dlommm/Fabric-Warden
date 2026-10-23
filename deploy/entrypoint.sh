#!/bin/bash
# Runs the API on loopback and the web app in front of it. When either exits the
# other is stopped too, so the container exits and Docker's restart policy applies.
set -u

# Client addresses come from WARDEN_FORWARDED_HOPS (see DEPLOYMENT.md), not uvicorn's proxy headers,
# which would trust a client-supplied X-Forwarded-For.
uvicorn warden.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers --no-server-header &
api=$!
(cd /app/web && exec node server.js) &
web=$!

stopping=0
trap 'stopping=1; kill -TERM "$api" "$web" 2>/dev/null' TERM INT
wait -n "$api" "$web"
status=$?
kill -TERM "$api" "$web" 2>/dev/null
wait
# A requested stop is a clean exit; anything else reports the process that ended first.
[ "$stopping" = 1 ] && exit 0
exit "$status"
