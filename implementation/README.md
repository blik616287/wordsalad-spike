# Implementation

The L2 DICOMweb work for `ChRIS_ultron_backEnd` (CUBE), plus the real backend it
targets — vendored as a pinned submodule.

```
implementation/
├── dicomweb-l2/            # the L2 dicomweb Django app (TRACKED source of truth)
└── ChRIS_ultron_backEnd/   # FNNDSC/ChRIS_ultron_backEnd, pinned submodule (the target)
```

## Why a submodule

`ChRIS_ultron_backEnd` is the repository the `dicomweb` app drops into. Vendoring
it as a **pinned git submodule** (same pattern as `deploy/vendor/miniChRIS-docker`)
gives us the real source to develop, validate, and diff against — reproducibly
and offline — instead of reverse-engineering the prebuilt `cube` image.

- Pinned to **`1d008bb`** (FNNDSC `master`, the baseline this spike was built on).
- It is the **upstream** repo, vendored read-only. We do **not** commit our code
  into it; `dicomweb-l2/` remains the single tracked source of the app in *this*
  repo. The submodule is the place to assemble + test the app in situ and to
  produce the upstream PR diff.

Initialize it (only if the repo was cloned without `--recurse-submodules`):

```bash
git submodule update --init implementation/ChRIS_ultron_backEnd
```

## How the app maps onto CUBE

`dicomweb-l2/` is a drop-in for `chris_backend/dicomweb/`. It supersedes the
Phase A app (`proposal-to-bch/code/phase-a.patch`, which is `PACSInstance` +
the indexer) and adds the L2 surface: `PACSStudy`, the QIDO query parser, the
DICOM-JSON renderer, the QIDO/WADO/STOW views, and `urls.py`. See
`dicomweb-l2/MAPPING.md` for the DICOMweb-attribute → CUBE-field mapping.

## Assemble + validate in situ (against the real backend)

This mutates the submodule **working tree** only (nothing is committed to it):

```bash
CUBE=implementation/ChRIS_ultron_backEnd
# 1. Phase A foundation (pacsfiles fields + 0009 migration, celery route,
#    INSTALLED_APPS, pydicom requirement):
git -C "$CUBE" apply "$PWD/proposal-to-bch/code/phase-a.patch"
# 2. Overlay the full L2 app (supersedes Phase A's dicomweb/):
cp -r implementation/dicomweb-l2/. "$CUBE/chris_backend/dicomweb/"
rm -f "$CUBE"/chris_backend/dicomweb/*.md
# 3. Mount the urls (INSTALLED_APPS already added by Phase A); idempotent:
python3 deploy/ansible/roles/dicomweb_app/files/overlay_patch.py "$CUBE/chris_backend"
# 4. Run CUBE's real test suite for the app:
( cd "$CUBE" && just test dicomweb )
```

Reset the submodule afterward:

```bash
git -C implementation/ChRIS_ultron_backEnd checkout . \
  && git -C implementation/ChRIS_ultron_backEnd clean -fd
```

> The eventual upstream contribution is `git -C $CUBE diff` after the steps
> above. To push a PR branch, fork `FNNDSC/ChRIS_ultron_backEnd`, repoint this
> submodule's URL at the fork, commit the assembled tree there, and open the PR.

## Version note

The submodule is pinned to `master` (`1d008bb`); the `deploy/` stack runs the
`ghcr.io/fnndsc/cube:6.11.0` **image**. For exact-match validation, check out the
matching tag in the submodule before assembling.
