#!/bin/bash
# LibreOffice launcher for the sandbox images. `soffice` and `libreoffice` on
# PATH resolve here (/usr/local/bin precedes /usr/bin).
#
# LibreOffice needs a writable user profile and builds one on first start,
# which costs a second or two. Sandbox containers start with a fresh
# HOME=/tmp on every run, so instead of paying that cost per conversion this
# wrapper seeds a per-run copy of the profile pre-built at image build time
# (/opt/libreoffice-profile) and points LibreOffice at it.
#
# Set LIBREOFFICE_PROFILE_DIR to use (and create/populate) a specific
# profile directory instead -- the image build uses that to create the
# pre-built profile in the first place.
set -e
PROFILE_DIR="${LIBREOFFICE_PROFILE_DIR:-}"
if [ -z "$PROFILE_DIR" ]; then
    PROFILE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/lo-profile.XXXXXX")"
    if [ -d /opt/libreoffice-profile ]; then
        cp -a /opt/libreoffice-profile/. "$PROFILE_DIR"/
    fi
fi
exec /usr/bin/soffice "-env:UserInstallation=file://${PROFILE_DIR}" "$@"
