#!/usr/bin/env bash
# Checks the working tree before publication. It helps a human review but does not replace it.
# Usage: scripts/preflight.sh [--release]
#   --release also fails on TODO(publish) and REPLACE_ME markers.
# Internal terms (hostnames, suppliers, names) go in .preflight-denylist, one per line.
# That file is ignored by git: never commit it.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
excludes=(--exclude-dir=.git --exclude=LICENSE --exclude=preflight.sh --exclude=.preflight-denylist --exclude=ci.yml)

check() { # label, grep arguments...
  local label="$1"; shift
  local out
  out=$(grep -rnIE "${excludes[@]}" "$@" . 2>/dev/null)
  if [ -n "$out" ]; then
    echo "FAIL: $label"; echo "$out" | head -20; fail=1
  else
    echo "ok:   $label"
  fi
}

check "private keys" -e '-----BEGIN [A-Z ]*PRIVATE KEY-----'
check "common token formats" -e 'gh[pousr]_[A-Za-z0-9]{20,}' -e 'sk-[A-Za-z0-9_-]{20,}' -e 'AKIA[0-9A-Z]{16}' -e 'xox[baprs]-[A-Za-z0-9-]{10,}'
check "bearer tokens and hardcoded passwords" -i -e 'bearer [a-z0-9._-]{20,}' -e '(password|passwd|secret|api_?key)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'$<{ ]{8,}'
check "private IP addresses" -e '\b(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})\b'

# Public State of Valais sites that prompts and docs may cite. Every other *.vs.ch host fails.
public_hosts='^(www\.)?(lex|parlement)\.vs\.ch$|^www\.vs\.ch$'
out=$(grep -rnIoiE "${excludes[@]}" -e '@?[a-z0-9.-]+\.(vs\.ch|ghe\.com)\b' . 2>/dev/null \
  | OK="$public_hosts" awk -F: '{ host = tolower($NF) } host !~ ENVIRON["OK"]')
if [ -n "$out" ]; then
  echo "FAIL: State of Valais internal addresses"; echo "$out" | head -20; fail=1
else
  echo "ok:   State of Valais internal addresses (public sites allowed)"
fi

if [ -f .preflight-denylist ]; then
  check "local denylist" -i -F -f .preflight-denylist
else
  echo "warn: no .preflight-denylist, internal terms not checked"
fi

if [ "${1:-}" = "--release" ]; then
  check "unfinished publication markers" -e 'TODO\(publish\)' -e 'REPLACE_ME'
  if git rev-parse --git-dir >/dev/null 2>&1 && [ "$(git rev-list --count HEAD 2>/dev/null || echo 0)" -gt 1 ]; then
    echo "warn: more than one commit; check that no internal history is being published"
  fi
fi

[ "$fail" -eq 0 ] && echo "Preflight passed." || { echo "Preflight failed."; exit 1; }
