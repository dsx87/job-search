#!/usr/bin/env bash
# Reactivate jobspy (Indeed + Google) on this ARMv6 Raspberry Pi.
#
# jobspy can't run out of the box on ARMv6: its Indeed scraper needs `tls-client`,
# whose Go shared library ships no 32-bit ARM build. We cross-compiled one
# (vendor/tls-client-armv6.so, GOARM=6, tuned for the Pi's arm1176jzf-s core).
# This script recreates the venv, installs python-jobspy from piwheels + numpy's
# libopenblas runtime dep, and patches the installed tls_client package to load
# our .so. After it succeeds, the daily run will include the Indeed/Google
# `jobspy` source (LinkedIn stays on the fast stdlib linkedin-guest either way).
#
# NOTE: the full Indeed matrix (12 countries x 5 queries) is slow on a single
# ARMv6 core and may exceed SCRAPE_BUDGET_SECONDS and be abandoned. If you want it
# to finish, raise SCRAPE_BUDGET_SECONDS in .env (e.g. 1800) — at the cost of a
# much longer daily fetch under RAM pressure.
#
# To turn Indeed/Google back OFF: just `rm -rf .venv` (the jobspy source then
# self-skips; linkedin-guest + the stdlib sources keep running on system python3).
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

SO="$PWD/vendor/tls-client-armv6.so"
[ -f "$SO" ] || { echo "ERROR: missing $SO (the cross-built ARMv6 tls-client library)"; exit 1; }

echo "==> Installing libopenblas (numpy runtime dep)"
sudo apt-get install -y --no-install-recommends libopenblas0

echo "==> Creating venv + installing python-jobspy (piwheels ARMv6 wheels)"
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir --upgrade pip
.venv/bin/pip install --no-cache-dir python-jobspy

echo "==> Wiring the ARMv6 tls-client library into the tls_client package"
TLSDIR=$(ls -d .venv/lib/python*/site-packages/tls_client)
cp "$SO" "$TLSDIR/dependencies/tls-client-armv6.so"
.venv/bin/python - "$TLSDIR/cffi.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
if "armv6.so" not in s:
    old = "    elif \"x86\" in machine():\n        file_ext = '-x86.so'"
    new = "    elif machine().startswith(\"arm\"):\n        file_ext = '-armv6.so'\n" + old
    assert old in s, "cffi.py layout changed — update this patch"
    open(p, "w").write(s.replace(old, new))
    print("   cffi.py patched (added arm branch)")
else:
    print("   cffi.py already patched")
PY

echo "==> Verifying import"
.venv/bin/python -c "import jobspy; from jobspy import scrape_jobs; print('   jobspy import OK')"

echo "Done. Indeed/Google are now enabled for the daily run."
echo "Disable again anytime with:  rm -rf $PWD/.venv"
