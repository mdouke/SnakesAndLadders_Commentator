#!/usr/bin/env bash
# Double-click in Finder to start the system (opens a Terminal window).
exec "$(dirname "$0")/scripts/start.sh" "$@"
