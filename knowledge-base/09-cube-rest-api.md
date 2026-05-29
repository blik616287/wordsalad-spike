# 09 — CUBE's REST API (the surface clients use TODAY)

> **Audience**: Marty (ISC consultant) preparing for a BCH stakeholder meeting on adding
> DICOMweb to CUBE. This document describes the **existing** REST API — what every
> ChRIS client (the ChRIS UI / `figures`, `chrs` CLI, `python-chrisclient`, ChRIS_store
> integrations, ohif/Slicer-via-pfdcm) talks to **before** any DICOMweb work lands.
> Everything here is cross-checked against the on-disk source at
> `implementation/ChRIS_ultron_backEnd/chris_backend/`. Citations are `file:line`.
>
> Companion docs (do not duplicate — this goes deeper on the wire protocol):
> - `01-chris-architecture.md` — system topology, services, async flow.
> - `02-cube-and-pacs-data-model.md` — the DB models behind these endpoints.
> - `proposal-to-bch/CURRENT_API.md` — the gap-analysis baseline. **This doc supersedes
>   one claim in it** (the SSE endpoint *is* wired — see §7.3).

---

## 1. The 30-second mental model

CUBE is a **Django 5.1 + Django REST Framework (DRF)** application. Three facts shape
everything:

1. **The default content type is `application/vnd.collection+json`**, not plain JSON.
   Every list/detail response is wrapped in a Collection+JSON envelope by a custom
   renderer (`collectionjson/renderers.py`). Plain JSON is available on request.
2. **All routes are declared in one file**: `core/api.py`
   (`chris_backend/core/api.py:20`, a single `format_suffix_patterns([...])` list).
   There are **no per-app `urls.py` files**. When you add an endpoint (e.g. QIDO-RS),
   you register the view class here.
3. **It is HATEOAS / hypermedia-driven.** Clients are expected to start at the API root
   (`/api/v1/`) and follow `links` and `template` relations rather than hard-coding URL
   strings. This is *why* Collection+JSON was chosen (see §3.5).

Base paths:
- `/api/v1/…` — the public surface (mounted via `config/urls.py:44` → `include('core.api')`).
- `/chris-admin/api/v1/…` — plugin & compute-resource admin (`config/urls.py:27-40`).
- `/chris-admin/` — the Django admin site (`config/urls.py:42`).
- `/schema/`, `/schema/swagger-ui/`, `/schema/redoc/` — drf-spectacular OpenAPI
  (`config/urls.py:46-48`).

Scale of the surface (from the live `schema.yaml` dump, per `CURRENT_API.md:30`):
**141 path templates, 220 operations (140 GET, 29 POST, 25 PUT, 26 DELETE, 0 PATCH),
101 component schemas.** Note: **no PATCH anywhere** — partial updates are done with PUT.

---

## 2. Global DRF configuration (`config/settings/common.py:60-83`)

This single `REST_FRAMEWORK` dict drives auth, content negotiation, pagination, and
filtering for almost every view. Memorize it — most "why does X happen?" curve-balls
resolve here.

```python
REST_FRAMEWORK = {
    'PAGE_SIZE': 10,
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',
    'DEFAULT_RENDERER_CLASSES': (
        'collectionjson.renderers.CollectionJsonRenderer',   # DEFAULT content type
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ),
    'DEFAULT_PARSER_CLASSES': (
        'collectionjson.parsers.CollectionJsonParser',
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ),
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.BasicAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema'
}
```

Key consequences:
- The **first renderer is the default** — so an unqualified request gets Collection+JSON.
- All three auth schemes are tried **in order** on every request unless a view overrides
  `authentication_classes` (the binary `*-resource` download views do — §6).
- Pagination is **limit/offset**, page size **10** — clients must paginate.
- Filtering is opt-in per view via `filterset_class`; the backend is global.

---

## 3. Collection+JSON — the content type

### 3.1 What it is

Collection+JSON ([Amundsen's media type](http://amundsen.com/media-types/collection/),
`application/vnd.collection+json`) is a hypermedia JSON format. A response is a
`collection` object containing:
- `version`, `href` (self URL),
- `items[]` — each item has `href` (its own URL), `data[]` (name/value field pairs), and
  `links[]` (relations to other resources),
- `links[]` — document-level relations (e.g. `next`, `previous`, owner, parent),
- `queries[]` — described searches (the equivalent of a form for GET),
- `template` — a write form (name/value pairs the client fills in for POST/PUT),
- `total` — total count (when paginated),
- `error` — on errors, `{message: ...}`.

### 3.2 How the renderer works (`collectionjson/renderers.py`)

`CollectionJsonRenderer` **subclasses DRF's `JSONRenderer`** and overrides `render()`
(`renderers.py:163`). It transforms the serialized data *after* the serializer runs, then
delegates to `JSONRenderer.render()` for final byte encoding. Walkthrough:

- `render()` (`:163`) pulls `request`, `view`, `response` from `renderer_context`, and if
  there's data calls `_transform_data()`.
- `_transform_data()` (`:141`) builds `{"version": "1.0", "href": <absolute request URL>}`.
  - On `response.exception`, it injects an `error` block via `_get_error()` (`:102`), which
    uses `data['detail']` if present else the JSON-dumped payload.
  - Otherwise `_get_items_and_links()` (`:110`) does the work.
  - It then **pops** `queries`, `template` off the data into the collection, and maps
    `count` → `total` (`:152-159`).
- `_get_items_and_links()` (`:110`):
  - Pops `collection_links` into document-level `links`.
  - Special-cases the "Api Root" view name (the `/api/v1/` homepage) — emits links only.
  - Detects pagination by checking for the keys `('next','previous','results')`
    (`_is_paginated`, `:85`); if paginated, adds `next`/`previous` links and unwraps
    `results` into the item list.
  - `_transform_items()` (`:75`) maps each row through `_transform_item()`.
- `_transform_item()` (`:53`) is the core per-row transform:
  - It asks the serializer for its **URL/identity field name** (`_get_id_field`, `:23` —
    `url_field_name` for a `HyperlinkedModelSerializer`).
  - It finds **related/hyperlink fields** (`_get_related_fields`, `:31`) — any
    `HyperlinkedRelatedField`, `HyperlinkedIdentityField`, custom `ItemLinkField`, or a
    `ManyRelatedField` of hyperlinks.
  - **Scalar fields** become `data: [{name, value}, ...]`.
  - The **identity field** becomes the item's `href`.
  - **Related fields** become `links: [{rel, href}, ...]`.

This is why a CUBE serializer is full of `HyperlinkedIdentityField(view_name=...)` and
`HyperlinkedRelatedField(view_name=...)` — those become navigable `links` (e.g. a PACS's
`query_list`, `series_list` in `pacsfiles/serializers.py:25-27`).

### 3.3 Where `queries` / `template` / `links` come from

Views attach these via helper functions in `collectionjson/services.py`:
- `append_collection_links(response, link_dict)` (`services.py:24`) → `collection_links`.
- `append_collection_template(response, template_data)` (`services.py:37`) → a write
  `template` (the POST/PUT form fields).
- `append_collection_querylist(response, query_url_list)` (`services.py:48`) →
  **introspects the target view's `filterset_class.base_filters`** to auto-generate the
  list of searchable field names. This is how the `queries` block stays in sync with
  django-filter.

Example (PACS list view, `pacsfiles/views.py:37-45`): after the normal `list()`, it
appends a query relation pointing at `pacs-list-query-search`. The PACS Series list
(`views.py:381-398`) additionally appends a `template` enumerating every writable field
(`path`, `ndicom`, `PatientID`, … `pacs_name`).

### 3.4 The parser (`collectionjson/parsers.py`) — writes are templated too

`CollectionJsonParser` (`parsers.py:4`) subclasses `JSONParser`. On POST/PUT with
`Content-Type: application/vnd.collection+json`, it **expects** the body shape
`{"template": {"data": [{"name": ..., "value": ...}, ...]}}` and flattens it to a plain
`{name: value}` dict for the serializer (`parsers.py:7-27`). If the shape is wrong it
raises `ParseError` with the message
`"Valid format: {template:{data:[{name: ,value: },...]}}"`.

**Practical note for scripts**: you do **not** have to use Collection+JSON for writes. The
parser list also includes `JSONParser`, `FormParser`, `MultiPartParser`
(`common.py:69-72`). Sending `Content-Type: application/json` with a flat
`{"title": "...", ...}` body works and is far easier for ad-hoc curl/scripts.

### 3.5 Why Collection+JSON and not plain JSON? (rationale)

- **Generic hypermedia client.** ChRIS shipped a single JS client (`@fnndsc/chrisapi`)
  that knows the Collection+JSON grammar generically. It walks `links`/`queries`/`template`
  without per-endpoint URL knowledge, so the backend can move/rename URLs without breaking
  clients. (`renderers.py:113-115` even has a code comment acknowledging the Api-Root
  lookup is a "not the right long-term approach … works okay for now.")
- **Self-describing writes.** The `template` tells the client exactly which fields a POST
  accepts, so forms can be rendered without out-of-band schema knowledge.
- **Uniformity.** Every collection looks identical (`items`/`links`/`queries`/`template`),
  so pagination, search, and navigation are handled once in the client.
- **Cost / why it matters for DICOMweb**: DICOMweb clients (OHIF, Slicer) speak
  `application/dicom+json` (the DICOM JSON Model: tag-keyed objects with `vr`+`Value`),
  **not** Collection+JSON. So QIDO-RS endpoints will need a *new* renderer
  (likely `core/renderers.py` next to `BinaryFileRenderer`) and must bypass the
  Collection+JSON wrapping — they cannot reuse the default renderer. (`CURRENT_API.md:149`.)

### 3.6 A real example response

`GET /api/v1/pacs/1/` with the default content type produces (shape derived from
`PACSSerializer`, `pacsfiles/serializers.py:20-32`, run through the renderer):

```json
{
  "collection": {
    "version": "1.0",
    "href": "https://cube.example.org/api/v1/pacs/1/",
    "items": [
      {
        "href": "https://cube.example.org/api/v1/pacs/1/",
        "data": [
          {"name": "id", "value": 1},
          {"name": "identifier", "value": "MINICHRISORTHANC"},
          {"name": "active", "value": true},
          {"name": "folder_path", "value": "SERVICES/PACS/MINICHRISORTHANC"}
        ],
        "links": [
          {"rel": "folder",      "href": "https://cube.example.org/api/v1/filebrowser/12/"},
          {"rel": "query_list",  "href": "https://cube.example.org/api/v1/pacs/1/queries/"},
          {"rel": "series_list", "href": "https://cube.example.org/api/v1/pacs/1/series/"}
        ]
      }
    ],
    "links": []
  }
}
```

The same request with `Accept: application/json` (or `?format=json`) returns the **flat**
DRF representation instead:

```json
{
  "url": "https://cube.example.org/api/v1/pacs/1/",
  "id": 1,
  "identifier": "MINICHRISORTHANC",
  "active": true,
  "folder_path": "SERVICES/PACS/MINICHRISORTHANC",
  "folder": "https://cube.example.org/api/v1/filebrowser/12/",
  "query_list": "https://cube.example.org/api/v1/pacs/1/queries/",
  "series_list": "https://cube.example.org/api/v1/pacs/1/series/"
}
```

---

## 4. Authentication & permissions

### 4.1 Three authentication classes, tried in order (`common.py:74-78`)

| Class | Header / mechanism | Use case |
|---|---|---|
| `TokenAuthentication` | `Authorization: Token <40-hex-char-key>` | Scripts, CLIs, the ChRIS UI after login. |
| `BasicAuthentication` | `Authorization: Basic base64(user:pass)` | Dev/CI quick calls, e.g. `chris:chris1234`. |
| `SessionAuthentication` | Django session cookie + CSRF | The browsable HTML API at `/api/v1/…` in a browser. |

LDAP: `config/settings/local.py` wires `users.models.CustomLDAPBackend` before Django's
`ModelBackend` (an lldap dev server). LDAP affects *who* can authenticate, not the
transport; tokens are still issued the same way. (`CURRENT_API.md:37`.)

### 4.2 Getting a token — `POST /api/v1/auth-token/`

Routed at `core/api.py:21-24` to DRF's built-in `obtain_auth_token`. Send username +
password, get back a token key:

```sh
curl -s -X POST https://cube.example.org/api/v1/auth-token/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"chris","password":"chris1234"}'
# -> {"token":"3c0c...e9"}
```

Then use it on every subsequent call:

```sh
TOKEN=3c0c...e9
curl -s https://cube.example.org/api/v1/ \
  -H "Authorization: Token $TOKEN"
```

> **Note**: `auth-token` is the *only* endpoint registered with no `name=` in
> `core/api.py` and it is DRF's generic view. Tokens are long-lived (DRF default — no
> expiry) and stored in the `authtoken_token` table (`rest_framework.authtoken` is in
> `INSTALLED_APPS`, `common.py:42`).

### 4.3 The fourth, special auth path: download tokens (`core/views.py:124-173`)

Binary file downloads need to work from `<img>`/`<a>` tags that can't set an
`Authorization` header. CUBE solves this with **short-lived single-use JWTs passed in the
query string**:

1. `POST /api/v1/downloadtokens/` mints a JWT signed with `SECRET_KEY` (HS512),
   `exp = now + 10 minutes`, plus a random `nonce` (`core/views.py:48-57`).
2. The client appends `?download_token=<jwt>` to a `*-resource` binary URL.
3. `TokenAuthSupportQueryString` (`core/views.py:124-134`) extends `TokenAuthentication`
   to read `download_token` from the query string, validates the JWT
   (`authenticate_token`, `:137`), confirms the row exists, and **deletes it** —
   one-time use (`:159`).

These `*-resource` views override `authentication_classes` to add this query-string scheme
(e.g. `pacsfiles/views.py:491-492`).

### 4.4 Permission classes — how authorization works

DRF runs **all** of a view's `permission_classes` and requires every one to pass.
`has_permission(request, view)` runs for the whole view; `has_object_permission(request,
view, obj)` runs per object on detail/update/delete.

The `chris` superuser is a hard-coded special case throughout — many permission classes
short-circuit `if user.username == 'chris': return True`.

PACS-specific permission classes (`pacsfiles/permissions.py`):

| Class (`permissions.py`) | Rule | Applied to |
|---|---|---|
| `IsChrisOrIsPACSUserReadOnly` (`:5`) | `chris` → all; else read-only **iff** in `pacs_users` group | PACS list/detail, series, files (`views.py:35,95,106,119,379,415,426,448,469,480,490`) |
| `IsChrisOrIsPACSUserOrReadOnly` (`:21`) | Reads open to anyone; writes require `chris` or `pacs_users` | `PACSQueryList` (`views.py:150`) |
| `IsChrisOrOwnerOrIsPACSUserReadOnly` (`:36`) | per-object: `chris` or `obj.owner` → write; `pacs_users` → read | `PACSQueryDetail`, retrieves (`views.py:258,289,336,366`) |

The well-known group handle is the Django group **`pacs_users`**, created on demand via
`Group.objects.get_or_create(name='pacs_users')` (e.g. `pacsfiles/views.py:74`,
`serializers.py:163`). Membership = read access to PACS data; `chris` = write.

Other apps use a parallel family: `IsAuthenticated`, `IsAuthenticatedOrReadOnly`,
`IsOwnerOrChris`, `IsAdminOrReadOnly`, and the filebrowser's ACL-aware classes
(`IsOwnerOrChrisOrCanWriteOrCanReadOnlyOrPublicReadOnly`, etc.,
`filebrowser/views.py:37-40`) which consult per-folder/file group & user permission rows.

---

## 5. URL structure & the full endpoint surface

### 5.1 The convention

Every resource family follows the same naming pattern (all in `core/api.py`):

- `…/<resource>/` — **list** (GET) and often **create** (POST). View class `XList`.
- `…/<resource>/search/` — **filtered list** (GET only). View class `XListQuerySearch`,
  carries `filterset_class`.
- `…/<resource>/<int:pk>/` — **detail** (GET, sometimes PUT/DELETE). View class `XDetail`.
- `…/<resource>/<int:pk>/.<anything>` — **raw binary** for file-bearing resources
  (a `re_path(r'.../(?P<pk>[0-9]+)/.*$')`). View class `XResource`.

The split between `XList` (with `queryset`/`get_queryset`) and `XListQuerySearch` (with
`filterset_class`) is deliberate: the base list applies owner/permission scoping and
attaches the Collection+JSON `queries`/`template`, while `/search/` is the plain filtered
endpoint. **drf-spectacular tags each by the URL stem.**

### 5.2 The API root (`/api/v1/`) is the Feed list

`core/api.py:109-113` maps `v1/` → `feeds.views.FeedList`. The feed list **is** the
homepage (`feeds/views.py:281`, docstring: *"This is also the API's 'homepage'."*). The
renderer special-cases the "Api Root" view to emit only links (`renderers.py:123`).

### 5.3 Endpoint surface by app (counts per `CURRENT_API.md:53-72`)

| App / tag | Mounted at | What it exposes |
|---|---|---|
| **feeds** | `/api/v1/` (root) + `/api/v1/<id>/…` | Feed (root analysis entity), notes, comments, tags, taggings, per-feed group/user permissions, public feeds. |
| **plugins** | `/api/v1/plugins/…` | Plugin metas, plugins, plugin parameters, per-plugin compute resources. Read-only to clients (registration is admin-only). |
| **plugininstances** | `/api/v1/plugins/instances/…` | Plugin instances (a single plugin run), their splits, descendants, parameter values, and per-type parameter detail views (str/int/float/bool/path/unextpath). |
| **pipelines** | `/api/v1/pipelines/…` | Pipelines, pipings (the DAG edges), default parameters (str/int/float/bool), source files, custom JSON, and **workflows**. |
| **workflows** | `/api/v1/pipelines/workflows/…` | A workflow = one execution of a pipeline; lists the plugin instances it spawned. |
| **userfiles** | `/api/v1/userfiles/…` | User-uploaded files (upload via POST multipart, download via `*-resource`). |
| **pacsfiles** | `/api/v1/pacs/…` | PACS sources, queries, retrieves, series, files, SSE. (§7.) |
| **filebrowser** | `/api/v1/filebrowser/…` | Virtual folder tree over unified storage; folders, files, link-files, and ACLs. (§8.) |
| **users** | `/api/v1/users/…`, `/api/v1/groups/…` | User create/detail/groups; group membership CRUD. |
| **core** | `/api/v1/downloadtokens/…`, `/api/v1/chrisinstance/<id>/` | Download tokens; the singleton describing this CUBE deployment. |
| **plugins admin** | `/chris-admin/api/v1/…` | Register a plugin (POST `description.json`); manage compute resources. |

### 5.4 Key endpoints table (method, path, auth, purpose)

> Auth column: **Auth** = any of Token/Basic/Session required (`IsAuthenticated`);
> **Auth\*** = open read / authenticated write (`IsAuthenticatedOrReadOnly`);
> **PACS** = PACS permission family (§4.4); **DLtoken** = also accepts `?download_token`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth-token/` | none | Username+password → token (`core/api.py:21`). |
| GET | `/api/v1/` | Auth | API root = feed list (`core/api.py:109`). |
| GET | `/api/v1/search/` | Auth | Feed query-search (`core/api.py:116`). |
| GET/PUT/DEL | `/api/v1/<id>/` | Auth | Feed detail (`core/api.py:121`). |
| GET | `/api/v1/publicfeeds/` | Auth\* | Public feeds (`core/api.py:242`). |
| GET | `/api/v1/plugins/` | Auth | Plugin list (`core/api.py:299`). |
| GET | `/api/v1/plugins/<id>/` | Auth | Plugin detail (`core/api.py:311`). |
| GET/POST | `/api/v1/plugins/<id>/instances/` | Auth | List / run a plugin (`core/api.py:421`). |
| GET | `/api/v1/plugins/instances/<id>/` | Auth | Plugin-instance detail (`core/api.py:433`). |
| GET | `/api/v1/pipelines/` | Auth | Pipelines (`core/api.py:337`). |
| GET/POST | `/api/v1/pipelines/<id>/workflows/` | Auth | List / launch a workflow (`core/api.py:478`). |
| GET/POST | `/api/v1/userfiles/` | Auth | List / upload user files (`core/api.py:499`). |
| GET | `/api/v1/userfiles/<id>/.…` | Auth + DLtoken | Download a user file (`core/api.py:511`). |
| GET | `/api/v1/pacs/` | PACS | List PACS (reconciles w/ pfdcm — §7.2) (`core/api.py:516`). |
| GET | `/api/v1/pacs/<id>/series/` | PACS | Series for a PACS (`core/api.py:554`). |
| GET/POST | `/api/v1/pacs/series/` | PACS | List series / **registration callback** (§7.4) (`core/api.py:558`). |
| GET/DEL | `/api/v1/pacs/series/<id>/` | PACS | Series detail / async delete (202) (`core/api.py:566`). |
| GET | `/api/v1/pacs/files/<id>/.…` | PACS + DLtoken | Download one `.dcm` (`core/api.py:582`). |
| GET/POST | `/api/v1/pacs/sse/` | PACS | SSE ingest-progress stream (§7.3) (`core/api.py:586`). |
| GET | `/api/v1/filebrowser/` | Auth\* | Root folder (`core/api.py:592`). |
| GET | `/api/v1/filebrowser/<id>/children/` | Auth\* | Subfolders (`core/api.py:611`). |
| GET | `/api/v1/filebrowser/<id>/files/` | Auth\* | Files in folder (`core/api.py:652`). |
| GET | `/api/v1/filebrowser/files/<id>/.…` | Auth\* + DLtoken | Download a file (`core/api.py:700`). |
| POST | `/api/v1/users/` | open* | Create a user (`core/api.py:27`). |
| GET | `/api/v1/chrisinstance/<id>/` | Auth | This CUBE's identity (`core/api.py:102`). |
| POST | `/api/v1/downloadtokens/` | Auth | Mint a 10-min download token (`core/api.py:84`). |
| POST | `/chris-admin/api/v1/` | admin | Register a plugin (`config/urls.py:27`). |

\* User creation is gated by `settings.DISABLE_USER_ACCOUNT_CREATION` (`users/views.py:17`).

---

## 6. Pagination, filtering, search, and binary downloads

### 6.1 Pagination — limit/offset, page size 10

Global `LimitOffsetPagination`, `PAGE_SIZE=10` (`common.py:60-62`). Clients send
`?limit=N&offset=M`. The paginated payload is `{count, next, previous, results}`; the
Collection+JSON renderer detects this (`renderers.py:85-87`), surfaces `count` as `total`,
and turns `next`/`previous` into document-level `links`.

```sh
curl -s "https://cube.example.org/api/v1/pacs/series/?limit=50&offset=100" \
  -H "Authorization: Token $TOKEN" -H 'Accept: application/json'
```

### 6.2 Filtering — django-filter, exposed via `/search/`

`DjangoFilterBackend` is the only global filter backend (`common.py:79-81`). Each
`*ListQuerySearch` view sets `filterset_class` pointing at a `FilterSet` defined in the
app's `models.py`. The `queries` block in Collection+JSON is auto-generated by reflecting
`filterset_class.base_filters` (`services.py:48-62`), so the advertised search fields
always match the code.

Filter idioms you'll see (from `pacsfiles/models.py`):
- `min_creation_date` / `max_creation_date` → `IsoDateTimeFilter` with `gte`/`lte`
  (`models.py:199-202`).
- `icontains` for human-text fields like `PatientName`, `StudyDescription`
  (`models.py:203-210`).
- Range filters `min_PatientAge`/`max_PatientAge` (`models.py:211-214`).
- Custom **method filters** for special cases, e.g. `PACSFileFilter.fname_nslashes` and
  `fname_icontains_topdir_unique` (`models.py:264-266`) which drive the UI's folder-tree
  collapsing.

### 6.3 Search example

```sh
curl -s "https://cube.example.org/api/v1/pacs/series/search/?PatientID=12345&Modality=&StudyDate=2024-01-15" \
  -H "Authorization: Token $TOKEN" -H 'Accept: application/json'
```

### 6.4 Binary download endpoints (`*-resource`)

The only routes where non-JSON payloads cross the API. They use a `re_path` so any
trailing path matches (`core/api.py:511,582,700`), use `BinaryFileRenderer`
(`pacsfiles/views.py:489`), stream via Django `FileResponse`, set
`Content-Disposition: attachment` (`pacsfiles/views.py:500-503`), and add the
`?download_token` auth scheme. Example with a download token:

```sh
# 1) mint token
TOK=$(curl -s -X POST https://cube.example.org/api/v1/downloadtokens/ \
  -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' -d '{}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
# 2) download (note the trailing filename and the query-string token)
curl -s -o slice.dcm \
  "https://cube.example.org/api/v1/pacs/files/42/slice.dcm?download_token=$TOK"
```

---

## 7. The PACS surface (deep dive)

This is the surface DICOMweb work touches. Sources: `pacsfiles/{views,serializers,
models,permissions,services,consumers}.py` and `core/api.py:516-589`.

### 7.1 Endpoint matrix

| Path | Methods | View / notes |
|---|---|---|
| `/api/v1/pacs/` | GET | `PACSList` — **reconciles with pfdcm on every list** (§7.2). |
| `/api/v1/pacs/search/` | GET | `PACSListQuerySearch`; `PACSFilter`: `id`, `identifier`, `active` (`models.py:45`). |
| `/api/v1/pacs/<id>/` | GET | `PACSDetail`. |
| `/api/v1/pacs/<id>/queries/` | GET, POST | `PACSQueryList` — POST a query; `execute=true` → async `send_pacs_query` (`views.py:183-192`). |
| `/api/v1/pacs/queries/` | GET | `AllPACSQueryList` (owner-scoped unless `pacs_users`/`chris`, `views.py:218-227`). |
| `/api/v1/pacs/queries/search/` | GET | `AllPACSQueryListQuerySearch`; `PACSQueryFilter` (`models.py:89-107`). |
| `/api/v1/pacs/queries/<id>/` | GET, PUT, DELETE | `PACSQueryDetail` — PUT can flip `execute` false→true to (re)dispatch (`views.py:268-279`); `query` becomes read-only after create (`serializers.py:55-56`). |
| `/api/v1/pacs/queries/<id>/retrieves/` | GET, POST | `PACSRetrieveList` — POST calls `pacs_retrieve.send()` (→ pfdcm C-MOVE) (`views.py:318-326`). |
| `/api/v1/pacs/queries/<id>/retrieves/search/` | GET | `PACSRetrieveListQuerySearch`; `PACSRetrieveFilter` (`models.py:148-158`). |
| `/api/v1/pacs/queries/retrieves/<id>/` | GET, DELETE | `PACSRetrieveDetail`. |
| `/api/v1/pacs/<id>/series/` | GET | `PACSSpecificSeriesList` (`views.py:112`). |
| `/api/v1/pacs/series/` | GET, **POST** | `PACSSeriesList` — POST is the **oxidicom registration callback** (§7.4). |
| `/api/v1/pacs/series/search/` | GET | `PACSSeriesListQuerySearch`; `PACSSeriesFilter` (§7.5). |
| `/api/v1/pacs/series/<id>/` | GET, DELETE | `PACSSeriesDetail` — DELETE marks deletion-pending, dispatches `delete_pacs_series`, returns **202 Accepted** (`views.py:428-439`). |
| `/api/v1/pacs/files/` | GET | `PACSFileList` — every file under `SERVICES/PACS/` (`views.py:446`). |
| `/api/v1/pacs/files/search/` | GET | `PACSFileListQuerySearch`; `PACSFileFilter` (`models.py:255-270`). |
| `/api/v1/pacs/files/<id>/` | GET | `PACSFileDetail` (metadata). |
| `/api/v1/pacs/files/<id>/.…` | GET | `PACSFileResource` — **binary `.dcm` download** (`views.py:483-503`). |
| `/api/v1/pacs/sse/` | GET, POST | `PACSFileProgressSSE` — DICOM-reception SSE (§7.3). |

### 7.2 `/api/v1/pacs/` reconciles with pfdcm — and why it 500s when pfdcm is down

`PACSList.get_queryset()` (`pacsfiles/views.py:47-85`) is **not** a plain DB read. On every
GET it:
1. Reads existing PACS rows.
2. Calls `PfdcmClient().get_pacs_list()` → `GET http://pfdcm:4005/api/v1/PACSservice/list/`
   (`services.py:23,27-43`).
3. Marks each PACS `active`/inactive based on whether pfdcm still lists it.
4. **Auto-creates** any PACS name pfdcm knows that CUBE doesn't, including a
   `SERVICES/PACS/<name>/` `ChrisFolder` granted read to `pacs_users` (`views.py:71-84`).

`PfdcmClient.get_pacs_list()` retries 5× with 0.4s backoff, then **re-raises** the
`requests` exception (`services.py:33-41`). That exception propagates out of
`get_queryset()` and DRF turns it into an **HTTP 500** — so `GET /api/v1/pacs/` returns 500
when `pfdcm` is unreachable or `PFDCM_ADDRESS` (`local.py:195` = `http://pfdcm:4005`,
production via `get_secret('PFDCM_ADDRESS')`, `production.py:179`) is wrong. This is a real
operational gotcha: the *list* endpoint has a hard runtime dependency on a second service.
(DICOMweb QIDO is a pure read of CUBE's own DB and would not have this coupling.)

### 7.3 The SSE endpoint `/api/v1/pacs/sse/` (`pacsfiles/consumers.py`)

> **Correction to `CURRENT_API.md:114`**: it claims the SSE endpoint is "declared … outside
> `format_suffix_patterns`" and the WebSocket consumer "appears to be unwired." Reading the
> source: `PACSFileProgressSSE` **is wired** at `core/api.py:586-589` (inside the
> `format_suffix_patterns([...])` list, though with **no `name=`** and no
> `serializer_class`, which is why it doesn't appear in the OpenAPI schema). The
> `PACSFileProgress` **WebSocket** consumer (`consumers.py:196`) is the one with no URL
> route in `core/api.py` — it would be routed via the ASGI/channels routing
> (`config/asgi.py`), not the HTTP urlconf.

`PACSFileProgressSSE` (`consumers.py:35`) is a plain Django `View` (not a DRF generic) that
returns a `StreamingHttpResponse` with `content_type="text/event-stream"` on both GET and
POST (`consumers.py:41-45`). Flow (`pacs_file_progress_sse`, `:47-92`):
1. Parse query params `pacs_name` and `series_uids` (comma-separated) (`_get_info`, `:164`).
2. Connect to **NATS** at `settings.NATS_ADDRESS` (`local.py:192` = `nats://nats:4222`)
   via `LonkClient` (`:94-106`).
3. Subscribe to a NATS subject per series UID; push messages onto an asyncio queue
   (`_subscribe`, `:108-115`).
4. Loop: drain the queue, emit each as an SSE frame `event: message\ndata: <json>\n\n`
   (`_event_response`, `:128`), tracking per-series progress until all are `done`/`error`.
5. Emit a final `{message:{done:true}}` or `{message:{error:...}}` frame and close NATS.

These NATS messages are published by **oxidicom** (the Rust C-STORE SCP) as it writes each
incoming instance. The SSE stream is how the UI shows a live "received N/M images" bar
during a PACS pull. If NATS is unreachable, the stream yields a single error frame and
returns (it does **not** 500 — `:54-58`).

### 7.4 The registration handshake: `POST /api/v1/pacs/series/`

This is **not** researcher-facing — it's the callback oxidicom (or a registration service)
fires after C-STORE has written a series to storage. `PACSSeriesSerializer.create()`
(`serializers.py:152-222`) and `validate()` (`:258-301`) do the heavy lifting:

- The POST body carries `path`, `ndicom` (expected file count), the DICOM tag fields, and
  `pacs_name` (the write-only fields, `serializers.py:133-137`).
- `validate_path` enforces the path is under `SERVICES/PACS/` and comma-free
  (`:224-237`); `validate` enforces it starts with `SERVICES/PACS/<pacs_name>/` (`:264-270`).
- `validate()` then **polls storage for up to 30 seconds** (1s intervals) waiting for
  `ndicom` `.dcm` files to materialize under `path` (`:278-300`). Too few → keep waiting;
  too many → `ValidationError`; still short at 30s → `ValidationError`. This handles the
  race between oxidicom finishing writes and the registration call.
- On success it gets-or-creates the `PACS` row and `SERVICES/PACS/<name>/` folder
  (creating the `pacs_users` group grant), enforces `(pacs, SeriesInstanceUID)` uniqueness,
  sanitizes filenames (strips commas), and **bulk-creates** the `PACSFile` rows
  (`:177-222`).

### 7.5 `PACSSeries` model & filter coverage (`pacsfiles/models.py:160-225`)

Stored, indexable DICOM tags on `PACSSeries`:

| Level | Columns (`models.py:160-178`) |
|---|---|
| Patient | `PatientID` (indexed), `PatientName`, `PatientBirthDate`, `PatientAge` (int), `PatientSex` (`M`/`F`/`O`) |
| Study | `StudyDate` (indexed), `AccessionNumber` (indexed), `StudyInstanceUID`, `StudyDescription` |
| Series | `Modality`, `ProtocolName`, `SeriesInstanceUID` (indexed), `SeriesDescription` |
| Instance / SOP | **none** — no `SOPInstanceUID`/`SOPClassUID`/`InstanceNumber` columns; instance metadata lives only inside the `.dcm` files on disk. |

Unique constraint: `(pacs, SeriesInstanceUID)` (`models.py:182`). Each `PACSSeries` owns
one `ChrisFolder` (one-to-one, `models.py:176`); `PACSFile` is a **proxy model** over
`ChrisFile` filtered to `fname__startswith='SERVICES/PACS/'` (`models.py:229-242`).

`PACSSeriesFilter` (`models.py:198-224`) exposes (all **exact** except the four
`icontains` text fields and the date/age ranges):
```
id, min_creation_date, max_creation_date, PatientID, PatientName(icontains),
PatientSex, PatientAge, min_PatientAge, max_PatientAge, PatientBirthDate, StudyDate,
AccessionNumber, ProtocolName(icontains), StudyInstanceUID, StudyDescription(icontains),
SeriesInstanceUID, SeriesDescription(icontains), pacs_id, pacs_identifier,
deletion_status  (+ limit, offset)
```

**Why this matters for QIDO-RS**: there is no Study-level rollup model and no Instance
row. QIDO needs `/studies`, `/studies/{uid}/series`, `/series/{uid}/instances`, multi-value
filters (`?00080060=CT,MR`), tag-hex parameter names, date ranges, and the DICOM JSON
Model response. None of those exist today — see the full gap table in `CURRENT_API.md:144-153`.

---

## 8. The filebrowser API & the ChrisFolder filesystem

### 8.1 The model

CUBE presents **one unified virtual filesystem** over its object store
(swift / S3 / fslink, abstracted by `core/storage`). The entities (`core/models.py`):
- `ChrisFolder` (`models.py:111`) — a node with a `path` and a self-referential `parent`
  FK (`models.py:115`). On save it **recursively creates parent folders**
  (`models.py:131-144`). Every PACS series, feed output, and user upload lives under some
  folder. Top-level namespaces include `SERVICES/PACS/…`, `home/…`, `PUBLIC/…`, `SHARED/…`.
- `ChrisFile` — a stored file (`fname` is the storage key). `PACSFile` is a proxy of it.
- `ChrisLinkFile` — a `.chrislink` symlink-like pointer used for sharing
  (`models.py:298-363`).
- Per-folder / per-file / per-link **group and user permission** rows (the ACL primitives).

### 8.2 The API (`filebrowser/views.py`, 28 path templates)

| Path | Methods | Purpose |
|---|---|---|
| `/api/v1/filebrowser/` | GET, POST | Root listing (single element) / create a folder (`views.py:48`). |
| `/api/v1/filebrowser/search/` | GET | `ChrisFolderFilter` (`id`, `path`). |
| `/api/v1/filebrowser/<id>/` | GET, PUT, DELETE | Folder detail / rename-move / async delete (`views.py:127`). |
| `/api/v1/filebrowser/<id>/children/` | GET | Subfolders (`views.py:168`). |
| `/api/v1/filebrowser/<id>/files/` | GET | Files directly in the folder (`views.py:410`). |
| `/api/v1/filebrowser/<id>/linkfiles/` | GET | Link files in the folder. |
| `/api/v1/filebrowser/files/<id>/` | GET, PUT, DELETE | File metadata (`views.py:440`). |
| `/api/v1/filebrowser/files/<id>/.…` | GET | **Binary download** (`views.py:459`, `core/api.py:700`). |
| `…/<id>/grouppermissions/`, `…/userpermissions/` (+ `/search/`) | GET, POST | Per-folder / per-file ACL management. |

The folder serializer (`filebrowser/serializers.py:17-35`) exposes `path`, `public`,
`owner_username`, and hyperlink relations to `children`, `files`, `link_files`,
`group_permissions`, `user_permissions` — i.e. the client navigates the tree by following
Collection+JSON `links`.

Permissions here are the most nuanced in CUBE: `IsAuthenticatedOrReadOnly` plus an
ACL-aware object permission (`IsOwnerOrChrisOrCanWriteOrCanReadOnlyOrPublicReadOnly`,
`views.py:37,55,134,175,447,466`). A `public=true` folder/file is world-readable; otherwise
owner + granted groups/users only.

### 8.3 Why DICOMweb cares

PACS `.dcm` files already live in this tree under `SERVICES/PACS/<pacs>/<study>/<series>/`.
WADO-RS retrieval can reuse `core.storage.connect_storage()` (the same abstraction the
`*-resource` download views and the series-registration serializer use,
`pacsfiles/serializers.py:190,276`) to stream instances — the storage plumbing is already
there; what's missing is the DICOMweb routing, `multipart/related` packaging, and frame
extraction (`CURRENT_API.md:151`).

---

## 9. drf-spectacular: schema generation & its quirks

CUBE generates an **OpenAPI 3.0.3** spec with drf-spectacular
(`DEFAULT_SCHEMA_CLASS = 'drf_spectacular.openapi.AutoSchema'`, `common.py:82`). Served at
`/schema/` (+ `/schema/swagger-ui/`, `/schema/redoc/`, `config/urls.py:46-48`). Title:
*"ChRIS Research Integration System: Ultron BackEnd (CUBE) API"* (`common.py:182`).

Settings of note (`common.py:181-222`):
- `SCHEMA_PATH_PREFIX = '/api/v1/'`.
- `COMPONENT_SPLIT_REQUEST` toggled by env `SPECTACULAR_SPLIT_REQUEST` — the split form is
  what client codegen uses (`CURRENT_API.md:10`).
- Enum name overrides for plugin types, parameter types, plugin-instance status, and
  **PACS query status** (`common.py:202-207`).

### 9.1 The big quirk: the schema describes *plain JSON*, not Collection+JSON

A `POSTPROCESSING_HOOK`, `collectionjson.spectacular_hooks.postprocess_remove_collectionjson`
(`common.py:214`, source `spectacular_hooks.py:3-16`), **deletes every
`application/vnd.collection+json` content entry** from request bodies and responses, and
strips the `?format=collection+json|json` query parameter. So:

> **The OpenAPI spec documents the underlying flat JSON shape — *not* the Collection+JSON
> envelope the default renderer actually returns.** A code-generated client built from the
> schema must request `Accept: application/json` (or the generated client sets it), or it
> must know to unwrap the `collection` envelope itself. This mismatch trips people up.

Other post-processing hooks:
- `plugininstances.spectacular_hooks.additionalproperties_for_plugins_instances_create`
  (`common.py:215`) — plugin-instance create accepts arbitrary plugin parameters, so the
  schema is patched to allow `additionalProperties`.
- `filebrowser.spectacular_hooks.nonrequired_fields` (`common.py:216`).

### 9.2 Things missing from / fuzzy in the schema

- **`/api/v1/pacs/sse/` is absent** — it's a plain `View` with no `serializer_class` and no
  route `name`, so AutoSchema can't introspect it (`consumers.py:35`, `core/api.py:586`).
  Any DICOMweb endpoints built as bare streaming views will need explicit `@extend_schema`
  annotations to appear.
- **Three generation-time warnings**, all in `pacsfiles/views.py` — harmless
  introspection fallbacks where DRF can't infer a serializer for the write-only callback
  fields (`CURRENT_API.md:23`).
- **Custom `operation_id` overrides**: several PACS/feeds views use `@extend_schema_view`
  to rename operations (e.g. `pacs_series_list`, `all_pacs_query_list`,
  `pacs_query_list`, `views.py:109-111,140-142,195-197,369-371`) to avoid collisions
  between the `<id>/series/` and `series/` style routes that resolve to similarly-named
  views.
- **Version reports `0.0.0+unknown`** in dev because `__version__.py` reads a git tag the
  loose clone doesn't have (`CURRENT_API.md:12`) — cosmetic.
- The `BinaryFileRenderer` resource endpoints are annotated
  `@extend_schema(responses=OpenApiResponse(OpenApiTypes.BINARY))` so they show as binary
  downloads (`pacsfiles/views.py:494`).

---

## 10. Worked curl examples (end to end)

```sh
BASE=https://cube.example.org
# --- auth ---
TOKEN=$(curl -s -X POST $BASE/api/v1/auth-token/ -H 'Content-Type: application/json' \
  -d '{"username":"chris","password":"chris1234"}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
AUTH="Authorization: Token $TOKEN"

# --- list PACS (note: hits pfdcm; 500 if pfdcm down) ---
curl -s $BASE/api/v1/pacs/ -H "$AUTH" -H 'Accept: application/json'

# --- search series, flat JSON ---
curl -s "$BASE/api/v1/pacs/series/search/?PatientID=12345&limit=20" \
  -H "$AUTH" -H 'Accept: application/json'

# --- create a PACS query (plain JSON write — no Collection+JSON template needed) ---
curl -s -X POST $BASE/api/v1/pacs/1/queries/ -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"title":"my-find","query":{"PatientID":"12345"},"execute":true}'

# --- the same write, the Collection+JSON way (what the JS client sends) ---
curl -s -X POST $BASE/api/v1/pacs/1/queries/ -H "$AUTH" \
  -H 'Content-Type: application/vnd.collection+json' \
  -d '{"template":{"data":[
        {"name":"title","value":"my-find"},
        {"name":"query","value":{"PatientID":"12345"}},
        {"name":"execute","value":true}]}}'

# --- subscribe to ingest progress (SSE) ---
curl -sN "$BASE/api/v1/pacs/sse/?pacs_name=MINICHRISORTHANC&series_uids=1.2.3,4.5.6" \
  -H "$AUTH"

# --- download one DICOM instance via short-lived token ---
TOK=$(curl -s -X POST $BASE/api/v1/downloadtokens/ -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{}' \
  | python -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -o slice.dcm "$BASE/api/v1/pacs/files/42/slice.dcm?download_token=$TOK"

# --- grab the OpenAPI spec ---
curl -s $BASE/schema/ -H "$AUTH" > schema.yaml
```

---

## 11. Curve-ball Q&A

**Q: Why Collection+JSON instead of plain JSON?**
A: ChRIS standardized on a generic hypermedia client (`@fnndsc/chrisapi`) that walks
`links`/`queries`/`template` rather than hard-coding URLs, so the backend can evolve URLs
without breaking clients; the `template` block self-describes writes. It's wrapped by
`CollectionJsonRenderer` (`collectionjson/renderers.py:163`). Plain JSON is always
available — set `Accept: application/json` or `?format=json` (it's the second renderer,
`common.py:64-65`). For DICOMweb this is moot: OHIF/Slicer need `application/dicom+json`,
which is a *third* renderer we'd add.

**Q: How does auth work for a script (no browser)?**
A: `POST /api/v1/auth-token/` with username+password → a long-lived token; then send
`Authorization: Token <key>` on every call. Or just use HTTP Basic
(`Authorization: Basic …`, e.g. `chris:chris1234` in dev) — both are enabled
(`common.py:74-78`). For file downloads from contexts that can't set headers (an `<img>`
tag), mint a 10-minute one-time JWT via `POST /api/v1/downloadtokens/` and pass it as
`?download_token=…` (`core/views.py:124-160`).

**Q: What does `GET /api/v1/pacs/` return, and why did it 500 when pfdcm was unreachable?**
A: It returns the list of registered PACS sources. But `PACSList.get_queryset()` isn't a
plain DB read — on every call it calls pfdcm's `/api/v1/PACSservice/list/` to reconcile
which PACS are active and to auto-create new ones (`pacsfiles/views.py:47-85`).
`PfdcmClient.get_pacs_list()` retries 5× then **re-raises** the connection error
(`services.py:33-41`), which DRF renders as **HTTP 500**. So the list endpoint has a hard
runtime dependency on pfdcm at `PFDCM_ADDRESS` (`local.py:195`). A QIDO-RS `/studies` read
would query CUBE's own DB and wouldn't have this coupling.

**Q: Can I add new endpoints in my app's own `urls.py`?**
A: No — there are no per-app urlconfs. Everything is registered centrally in
`core/api.py:20` inside one `format_suffix_patterns([...])`. DICOMweb routes go there
(e.g. a `dicomweb` app's views imported and `path(...)`-ed in `core/api.py`).

**Q: Does the OpenAPI schema tell me the real response shape?**
A: Partially. A post-processing hook strips Collection+JSON from the spec
(`spectacular_hooks.py:3-16`), so the schema describes the **flat JSON** body, not the
`collection{…}` envelope the default renderer returns. Generate clients against
`application/json`, or unwrap the envelope. Also, streaming views like `/pacs/sse/` aren't
in the schema at all.

**Q: Why is there no PATCH anywhere?**
A: CUBE only uses `RetrieveUpdateDestroyAPIView` / `RetrieveUpdateAPIView` with
`http_method_names` restricted to `['get','put','delete']` (e.g. `views.py:255,419`).
Updates are full PUTs; the spec confirms 0 PATCH operations (`CURRENT_API.md:30`).

**Q: How does the `pacs_users` group gate access?**
A: It's the well-known Django group created on demand
(`Group.objects.get_or_create(name='pacs_users')`, `views.py:74`). The PACS permission
classes (`pacsfiles/permissions.py`) grant **read** to its members and **write** only to
`chris`. There's no per-PACS-source ACL — membership is all-or-nothing across PACS data,
with per-object owner checks only on queries/retrieves.

**Q: How does a series get registered after a C-STORE push?**
A: oxidicom writes the `.dcm` files to storage and publishes NATS progress events; a
registration call then `POST`s to `/api/v1/pacs/series/` with the tags, the target `path`,
and `ndicom`. The serializer **polls storage up to 30s** for the expected file count before
bulk-creating rows (`serializers.py:258-301`). The browser sees live progress over
`/api/v1/pacs/sse/` (NATS → SSE, `consumers.py:47-92`).

**Q: How are large DICOM payloads served today vs. what WADO-RS needs?**
A: Today: one `.dcm` at a time via `GET /api/v1/pacs/files/<id>/.…` →
`BinaryFileRenderer` + `FileResponse` as `application/octet-stream`
(`views.py:483-503`). WADO-RS needs `multipart/related` study/series bundles, `/metadata`,
`/frames/{n}`, `/rendered`, `/thumbnail`. The storage layer (`core.storage.connect_storage`)
already streams; the work is routing + packaging + frame extraction (`CURRENT_API.md:151`).

**Q: Is there a conformance/capabilities endpoint?**
A: No. CUBE has no DICOMweb capabilities document. OHIF tolerates its absence; some clients
expect `GET /` to return a conformance statement (`CURRENT_API.md:153`).

---

## 12. Sources

- Routes: `chris_backend/core/api.py` (single registry, lines 20-766); project urlconf
  `chris_backend/config/urls.py`.
- Global DRF config & spectacular settings: `chris_backend/config/settings/common.py:60-222`;
  service addresses `config/settings/local.py:192,195`, `config/settings/production.py:174,179`.
- Collection+JSON: `chris_backend/collectionjson/{renderers,parsers,services,spectacular_hooks}.py`.
- Auth & download tokens: `chris_backend/core/views.py`.
- PACS: `chris_backend/pacsfiles/{views,serializers,models,permissions,services,consumers}.py`.
- Filebrowser & filesystem: `chris_backend/filebrowser/{views,serializers}.py`,
  `chris_backend/core/models.py`.
- Baseline gap analysis: `proposal-to-bch/CURRENT_API.md`, `proposal-to-bch/schema.yaml`.
- Companion KB: `knowledge-base/01-chris-architecture.md`, `02-cube-and-pacs-data-model.md`.
```
