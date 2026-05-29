# CUBE Django Internals — A Deep Engineering Reference

> **Audience:** an engineer new to the
> ChRIS stack. **Purpose:** answer questions about *how CUBE is actually built* —
> the filesystem model, the storage abstraction, the plugin/compute state machine, Celery,
> settings layering, and auth. Every claim is cited to real source as
> `path:line` relative to
> `implementation/ChRIS_ultron_backEnd/chris_backend/`.
>
> This file goes **deeper** than `knowledge-base/02-cube-and-pacs-data-model.md` (the PACS data
> model) and deliberately does **not** re-derive `PACS` / `PACSSeries` / `PACSFile` fields — see
> §02 for those. Here we cover the *engine room*: the generic filesystem (`ChrisFolder` /
> `ChrisFile` / `ChrisLinkFile`), `StorageManager`, the compute pipeline, and the worker fleet.

---

## 0. Orientation — where the code physically lives

In the **prebuilt CUBE image**:

| Fact | Value | Source |
|---|---|---|
| Base image | `registry.access.redhat.com/ubi9/python-312` | `../Dockerfile:33` |
| Project root inside image | `/opt/app-root/src` (the UBI Python `WORKDIR`; `COPY chris_backend/ ./`) | `../Dockerfile:38` |
| Static files baked at build | `/opt/app-root/var/staticfiles` | `config/settings/common.py:173` |
| Web entrypoint | `uvicorn config.asgi:application` on `:8000` (ASGI, not WSGI) | `../Dockerfile:42` |
| Settings module (prod) | `config.settings.production` (set in `asgi.py`) | `config/asgi.py:25` |
| Settings module (Celery default) | `config.settings.local` (overridden by env in prod) | `core/celery.py:10` |
| Django version | 5.1.x | `config/settings/common.py:5` |
| Python | 3.12 | base image |

CUBE runs as an **ASGI** app under uvicorn (`config/asgi.py:30`) with a `ProtocolTypeRouter`
splitting `http` (Django) from `websocket` (Django Channels, `core/websockets/urls.py`). This
matters for DICOMweb: the SSE progress endpoint (`PACSFileProgressSSE`) and the LONK WebSocket
both depend on this async stack — CUBE is *not* a plain WSGI/synchronous Django server.

---

## 1. INSTALLED_APPS — the app inventory

`config/settings/common.py:32-57`. Third-party + 10 first-party apps:

```
django.contrib.{admin,auth,contenttypes,sessions,messages,staticfiles}
django_filters · django_celery_beat · rest_framework · rest_framework.authtoken
corsheaders · storages · collectionjson · drf_spectacular
core · feeds · plugins · plugininstances · pipelines
userfiles · pacsfiles · filebrowser · users · workflows
```

Note: in this repo there is also a **`dicomweb`** app on disk
(`dicomweb/`) — the Phase A DICOMweb spike — but in upstream `common.py` it is **not** in
`INSTALLED_APPS` (the list above is the upstream master list). Treat `dicomweb` as the additive
work, not part of the shipped backend.

### 1.1 App dependency graph (import direction = "depends on")

Derived from the `from <app>.models import ...` lines at the top of each `models.py`:

```
                         core   (ChrisFolder/ChrisFile/ChrisLinkFile, StorageManager, ChrisInstance)
                          ▲  ▲  ▲  ▲  ▲  ▲
        ┌─────────────────┘  │  │  │  │  └────────────────────┐
        │            ┌───────┘  │  │  └────────┐              │
     userfiles    plugins     users          pacsfiles    filebrowser
        ▲            ▲  ▲        ▲ (users imports          (imports core only)
        │            │  │        │  userfiles + core)
      feeds ─────────┘  │     (no app imports users)
        ▲               │
        │               │
   plugininstances ─────┘   (imports core, feeds, plugins, workflows)
        ▲
        │
   pipelines (imports core, plugins)        workflows (imports pipelines, plugininstances.enums)
```

Key edges, with citations:

- `core` is the **root** — depends on no other first-party app. Everything imports `core.models`
  and `core.storage`.
- `feeds.models` imports `core.models` (filesystem + permissions) and `userfiles.models`
  (`feeds/models.py:11-15`).
- `plugininstances.models` imports `core`, `feeds`, `plugins`, `plugins.fields`, and
  `workflows.models` (`plugininstances/models.py:12-17`).
- `pipelines.models` imports `core` and `plugins` (`pipelines/models.py:14-17`).
- `workflows.models` imports `pipelines.models` and `plugininstances.enums`
  (`workflows/models.py:7-8`).
- `users.models` imports `core.models`, `core.storage`, and `userfiles.models`
  (`users/models.py:12-14`).
- `pacsfiles.models` imports `core.models`, `core.utils`, `core.storage`, and its own
  `.services.PfdcmClient` (`pacsfiles/models.py:11-14`).

There are **no per-app `urls.py`** — every route is registered centrally (see §7).

---

## 2. The ChRIS filesystem model — `core/models.py`

This is the conceptual heart of CUBE. **There is no per-app file table.** All data — feed
outputs, uploaded user files, *and PACS DICOM files* — is one virtual filesystem made of three
DB models, all in `core/models.py`, all backed by an abstract storage layer (§3).

### 2.1 The three node types

| Model | Table? | Key field | Meaning |
|---|---|---|---|
| `ChrisFolder` | real | `path` (CharField 1024, **unique**) | a directory node | `core/models.py:111-114` |
| `ChrisFile` | real | `fname` (**FileField** 1024, unique) | a file (the bytes live in storage) | `core/models.py:601-603` |
| `ChrisLinkFile` | real | `fname` + `path` | a symlink-like pointer (`*.chrislink`) | `core/models.py:895-898` |

Path rules (enforced in `save()`):
- Paths **may not start or end with `/`** — `ChrisFolder.save` raises `ValueError`
  (`core/models.py:135-136`); same check in `ChrisFile.save` (`:625-626`).
- Paths are **relative** to the storage container root (e.g. the Swift container `users`), not
  absolute OS paths. `home/<user>/...`, `SERVICES/PACS/...`, `PUBLIC`, `SHARED`, `PIPELINES` are
  all top-level path prefixes.

### 2.2 The folder tree is materialized by `path`, not just by FK

`ChrisFolder` has a self-FK `parent` (`core/models.py:115-116`), **but the tree is primarily
encoded in the `path` string**. On `save()`, the folder *recursively creates its parent folders*
by string-splitting the path (`core/models.py:129-149`):

```python
parent_path = os.path.dirname(self.path)
try:
    parent = ChrisFolder.objects.get(path=parent_path)
except ChrisFolder.DoesNotExist:
    parent = ChrisFolder(path=parent_path, owner=self.owner)
    parent.save()   # recursive — walks all the way up to the root ''
self.parent = parent
```

So saving `home/jane/feeds/feed_3/pl-x_5/data` will auto-create every missing ancestor folder
row. Tree operations therefore use **`path__startswith` prefix queries**, not recursive FK walks:
- `get_descendants()` → `ChrisFolder.objects.filter(path__startswith=path + '/')`
  (`core/models.py:190-196`).
- `move()` rewrites every descendant folder/file/link path with `bulk_update` and physically
  moves the storage tree via `storage_manager.move_path` (`core/models.py:151-188`).
- `get_first_existing_folder_ancestor()` builds the list of ancestor paths and picks the longest
  existing one by `Length('path')` (`core/models.py:392-414`). **This is exactly how a PACS `.dcm`
  file finds its owning series folder** — there is no FK, only path ancestry.

**Ownership quirk:** any folder whose path is `''`, `home`, `PUBLIC`, `SHARED`, or starts with
`PIPELINES`/`SERVICES` is force-owned by the `chris` superuser (`core/models.py:146-148`). PACS
lives under `SERVICES/PACS/...`, so the whole PACS subtree is owned by `chris`.

### 2.3 `ChrisFile` — bytes live in storage, metadata in Postgres

`fname` is a Django `FileField` (`core/models.py:603`), so the model row is *metadata*; the
actual bytes are in the configured storage backend (§3). The model is **WORM-ish**: there is no
content-mutation API, only `move()` (`core/models.py:635-661`). Other fields: `public`
(`db_index=True`), `parent_folder` FK, `owner` FK, and M2M share tables (`shared_groups`,
`shared_users`).

`get_base_queryset()` (`core/models.py:835-841`) returns *all* `ChrisFile` rows; **proxy
subclasses override it to scope a subtree** — this is the single most important pattern in CUBE's
data model:

- `UserFile` (proxy) → `fname__startswith='home/'` (`userfiles/models.py:18-30`).
- `PACSFile` (proxy) → `fname__startswith='SERVICES/PACS/'` (see §02 / `pacsfiles/models.py:229`).

So **`UserFile` and `PACSFile` are not tables** — they are the same `chris_file` Postgres table,
filtered by path prefix. A DICOM `.dcm` file and a user's uploaded CSV are the *same kind of row*.

### 2.4 `ChrisLinkFile` — ChRIS's "symlink"

A link file is a real stored object whose **contents are the target path string**
(`core/models.py:911-929`). On `save(name=...)` it writes a `<name>.chrislink` text file into
storage containing the pointed path, then saves the row. This powers:
- `SHARED/<munged_path>.chrislink` — sharing a folder/file with users/groups
  (`create_shared_link`, `core/models.py:306-321`).
- `PUBLIC/<munged_path>.chrislink` — public exposure (`create_public_link`, `:337-352`).
- A user's home gets two seed links on creation: `public → PUBLIC`, `shared → SHARED`
  (`users/models.py:75-80`).

`*.chrislink` files are how a feed can "contain" data that physically lives elsewhere (e.g. a
user's uploads, or a PACS series) without copying bytes.

### 2.5 Permissions are denormalized down the whole subtree

CUBE does **not** compute permissions transitively at read time. When you grant a group/user a
permission on a folder, the `save()` override **bulk-creates permission rows for every descendant
folder, file and link file** (`FolderGroupPermission.save`, `core/models.py:447-487`; the user
variant `:531-569`). Deletion bulk-deletes them again (`:489-509`). Permissions: `r` / `w`
(`PERMISSION_CHOICES`, `core/models.py:25`). Edge case: this means granting on a huge folder is an
O(N-descendants) write — relevant if someone asks about sharing a large PACS study.

### 2.6 Storage is kept in sync via Django signals

Every node type has a `post_delete` signal that deletes the underlying storage object/tree:
- `ChrisFolder` → `auto_delete_folder_from_storage` → `storage_manager.delete_path`
  (`core/models.py:417-425`).
- `ChrisFile` → `storage_manager.delete_obj` (`core/models.py:844-852`).
- `ChrisLinkFile` → same (`core/models.py:1141-1149`).

And `ChrisFile.save` has a **compensating action**: if the DB save fails after the bytes were
written, it deletes the leftover storage object (`core/models.py:627-633`). This is hand-rolled
two-phase consistency between Postgres and object storage — there is no XA transaction.

### 2.7 `FileDownloadToken` and `ChrisInstance`

- `FileDownloadToken` (`core/models.py:1192-1201`): short-lived per-user token so a browser/viewer
  can GET file bytes via `?download_token=...` without a header (used by the PACS binary file
  route — see §02). `token` is `db_index=True`.
- `ChrisInstance` (`core/models.py:75-108`): a **singleton** (forces `id=1` on save, `delete()` is
  a no-op). Holds `job_id_prefix` (default `chris-jid-`) used to name remote compute jobs
  (`:82`). Loaded via `ChrisInstance.load()`.
- `AsyncDeletableModel` (`core/models.py:39-72`): abstract base giving `deletion_status`
  (`inactive`/`pending`/`failed`), used by `ChrisFolder`, `Feed`, `PluginInstance`, `PACSSeries`
  to support **async deletion** via Celery (mark pending → background task does the work).

---

## 3. The storage abstraction — `core/storage/`

CUBE decouples "the filesystem model" (§2) from "where bytes actually go." The contract is the
abstract base class `StorageManager` (`core/storage/storagemanager.py:6`), implemented by three
backends. The docstring is candid: *"historically ChRIS was tightly-coupled to OpenStack Swift,
hence variable and function names use Swift terminology"* (`core/storage/__init__.py:11-12`).

### 3.1 The interface

`StorageManager` (`core/storage/storagemanager.py`) — methods analogous to `ls`/`stat`/`cat`:

| Method | Purpose | line |
|---|---|---|
| `create_container()` | create bucket/container/top-dir | `:14` |
| `ls(path_prefix)` | list files under a prefix | `:25` |
| `path_exists` / `obj_exists` | dir-or-file vs file existence | `:31` / `:37` |
| `upload_obj(path, contents, content_type)` | write bytes | `:43` |
| `download_obj(path)` | read bytes | `:53` |
| `copy_obj` / `delete_obj` | object ops | `:59` / `:67` |
| `copy_path` / `move_path` / `delete_path` | subtree ops | `:73` / `:79` / `:85` |
| `sanitize_obj_names(path)` | strip commas from names (handles edge cases) | `:91` |

Files are explicitly described as **immutable / WORM** (write-once, read-many) — see
`core/storage/__init__.py:8-9`. That's the architectural assumption behind §2.3.

### 3.2 The three backends

| Backend class | Storage | Constructed with | Manager file |
|---|---|---|---|
| `SwiftManager` | OpenStack Swift object store | `SWIFT_CONTAINER_NAME`, `SWIFT_CONNECTION_PARAMS` | `core/storage/swiftmanager.py` (238 L) |
| `S3Manager` | S3 / MinIO / Ceph RGW | `S3_BUCKET_NAME`, `S3_CONNECTION_PARAMS` | `core/storage/s3manager.py` (312 L) |
| `FilesystemManager` | a literal POSIX directory (`fslink`/`filesystem`) | `MEDIA_ROOT` | `core/storage/plain_fs.py` (166 L) |

### 3.3 `connect_storage(settings)` — the dispatcher

The single chokepoint everything uses (`core/storage/helpers.py:12-24`):

```python
def connect_storage(settings) -> StorageManager:
    storage_name = settings.STORAGES['default']['BACKEND'].rsplit('.', 1)[-1]
    if storage_name == 'SwiftStorage':     return SwiftManager(settings.SWIFT_CONTAINER_NAME, settings.SWIFT_CONNECTION_PARAMS)
    elif storage_name == 'FileSystemStorage': return FilesystemManager(settings.MEDIA_ROOT)
    elif storage_name == 'S3Boto3Storage':  return S3Manager(settings.S3_BUCKET_NAME, settings.S3_CONNECTION_PARAMS)
    raise ValueError(...)
```

It **dispatches on the Django `STORAGES['default']['BACKEND']` string** (`helpers.py:68-69`),
which is set per environment in settings (§5). So the chosen backend is determined by
`STORAGE_ENV`. Every model method that touches bytes calls `connect_storage(settings)` fresh
(e.g. `ChrisFolder.move`, `ChrisFile.save`, `ChrisLinkFile.save`, all `post_delete` signals) —
there is no long-lived singleton client; the manager is cheap to reconstruct.

`verify_storage_connection(**kwargs)` (`helpers.py:27-36`) builds a `_DummySettings` from kwargs,
connects, and calls `create_container()` — run at settings-import time so the process **fails fast
at boot** if storage is misconfigured (`production.py:120-123`, `local.py:129-139`).

### 3.4 fslink vs swift vs s3 — what they mean operationally

- **`swift`** — the historical default; `STORAGES['default'] = swift.storage.SwiftStorage`
  (`local.py:96`, `production.py:77`). Dev creds: user `chris:chris1234`, key `testing`, container
  `users`, auth at `http://swift_service:8080/auth/v1.0` (`local.py:97-103`).
- **`s3`** — `storages.backends.s3boto3.S3Boto3Storage`; needs `S3_ENDPOINT_URL`,
  `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` (`production.py:90-111`). Forces path-style
  addressing and SigV4 (`production.py:105-106`). Dev default points at MinIO
  `http://s3-service:9000` (`local.py:113-119`).
- **`fslink` / `filesystem`** — `django.core.files.storage.FileSystemStorage` rooted at
  `MEDIA_ROOT` (`production.py:112-116`; dev `MEDIA_ROOT='/data'`, `local.py:108-110`). "fslink"
  is the bind-mount-a-host-directory mode — fastest for single-node, no object store needed.
  `copy_obj` may be implemented as hard/sym links for efficiency (interface note,
  `storagemanager.py:63`).

`STORAGE_ENV` is validated against exactly `('swift','fslink','filesystem','s3')`; anything else
raises `ImproperlyConfigured` (`production.py:73-74`, `local.py:93-94`).

---

## 4. The plugin / compute model — the analysis engine

### 4.1 The static catalog (`plugins/models.py`)

| Model | Role | line |
|---|---|---|
| `PluginMeta` | version-independent plugin identity (`name` unique, `type`, repo, authors) | `plugins/models.py:86-108` |
| `Plugin` | a **specific version** of a `PluginMeta` (`dock_image`, `selfexec`, resource limits) | `:159-197` |
| `PluginParameter` | a CLI parameter spec (`flag`, `type`, `optional`, `action`) | `:261-280` |
| `Default{Str,Int,Float,Bool}Parameter` | per-param default values | `:290-335` |
| `ComputeResource` | a **remote compute site** (a pfcon endpoint) | `:12-67` |

- `Plugin.meta.type` is one of `fs` / `ds` / `ts` (`PLUGIN_TYPE_CHOICES`). `fs` = *feed source*
  (root, creates a new feed), `ds` = *data → data*, `ts` = *topology* (joins multiple parents).
  The type drives feed/folder creation logic (§4.3).
- `Plugin.compute_resources` is **M2M** (`plugins/models.py:190`): a plugin can be registered on
  several compute sites. Resource limits (`min/max_cpu_limit`, `min_memory_limit`,
  `min_number_of_workers`, `min_gpu_limit`) are stored as custom `CPUField`/`MemoryField`
  (millicores / Mi) (`:163-189`).
- `ComputeResource` (`plugins/models.py:12-67`) is the data-model representation of a **pfcon**
  service: `compute_url`, `compute_auth_url`, `compute_user`/`compute_password`,
  `compute_auth_token`, and three behavior flags — `compute_innetwork`,
  `compute_requires_copy_job`, `compute_requires_upload_job` (`:21-23`) — which select the job
  flow (§4.4). It refuses deletion if any plugin would be left with **zero** compute resources
  (`:42-53`).

### 4.2 `Feed` — the unit of provenance (`feeds/models.py`)

A `Feed` (`feeds/models.py:18-29`) is a named analysis tree. It owns a `ChrisFolder`
(`OneToOne`, `:23`) at `home/<user>/feeds/feed_<id>` and auto-creates a `Note` on first save
(`:37-51`). Supporting models: `Note`, `Tag`/`Tagging` (M2M), `Comment`, plus
`Feed{Group,User}Permission` whose `save()`/`delete()` cascade write-perms across the feed's
folder **and across any `*.chrislink`-pointed external data owned by the feed owner**
(`:251-368`) — that's how shared inputs (e.g. linked PACS data) inherit access.

`Feed.add_jobs_status_count` annotates a queryset with per-status plugin-instance counts
(`feeds/models.py:53-80`) — the same status vocabulary as §4.5.

### 4.3 `PluginInstance` — one execution node (`plugininstances/models.py`)

A `PluginInstance` (`:41-79`) is one run of one `Plugin` inside a `Feed`. Critical relationships:

- `previous` = self-FK building the **DAG within a feed** (`:61-62`); `next` is the reverse.
- `plugin` FK, `feed` FK, `compute_resource` FK (nullable, `SET_NULL`), `workflow` FK
  (`:63-74`).
- `output_folder` = `OneToOne` to a `ChrisFolder` (`:66-67`).
- per-instance resource overrides `cpu_limit`, `memory_limit`, `number_of_workers`, `gpu_limit`
  (`:75-78`).

**Save-time folder/feed wiring** (`plugininstances/models.py:86-149`):
- An `fs` instance creates a **new `Feed`** and its folder
  `home/<user>/feeds/feed_<id>` (`_save_feed`, `:107-123`).
- A `ds`/`ts` instance **inherits the previous instance's feed** (`:96-97`).
- Output folder path is built by walking `previous` back to the `fs` root, producing
  `home/<user>/feeds/feed_<id>/<plugin>_<instid>/.../<plugin>_<instid>/data`
  (`_save_output_folder`, `:125-149`).
- Compute defaults fall back to the plugin's `min_*` limits (`_set_compute_defaults`, `:151-162`).

Parameter *values* for an instance are stored in six typed tables —
`StrParameter`, `IntParameter`, `FloatParameter`, `BoolParameter`, `PathParameter`,
`UnextpathParameter` (`plugininstances/models.py:323-405`), each `unique_together(plugin_inst,
plugin_param)`. `PathParameter`/`UnextpathParameter` allow 16 000-char values (lists of paths,
`:380`,`:394`). The dispatch table `PARAMETER_MODELS` maps type→model (`:407-412`).

Concurrency control: `PluginInstanceLock` (`OneToOne`, `:300-306`) is a mutex so the periodic
scheduler doesn't double-submit an instance; `PluginInstanceSplit` (`:309-320`) records fan-out.

### 4.4 How compute is dispatched — pfcon, not Docker directly

CUBE never runs containers itself. It talks to a remote **pfcon** service (which in turn drives
**pman** to schedule containers). The client lives in
`plugininstances/services/abstractjobs.py`:

```python
from pfconclient import client as pfcon              # abstractjobs.py:11
...
cr = self.c_plugin_inst.compute_resource             # :40
self.pfcon_client = pfcon.Client(cr.compute_url, cr.compute_auth_token)   # :41
self.pfcon_client.pfcon_innetwork      = cr.compute_innetwork              # :42
self.pfcon_client.requires_copy_job    = cr.compute_requires_copy_job     # :43
self.pfcon_client.requires_upload_job  = cr.compute_requires_upload_job   # :44
```

The job id is `ChrisInstance.job_id_prefix + str(plugin_instance.id)` → e.g. `chris-jid-42`
(`abstractjobs.py:37-38`). The concrete job classes live in `plugininstances/services/`:
- `PluginInstanceAppJob` (`pluginjobs.py:36`) — submit/run/status/cancel/delete against pfcon
  (`run()` POSTs to the pfcon URL, `pluginjobs.py:46-148`; status poll `:190-243`; delete
  `:252-269`).
- `PluginInstanceCopyJob` (`copyjobs.py`) and `…UploadJob` (`uploadjobs.py`) — used when the
  compute resource sets `compute_requires_copy_job` / `compute_requires_upload_job` (out-of-network
  sites that need data shuttled in/out).

**In-network vs out-of-network** is the key branch: an in-network pfcon shares storage with CUBE,
so no copy/upload job is needed; an out-of-network site needs CUBE to copy inputs in and upload
outputs back (`pluginjobs.py:95-117`, flags from `ComputeResource`).

### 4.5 The plugin-instance state machine

Statuses (`plugininstances/enums.py`):

```
created → waiting → [copying] → scheduled → started → [uploading] → registeringFiles
        → finishedSuccessfully | finishedWithError | cancelled
```

- `ACTIVE_STATUSES` = everything up to and incl. `registeringFiles`; `INACTIVE_STATUSES` =
  the three terminal states (`enums.py`).
- `copying`/`uploading` only occur for out-of-network compute ("when supported", `enums.py`).
- `set_status()` saves only `status` (and start/end dates when moving to `scheduled`)
  (`plugininstances/models.py:207-216`).
- There is a parallel `remote_cleanup_status` machine (`notStarted → deletingData →
  deletingContainers → complete | failed`, `REMOTE_CLEANUP_STATUS_CHOICES`) for tearing down
  remote jobs after completion.

The **scheduler** (periodic task, §6) moves `waiting → copying|scheduled`:
`_schedule_plugin_instance` checks `compute_requires_copy_job`; if set, status→`copying` and it
queues a `PluginInstanceCopyJob`, otherwise status→`scheduled` and it queues a
`PluginInstanceAppJob` (`plugininstances/tasks.py:197-210`). A `ts` instance only schedules once
**all** its named parent instances are `finishedSuccessfully` (`tasks.py:166-196`).

### 4.6 Pipelines & Workflows — templated DAGs

- `Pipeline` (`pipelines/models.py:27-37`) = a named, reusable DAG of plugins via the
  `PluginPiping` through-model (M2M, `:35`). It owns parameter defaults
  (`DefaultPiping{Str,Int,Float,Bool}Parameter`) and exposes tree helpers
  (`get_pipings_tree`, `pipelines/models.py:82+`). Pipelines can be authored from YAML/JSON
  (`PIPELINE_SOURCE_FILE_TYPE_CHOICES`, `:24`).
- `Workflow` (`workflows/models.py`) = one *instantiation* of a `Pipeline` (FK to `Pipeline`),
  and `PluginInstance.workflow` FK ties created instances back to it. Workflows annotate
  per-status job counts via the shared `JOBS_STATUS_FIELDS` table, which is **asserted at import
  time** to be a subset of `plugininstances.enums.STATUS_CHOICES` (`workflows/models.py:27-30`) —
  a nice guard against status-string drift.

---

## 5. Settings layering & key env vars

Three modules, classic Django split (`config/settings/`):

```
common.py   ── base: INSTALLED_APPS, DRF, MIDDLEWARE, DB ENGINE, drf-spectacular, static
   ▲
   ├── local.py       ──  imports * from common; DEBUG=True; hard-coded dev secrets
   └── production.py  ──  imports * from common; everything from env via environs.Env
```

Both leaf modules do `from .common import *` (`local.py:14`, `production.py:9`).

### 5.1 What each layer fixes

| Concern | common.py | local.py (dev) | production.py (prod) |
|---|---|---|---|
| `DEBUG` | — | `True` (`:38`) | implicitly False |
| `ROOT_URLCONF` | `config.urls` (`:98`) | `config.local_urls` (`:25`) | `config.urls` |
| `SECRET_KEY` | — | hard-coded (`:28`) | `env('DJANGO_SECRET_KEY')` (`:36`) |
| DB engine | `postgresql` (`:124`) | name `chris_dev`@`chris_dev_db:5432` (`:146-152`) | all from env (`:53-66`) |
| DB conn pool | — | `{min1,max2,timeout10}` (`:152`) | optional via `DATABASE_CONN_POOL` (`:58-66`) |
| Storage | `STORAGES.staticfiles` only (`:166`) | `STORAGE_ENV` default `swift` (`:92`) | `STORAGE_ENV` required (`:71`) |
| Celery broker | — | `redis://dragonflydb:6379/0` (`:200`) | `env('CELERY_BROKER_URL')` (`:184`) |
| LDAP | — | on, `ldap://lldap:3890` (`:214-241`) | optional via `AUTH_LDAP` (`:206-251`) |
| NATS | — | `nats://nats:4222` (`:192`) | `env('NATS_ADDRESS')` (`:174`) |
| pfdcm | — | `http://pfdcm:4005` (`:195`) | `env('PFDCM_ADDRESS')` (`:179`) |
| CORS | — | allow-all (`:187`) | env-driven (`:160-169`) |

### 5.2 Production env-var inventory (all via `environs.Env`, `production.py:23-29`)

`DJANGO_SECRET_KEY`, `CHRIS_SUPERUSER_PASSWORD`, `DJANGO_ALLOWED_HOSTS`,
`POSTGRES_DB/USER/PASSWORD`, `DATABASE_HOST/PORT`, `DATABASE_CONN_POOL[_MIN_SIZE/_MAX_SIZE/_TIMEOUT]`,
`STORAGE_ENV` (+ the backend-specific `SWIFT_*` / `S3_*` / `MEDIA_ROOT`), `CHRIS_STORE_URL`,
`DJANGO_CORS_*`, **`NATS_ADDRESS`** (`:174`), **`PFDCM_ADDRESS`** (`:179`),
`CELERY_BROKER_URL` (`:184`), `DJANGO_SECURE_PROXY_SSL_HEADER`, `DJANGO_USE_X_FORWARDED_HOST`,
`AUTH_LDAP` (+ `AUTH_LDAP_*`), `DISABLE_USER_ACCOUNT_CREATION`.

`POLL_INTERVAL` for the Celery beat schedule is read from **`CUBE_CELERY_POLL_INTERVAL`** (default
`5.0` s) directly in `core/celery.py:56` (notably *not* via Django settings — the comment explains
this is due to a module-import ordering issue, `core/celery.py:55`).

### 5.3 DRF config (in `common.py:60-83`)

`PAGE_SIZE=10`, `LimitOffsetPagination`; renderers
`CollectionJsonRenderer → JSONRenderer → BrowsableAPIRenderer`; auth
`Token → Basic → Session`; filter backend `DjangoFilterBackend`; schema via drf-spectacular.
(See §02 for why this matters to DICOMweb's `application/dicom+json` break.)

### 5.4 Middleware (`common.py:85-96`)

Order matters: `corsheaders` first, then a custom `core.middleware.ResponseMiddleware`, then
security/whitenoise/session/CSRF/auth. `local.py` appends `debug_toolbar` (`:162`).

---

## 6. Celery — the worker fleet

CUBE's async work runs on Celery (`core/celery.py`). The broker is **Redis-compatible**
(DragonflyDB in dev: `redis://dragonflydb:6379/0`, `local.py:200`; env-driven in prod). Only JSON
serialization is accepted (`CELERY_ACCEPT_CONTENT=['json']`, `production.py:188`), and
`CELERYD_PREFETCH_MULTIPLIER=2` (`production.py:194`).

### 6.1 Three logical queues: `main1`, `main2`, `periodic` (+ test `celery`)

Task routing is declared in `core/celery.py:25-52`. The default `celery` queue is **only for the
automated tests** (`core/celery.py:24`).

| Queue | Tasks routed here | Why | line |
|---|---|---|---|
| **`main1`** | `run_plugin_instance_job`, `sum` (toy) | "hot path" — actually **submitting / launching** plugin jobs to pfcon | `celery.py:26-27` |
| **`main2`** | `check_plugin_instance_job_exec_status`, `cancel_plugin_instance_job`, `delete_plugin_instance_containers_from_remote`, `delete_plugin_instance`, `delete_feed`, `delete_folder`, `delete_pacs_series`, `send_pacs_query`, **`register_pacs_series`** | slower/secondary ops: status checks, cancellations, deletions, and **PACS registration / pfdcm queries** | `celery.py:28-50` |
| **`periodic`** | the beat-driven housekeeping tasks (scheduler, status sweep, cleanup, stuck-job recovery) | recurring cron-like work, isolated so it never starves job submission | `celery.py:31-44` |

**Why split `main1` vs `main2`?** So that the latency-sensitive act of *starting a job*
(`run_plugin_instance_job`, `main1`) is not blocked behind a backlog of *bookkeeping* work —
status polling, deletions, PACS registration — which all live on `main2`. Running the worker for
each queue as a separate process lets the deployment scale and isolate them independently. The
`periodic` queue is separated again so the beat-scheduled sweeps can't crowd out either.

> **Where does PACS DICOM registration run?** `pacsfiles.tasks.register_pacs_series` is on
> **`main2`** (`celery.py:50`). It validates the series metadata through `PACSSeriesSerializer`
> and saves it owned by user `chris`; pre-condition is that the `.dcm` files already exist in
> storage (`pacsfiles/tasks.py:48-94`). So oxidicom writes the files, then CUBE registers the
> series row on `main2`.

### 6.2 Beat (periodic) schedule

`app.conf.beat_schedule` (`core/celery.py:62-91`), with `POLL_INTERVAL` default 5 s
(`CUBE_CELERY_POLL_INTERVAL`):

| Task | Cadence | Job |
|---|---|---|
| `schedule_waiting_plugin_instances` | every `POLL_INTERVAL` (5 s) | move `waiting → scheduled/copying`, queue job |
| `check_running_plugin_instances_exec_status` | 5 s | poll pfcon for running jobs |
| `cancel_waiting_plugin_instances` | 5 s | honor cancellation requests |
| `handle_remote_cleanup` | 60 s | drive the remote-cleanup state machine |
| `cancel_plugin_instances_stuck_in_lock` | 7200 s (2 h) | recover instances stuck in `PluginInstanceLock` |
| `cancel_plugin_instances_stuck_in_scheduled_status` | 7200 s | recover wedged `scheduled` instances |
| `delete_plugin_instances_jobs_from_remote` | 7200 s | GC remote jobs |

The scheduler uses a `@skip_if_running` decorator (`plugininstances/tasks.py:31-54`) so overlapping
beat ticks don't double-run. `django_celery_beat` is installed (`common.py:40`) so schedules can
also be DB-managed.

### 6.3 Logging

`@setup_logging.connect` wires Celery's logging to Django's `LOGGING` dict
(`core/celery.py:94-96`). In dev, every app logs at DEBUG to console + `/tmp/debug.log`
(`local.py:82-89`).

---

## 7. URL routing — one central table, no per-app urls

- `ROOT_URLCONF = config.urls` (prod) / `config.local_urls` (dev) (`common.py:98`, `local.py:25`).
- `config/urls.py` mounts: `chris-admin/api/v1/...` (plugin & compute-resource admin views,
  `config/urls.py:25-40`), the drf-spectacular schema/docs, and `include('core.api')` for the
  public surface.
- **All** public REST routes are a single `format_suffix_patterns([...])` list in
  **`core/api.py`** (765 lines, ~138 `path(...)` entries) importing every app's views
  (`core/api.py:1-16`, registration starts `:19`). There are no per-app `urls.py` files.
- This is exactly why DICOMweb (Phase A) must mount a **separate** urlconf — it can't slot a new
  content type into this single collection+json router. (See §02.)
- WebSocket routes are separate: `core/websockets/urls.py`, wired in `config/asgi.py:18,30-34`
  behind `TokenQsAuthMiddleware` + `AllowedHostsOriginValidator`.

---

## 8. Auth & users

### 8.1 Authentication chain

DRF tries, in order, `TokenAuthentication → BasicAuthentication → SessionAuthentication`
(`common.py:74-78`). Tokens come from `POST /api/v1/auth-token/` (DRF's `obtain_auth_token`,
`core/api.py:20-23`). For WebSockets, a token-in-query-string middleware is used
(`TokenQsAuthMiddleware`, `config/asgi.py:19`).

### 8.2 LDAP (optional)

When `AUTH_LDAP` is true, `AUTHENTICATION_BACKENDS = (users.models.CustomLDAPBackend,
django.contrib.auth.backends.ModelBackend)` (`production.py:248-251`, dev `local.py:238-241`) —
LDAP first, then the local DB. Dev LDAP is the `lldap` container at `ldap://lldap:3890`
(`local.py:216`). `is_staff` is mirrored from the `chris_admin` LDAP group
(`AUTH_LDAP_USER_FLAGS_BY_GROUP`, `production.py:238-244`).

### 8.3 The user model is stock `auth.User` + a proxy

CUBE does **not** define a custom user table — it uses Django's `auth.User`. `users/models.py`
defines `UserProxy(User)` (proxy, `:35-40`) whose `save()` override, on first save
(`users/models.py:42-93`):
1. Assigns the new user to groups **`all_users`** and **`pacs_users`** (`:53-61`) — those groups
   must pre-exist or it errors.
2. Creates home folders `home/<user>/uploads` and `home/<user>/feeds` (`:64-72`).
3. Creates two seed link files `public → PUBLIC`, `shared → SHARED` (`:75-80`).
4. Writes a `welcome.txt` `UserFile` into uploads via `connect_storage(settings).upload_obj`
   (`:82-93`).

`CustomLDAPBackend.get_user_model()` returns `UserProxy` so LDAP-provisioned users get the same
home setup (`users/models.py:96-98`). Account creation through the API can be disabled with
`DISABLE_USER_ACCOUNT_CREATION` (`production.py:255`).

**Why `pacs_users` matters:** the SSE/LONK PACS-progress endpoints gate on it via
`IsChrisOrIsPACSUserReadOnly` (`pacsfiles/consumers.py:29`, `:34-37`). Membership in `pacs_users`
is what lets a normal user read PACS data — relevant for any DICOMweb authorization discussion.

---

## 9. NATS / LONK — the oxidicom integration (real-time DICOM ingest progress)

`nats-py==2.12.0` is a hard dependency (`../requirements/base.txt:20`). CUBE subscribes to
**oxidicom's** NATS messages to surface live ingest progress.

- **LONK** = "Light Oxidicom NotifiKations Encoding." Client implementation:
  `pacsfiles/lonk.py`. It connects to NATS (`LonkClient.connect`, `lonk.py:133-135`) and
  subscribes per-series on subject `oxidicom.<pacs_name>.<series_uid>`
  (`subject_of`, `lonk.py:164-170`, with dot/space/glob sanitization `:173-180`).
- Wire format is a binary magic-byte protocol decoded in `_serialize_to_lonkws`
  (`lonk.py:216-237`): `0x00`=DONE, `0x01`=PROGRESS (little-endian `ndicom` count), `0x02`=ERROR.
- CUBE re-exposes this two ways (`pacsfiles/consumers.py`):
  - **SSE** via `PACSFileProgressSSE` — a DRF `View` returning a `StreamingHttpResponse` of
    `text/event-stream` (`consumers.py:33-44`), pumping NATS messages into a per-request asyncio
    queue (`consumers.py:46-80`). Gated by `IsAuthenticated` + `IsChrisOrIsPACSUserReadOnly`.
  - **WebSocket** via an `AsyncJsonWebsocketConsumer` (Channels), routed through `config/asgi.py`.

This is the async glue that lets a UI/viewer show "received N of M DICOM instances" while a study
is being pushed in by oxidicom. It is also why CUBE must run on the ASGI/uvicorn stack (§0).

---

## 10. Migration history shape (data-model maturity signal)

`*/migrations/` initial-plus-N counts (excluding `__init__`):

| App | Migrations | Reading |
|---|---|---|
| `core` | 3 | the filesystem model is fairly settled |
| `feeds` | 3 | |
| `plugins` | 3 | |
| `plugininstances` | 5 | most-churned (status machine, retries, remote-cleanup added over time) |
| `pipelines` | 2 | |
| `userfiles` | 1 | thin proxy app, little schema of its own |
| `pacsfiles` | **8** | **most-migrated** — the PACS model has evolved the most (latest: `0008_pacsseries_deletion_error_and_more.py`, adding `AsyncDeletableModel` fields to `PACSSeries`) |
| `filebrowser` | 0 | **no models of its own** — it's a *view* app over `core` (browses the `ChrisFolder`/`ChrisFile` tree) |
| `users` | 1 | uses stock `auth.User` + proxy, so almost no migrations |
| `workflows` | 2 | |

Takeaways: (1) `filebrowser` and `userfiles` confirm the "everything is `core`'s filesystem"
design — they add behavior, not tables. (2) `pacsfiles` having 8 migrations (and the newest adding
async-deletion fields) shows PACS is the actively-evolving surface, consistent with the DICOMweb
work being layered on top.

---

## 11. End-to-end: how a DICOM file becomes a queryable thing

Tying §2-§9 together (the path a single `.dcm` travels):

1. An upstream PACS sends DICOM via C-STORE to **oxidicom** (Rust SCP, port 11111). CUBE is not
   involved yet.
2. oxidicom writes the `.dcm` bytes into the configured storage backend under
   `SERVICES/PACS/<pacs_name>/...` (the same container CUBE's `connect_storage` reads, §3).
3. oxidicom publishes LONK progress on NATS subject `oxidicom.<pacs>.<series_uid>`; CUBE's
   `LonkClient`/SSE relays it live to clients (§9).
4. The series is **registered** in Postgres by `pacsfiles.tasks.register_pacs_series` on the
   **`main2`** Celery queue (`pacsfiles/tasks.py:48-94`) — creating the `PACSSeries` row and its
   `ChrisFolder` (which auto-creates ancestor folders, §2.2).
5. The `.dcm` files are now visible as `PACSFile` rows — i.e. `ChrisFile` rows whose `fname`
   starts with `SERVICES/PACS/` (proxy scoping, §2.3). Their owning series is found by **folder
   path ancestry** (`get_first_existing_folder_ancestor`, §2.2), since there is no FK.
6. Reads go back through `connect_storage(settings).download_obj(path)` regardless of whether the
   bytes are in Swift, S3, or a plain directory (§3.3).

---

## 12. Q&A

**Q: Where do PACS DICOM files *physically* live?**
A: As **objects/files in the configured storage backend** (Swift, S3, or a POSIX directory),
under the path prefix `SERVICES/PACS/<pacs_identifier>/...`. They are *registered* as `ChrisFile`
rows (surfaced via the `PACSFile` proxy) in Postgres, but the **bytes are never in the DB** — only
the path metadata is. The backend is chosen by `STORAGE_ENV` and resolved by
`connect_storage(settings)` (`core/storage/helpers.py:12-24`).

**Q: Does CUBE store files in the database, the filesystem, or an object store?**
A: **Metadata in Postgres, bytes in pluggable storage.** The DB holds `ChrisFolder`/`ChrisFile`/
`ChrisLinkFile` rows (paths, owners, permissions). The actual bytes go to whatever
`StorageManager` backend is configured — `SwiftManager` (object store), `S3Manager`
(S3/MinIO/Ceph), or `FilesystemManager` (a literal directory at `MEDIA_ROOT`). Files are treated
as immutable/WORM (`core/storage/__init__.py:8-9`).

**Q: What's the difference between the `main1` and `main2` Celery queues?**
A: `main1` carries the **latency-sensitive job-launch** task (`run_plugin_instance_job`); `main2`
carries **everything secondary** — status checks, cancellations, all deletions, pfdcm queries, and
**PACS series registration**. Splitting them keeps a backlog of bookkeeping from delaying the act
of starting a compute job. A third queue, `periodic`, runs the beat-scheduled housekeeping so it
can't starve either. (`core/celery.py:25-52`.)

**Q: What actually runs the plugins?**
A: Not CUBE. CUBE dispatches to a remote **pfcon** service (which drives **pman** to schedule the
container) via `pfconclient` (`plugininstances/services/abstractjobs.py:11,41`). The target site
is a `ComputeResource` row (`compute_url`, auth token, in-network flag). CUBE only tracks the job's
state machine and shuttles files in/out for out-of-network sites.

**Q: How does CUBE know which compute resource / where to run a plugin?**
A: `Plugin.compute_resources` is M2M; a `PluginInstance.compute_resource` FK pins the chosen site.
The scheduler reads `compute_requires_copy_job` to decide whether to run a copy job first
(`plugininstances/tasks.py:197-210`).

**Q: Is there a per-instance table for individual DICOM instances (per `.dcm`)?**
A: Not in upstream CUBE — the finest metadata grain is `PACSSeries`; individual `.dcm` files exist
only as `ChrisFile`/`PACSFile` rows with no DICOM-tag columns. Adding a per-Instance level is
precisely the Phase A `dicomweb` work (see §02).

**Q: How are files linked/shared without copying?**
A: `ChrisLinkFile` — a stored `*.chrislink` text file whose content is the target path
(`core/models.py:911-929`). `SHARED/` and `PUBLIC/` folders are full of these.

**Q: Why does CUBE need to be async (uvicorn/ASGI)?**
A: For the real-time DICOM-ingest channels: the SSE endpoint (`StreamingHttpResponse`,
`text/event-stream`) and the LONK WebSocket consumer, both backed by NATS subscriptions to
oxidicom (`pacsfiles/consumers.py`, `pacsfiles/lonk.py`). The web entrypoint is
`uvicorn config.asgi:application` (`../Dockerfile:42`).

**Q: How is permission checking done — is it transitive at read time?**
A: No. Permissions are **denormalized**: granting on a folder bulk-creates permission rows on
every descendant folder/file/link at grant time (`core/models.py:447-487`). Reads check a direct
row. Trade-off: cheap reads, expensive (O(N)) grants on large trees.

**Q: What's the singleton `ChrisInstance` for?**
A: One-row table (`id` forced to 1) holding instance identity and `job_id_prefix` (`chris-jid-`)
used to name remote jobs (`core/models.py:75-108`, `82`).

**Q: Where's the URL routing — can we just add DICOMweb routes to an app?**
A: There are no per-app urlconfs; all public routes are one `format_suffix_patterns([...])` in
`core/api.py`. DICOMweb needs its own urlconf mounted from `config/urls.py` because it speaks a
different content type than the collection+json router. (§7, and §02.)

**Q: What broker does Celery use?**
A: Redis-protocol — DragonflyDB in dev (`redis://dragonflydb:6379/0`, `local.py:200`),
`CELERY_BROKER_URL` from env in prod (`production.py:184`). JSON-only serialization
(`production.py:188`).

**Q: What are the key infra env vars an operator must set in prod?**
A: `DJANGO_SECRET_KEY`, `POSTGRES_*`/`DATABASE_*`, `STORAGE_ENV` (+ backend creds),
`CELERY_BROKER_URL`, **`NATS_ADDRESS`**, **`PFDCM_ADDRESS`**, `DJANGO_ALLOWED_HOSTS`, and the
LDAP block if `AUTH_LDAP=true`. (`config/settings/production.py`.)

---

### Appendix: file map (all relative to `chris_backend/`)

| Concern | File |
|---|---|
| Filesystem model | `core/models.py` |
| Storage abstraction | `core/storage/{__init__,storagemanager,helpers,swiftmanager,s3manager,plain_fs}.py` |
| Celery app / queues / beat | `core/celery.py` |
| Central routing | `core/api.py`, `config/urls.py`, `config/asgi.py` |
| Settings | `config/settings/{common,local,production}.py` |
| Plugins / compute catalog | `plugins/models.py` |
| Plugin execution + state | `plugininstances/{models,tasks,enums}.py`, `plugininstances/services/*.py` |
| Feeds / provenance | `feeds/models.py` |
| Pipelines / workflows | `pipelines/models.py`, `workflows/models.py` |
| PACS data model | `pacsfiles/models.py` (see also KB §02) |
| PACS ingest progress | `pacsfiles/lonk.py`, `pacsfiles/consumers.py`, `pacsfiles/tasks.py` |
| Users / auth | `users/models.py`, settings auth blocks |
