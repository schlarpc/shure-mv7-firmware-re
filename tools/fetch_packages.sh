#!/bin/sh
# Download the Shure MV7 firmware packages.  They are not redistributed with this
# write-up, and no credential is included here.
#
# The package server uses HTTP basic authentication.  MOTIV Mix holds the user
# name and the password in plain text.  To read them:
#   1. install MOTIV Mix (README.md section 1 gives the steps for Wine)
#   2. extract resources/app.asar
#   3. recover the source from main.js.map with the sourcesContent array
#   4. open src/app-config/app-config.production.ts and find the packageServer block
#
# Then:
#   SHURE_PKG_USER=... SHURE_PKG_PASS=... ./fetch_packages.sh
set -e
: "${SHURE_PKG_USER:?set SHURE_PKG_USER (see the comment at the top of this file)}"
: "${SHURE_PKG_PASS:?set SHURE_PKG_PASS (see the comment at the top of this file)}"
BASE=https://wwb.shure.com/wireless
for v in 1.2.15 1.2.16 1.2.17 1.2.18 1.2.19; do
    echo "fetching MV7.$v.pack"
    curl -fsS -u "$SHURE_PKG_USER:$SHURE_PKG_PASS" -o "MV7.$v.pack" "$BASE/MV7.$v/MV7.$v.pack"
done
echo
echo "verify (SHA256SUMS ships in data/):"
sha256sum -c SHA256SUMS
