# Bug ledger — end-to-end validation of the client-deliverable deployment
(control host: Ubuntu, Docker 29.5.2, docker compose v2 plugin, ansible-core 2.20.6 in venv)

## Pre-run (self-containment) fixes
- **S1 — bundle not self-contained:** `minichris_dir` resolved (via group_vars) to
  `<bundle>/deploy/vendor/miniChRIS-docker`, which does not exist in the bundle.
  Fix: repoint to `{{ playbook_dir }}/../vendor/miniChRIS-docker` and vendor it there
  via `tooling/bootstrap.sh`. (group_vars/all.yml)
- **S2 — no dependency tooling / venv:** added `tooling/requirements.txt`,
  `tooling/bootstrap.sh` (venv + collection + vendored miniChRIS), and `run.sh`
  (pins ansible_python_interpreter to the venv so community.docker's SDK resolves
  despite the inventory's `auto_silent`).

## Runtime bugs (found by running it)

### B1 — `when: dicomweb_overlay_enabled` errors on modern ansible-core
- **Symptom:** overlay phase aborts: *"Conditionals must have a boolean result … derived from value of type 'str' at <CLI option '-e'>"* (ansible-core 2.19+ strict conditionals). The deliverable's own documented invocation `-e dicomweb_overlay_enabled=true` triggers it — `-e` makes the value the string `'true'`, not a bool.
- **Layer:** L2 deploy (Ansible), only surfaces when the overlay toggle is overridden on the CLI (i.e. exactly the documented "apply the overlay" path).
- **Fix:** `| bool` on the conditionals so string/bool both work. Hardened the same way for `test_orthanc_enabled` and `sample_data_push_to_cube` (4 sites) so any `-e` override is safe.
- **Files:** roles/dicomweb_app/tasks/main.yml (when + inline if), roles/orthanc/tasks/main.yml, roles/sample_data/tasks/main.yml.

### B2 — looped `changed_when` references bare `stdout` (undefined on ansible-core 2.19+)
- **Symptom:** overlay aborts on the pydicom-install task: *"Error while evaluating conditional: 'stdout' is undefined"*. The pip install itself succeeded (pydicom 3.0.2 in chris + worker) — only the per-item `changed_when` broke. Same defect in the "Wire INSTALLED_APPS+urls" task (`'UNCHANGED' not in stdout`), which also gates the restart handler.
- **Layer:** L2 deploy (Ansible); only reachable with the overlay enabled.
- **Fix:** pydicom task → `changed_when: false` (idempotent, drives no handler). Wire task → `changed_when: true` (always notify restart so Django re-imports the overlaid app; safe/idempotent). Non-looped migrate task already used `dicomweb_migrate.stdout` correctly.
- **Files:** roles/dicomweb_app/tasks/main.yml (lines ~106, ~123).

### B3 — overlay migrations fail: missing Phase A `pacsfiles.0009` parent node
- **Symptom:** `manage.py migrate` aborts: *NodeNotFoundError: Migration dicomweb.0002_pacsstudy dependencies reference nonexistent parent node ('pacsfiles', '0009_…')*. The L2 `dicomweb` migrations depend on Phase A's `pacsfiles` migration 0009 (6 PACSSeries tag columns), but stock `cube:6.11.0` predates Phase A and the overlay only copied the `dicomweb` app.
- **Layer:** L2 deploy / integration seam. The overlay was incomplete — it omitted the Phase A `pacsfiles` dependency the L2 app is built on.
- **Fix:** extend the overlay to apply Phase A to the container's `pacsfiles` app: copy the Phase A `0009` migration into `pacsfiles/migrations/` and add the matching model fields via an idempotent patcher (`phase_a_pacsfiles_patch.py`) on chris + worker, before migrate. (cube:6.11.0's PACSSeries model matched the Phase A base verbatim, so the patch is exact.) Result: pacsfiles.0009 + dicomweb.0001/0002/0003 (incl. pg_trgm) all apply.
- **Files:** roles/dicomweb_app/{tasks/main.yml, files/phase_a/0009_*.py, files/phase_a_pacsfiles_patch.py}.

### B4 — smoke test hard-fails on the upstream pfdcm 500 at `/api/v1/pacs/`
- **Symptom:** smoke `FAIL: CUBE /api/v1/pacs/ (got 500)`. CUBE logs show `Failed to resolve 'pfdcm'` — `/api/v1/pacs/` proxies pfdcm to list queryable PACS *services* and 500s when pfdcm is unreachable (documented miniChRIS quirk, KB 09). Unrelated to DICOMweb; the ingested-data check `/api/v1/pacs/series/` passes.
- **Layer:** smoke script (over-strict on an upstream-dependent, non-DICOMweb endpoint).
- **Fix:** treat a 500 there as a non-fatal SKIP (with note), keep PASS on 200, FAIL on anything else. (scripts/smoke.sh)

### B5 — QIDO/STOW return "no studies": wrong PACS id + un-indexed pre-overlay data
- **Symptom:** `QIDO /dicom-web/pacs/ChRIS/studies` → 404 `{"detail":"No PACS matches the given query."}` and `PACSStudy.count()==0`, despite a series being ingested. Two root causes:
  1. **Wrong PACS identifier.** oxidicom names the PACS after the *calling* AET (the test Orthanc's `DicomAet = SPIKEORTHANC`), not its own SCP AET. The deployment used `verify_pacs_identifier: "ChRIS"` (the SCP AET) — the group_vars/README comment asserted the wrong rule.
  2. **Pre-overlay ingest never indexed.** `sample_data` (ingest) runs *before* `dicomweb_app` installs the post_save auto-index signal, so the ingested PACSFiles never produced PACSInstance/PACSStudy rows.
- **Layer:** L2 deploy assumptions + role ordering. Routing/code were correct (the QIDO view executed and returned its own 404).
- **Fix:** (1) `verify_pacs_identifier: "{{ test_orthanc_aet }}"` with a corrected comment (PACS name = calling AET). (2) Add a `reindex_pacs_instances --sync` backfill step to the overlay role after restart, so already-ingested data is indexed into PACSStudy/PACSInstance immediately; future ingests are handled in real time by the signal.
- **Files:** group_vars/all.yml, roles/dicomweb_app/tasks/main.yml.

### B6 — cold-start race: backfill runs before async ingestion registers the series
- **Symptom (latent, cold-start only):** on a fresh `down -v` deploy, `sample_data` pushed to oxidicom then slept a fixed 10s. Ingestion is async (oxidicom → NATS → CUBE worker → PACSSeries/PACSFile). If registration takes longer than 10s, the overlay's `reindex_pacs_instances` backfill finds no PACSFiles → PACSStudy stays 0 → QIDO empty → smoke FAIL. Non-deterministic.
- **Layer:** L2 deploy timing (only surfaces on cold teardown→redeploy).
- **Fix:** replace the fixed sleep with a poll of `/api/v1/pacs/series/` (retries until ≥1 SeriesInstanceUID), so the play proceeds to overlay/backfill only once ingestion is confirmed.
- **Files:** roles/sample_data/tasks/main.yml.

### B7 — STOW of bodies >2.5MB 500s (Django DATA_UPLOAD_MAX_MEMORY_SIZE)
- **Symptom:** STOW of a large object (multi-frame volume, or a multi-instance multipart body) returns **500**; CUBE log: *"Request body exceeded settings.DATA_UPLOAD_MAX_MEMORY_SIZE."* Latent because the ~200KB sample slices are under Django's 2.5MB default; real DICOM (CT/MR, enhanced/multi-frame) and multi-instance STOW bodies exceed it.
- **Layer:** L2 deploy / settings. DICOMweb STOW inherently carries large binary bodies; the CUBE-wide Django cap blocks them before the view runs.
- **Fix:** overlay settings patch sets `DATA_UPLOAD_MAX_MEMORY_SIZE = None` (no in-memory cap) so STOW accepts full-size objects. (Production would enforce a size policy at the proxy.) Applied via roles/dicomweb_app/files/overlay_patch.py.

### B8 — clean-host failure: `ansible-galaxy` not found (venv bin not on PATH)
- **Symptom (fresh-machine only):** prereqs role aborts: *"Error executing command: [Errno 2] No such file or directory: b'ansible-galaxy'"*. The prereqs task shells out to `ansible-galaxy collection list`, but `run.sh` invoked ansible via the venv's absolute path without putting the venv `bin` on PATH. On the dev host a system Ansible (miniconda) masked this; a clean Ubuntu VM has no system Ansible → not found.
- **Layer:** tooling / run wrapper; only surfaces on a machine with no system-wide Ansible — exactly what the clean-room KVM test exercises.
- **Fix:** `run.sh` now `export PATH="${VENV_DIR}/bin:${PATH}"` so task shell-outs resolve venv tools.
- **Found by:** operations/tooling/cleanroom_kvm.sh (fresh Ubuntu 24.04 KVM).
