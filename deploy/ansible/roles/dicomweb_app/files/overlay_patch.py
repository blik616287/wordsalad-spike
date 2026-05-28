#!/usr/bin/env python3
"""Idempotently wire the overlaid `dicomweb` app into CUBE's settings + urls.

Run INSIDE a running CUBE container, after the dicomweb app tree has been copied
to <APP_DIR>/dicomweb:

    python overlay_patch.py [APP_DIR]

APP_DIR defaults to /opt/app-root/src (the cube:6.11.x image project root, where
config/settings/common.py and config/urls.py live).

It appends two marker-fenced blocks:
  - config/settings/common.py : add 'dicomweb' to INSTALLED_APPS (so its models
    and migrations load).
  - config/urls.py            : mount the per-PACS DICOMweb (QIDO/WADO/STOW)
    routes under /dicom-web/pacs/<id>/.

Appending *after* the definitions (rather than editing the literals in place) is
robust across CUBE versions, and the marker fence makes re-runs a no-op.
"""
import os
import sys

APP_DIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("APP_DIR", "/opt/app-root/src")
MARKER = "dicomweb spike overlay"

SETTINGS = os.path.join(APP_DIR, "config", "settings", "common.py")
URLS = os.path.join(APP_DIR, "config", "urls.py")

SETTINGS_BLOCK = f"""

# >>> {MARKER} >>>
# Register the overlaid DICOMweb app so its models/migrations load.
if "dicomweb" not in INSTALLED_APPS:
    INSTALLED_APPS = list(INSTALLED_APPS) + ["dicomweb"]
# <<< {MARKER} <<<
"""

URLS_BLOCK = f"""

# >>> {MARKER} >>>
# Mount the per-PACS DICOMweb (QIDO/WADO/STOW) routes.
from django.urls import path as _dw_path, include as _dw_include  # noqa: E402
urlpatterns += [
    _dw_path("dicom-web/pacs/<str:pacs_identifier>/", _dw_include("dicomweb.urls")),
]
# <<< {MARKER} <<<
"""


def patch(path, block, label):
    if not os.path.exists(path):
        print(f"  {label}: {path} NOT FOUND -- is APP_DIR correct?", file=sys.stderr)
        sys.exit(2)
    with open(path) as fh:
        text = fh.read()
    if MARKER in text:
        print(f"  {label}: already wired (marker present) -> no change")
        return False
    with open(path, "a") as fh:
        fh.write(block)
    print(f"  {label}: wired 'dicomweb' -> {path}")
    return True


def main():
    changed = patch(SETTINGS, SETTINGS_BLOCK, "INSTALLED_APPS")
    changed = patch(URLS, URLS_BLOCK, "urlpatterns") or changed
    print("CHANGED" if changed else "UNCHANGED (idempotent)")


if __name__ == "__main__":
    main()
