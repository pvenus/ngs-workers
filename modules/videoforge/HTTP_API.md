# VideoForge local HTTP contract

Start only when the frontend is ready to use the service:

```sh
pip install -e '.[http]'
videoforge-http
```

The entry point binds **127.0.0.1:8001**. `GET /api/health` returns
`{"status":"ok","service":"videoforge-http"}`. It does not start or modify
the existing STDIO MCP service. The configured `VIDEOFORGE_OUTPUT_DIR` remains
the storage root. HTTP data is confined to its `http/projects/{project}`
namespace. Project identifiers are 1–64 ASCII letters, digits, `_` or `-`,
beginning with a letter or digit. Identifiers are namespacing keys, not user
authentication; the service must remain local.

| Method | Path | Request | Success |
| --- | --- | --- | --- |
| POST | `/api/projects/{project}/uploads` | multipart `file` MP4 | 201 upload receipt |
| POST | `/api/projects/{project}/uploads/references` | JSON `{"job_id":"<32 lowercase hex>"}` | 201 upload receipt copied from a completed local VideoForge job |
| POST | `/api/projects/{project}/extractions` | JSON `{"upload_id":"<32 lowercase hex>","target_n":30}` | 201 public manifest |
| GET | `/api/projects/{project}/extractions/{extraction_id}` | — | 200 public manifest |
| GET | `.../candidates/{candidate_id}/preview` | — | 200 `image/png`, inline |
| GET | `.../candidates/{candidate_id}/download` | — | 200 `image/png`, attachment |
| POST | `.../selection` | JSON `{"selected_candidate_ids":["frame-000000"]}` | 201 selection receipt |
| GET | `.../selection` | — | 200 selection receipt |
| GET | `.../download.zip` | optional `?selected=true` | 200 ZIP containing public `candidates.json` and PNG files |

The upload receipt is `{upload_id,sha256,size_bytes,extract_url}`. The public
manifest is `{schema_version,extraction_id,source,target_n,candidate_limit,
candidates,selection_url,zip_url,diagnostics}`. `source` contains SHA-256,
fps, frame count, duration and dimensions. Each candidate contains
`candidate_id`, `frame_index`, `timestamp_seconds`, `sha256`, `preview_url`,
and `download_url`. No absolute source or frame filesystem paths are returned.
Selection receipt contains `selected_candidate_ids`,
`automatic_final_selection:false`, and `candidate_manifest_sha256`.

The existing `extract_candidates` function chooses at most `2 × target_n`
frames, including first and last, and writes SHA-256 hashes for the source and
every PNG. It publishes a complete extraction by atomic directory rename; a
failed extraction leaves no visible extraction and a retry receives a new ID.
The original uploaded or job MP4 is never deleted. `target_n` is 1–60, uploads
are at most 512 MiB (536,870,912 file bytes, inclusive), and decoded videos are
limited to 12,000 frames. Multipart request bodies also have a 1 MiB allowance
for headers and boundaries; both declared and streamed body sizes are checked.
The upload parser spools file data to disk and the application copies it in
1 MiB chunks, so it does not hold the entire MP4 in memory. A file one byte
over the file limit returns `413 upload_too_large`, and an incomplete upload
is removed. A job
reference is accepted only for a completed job whose canonical MP4 path equals
`VIDEOFORGE_OUTPUT_DIR/{job_id}.mp4`; arbitrary filesystem paths are never API
inputs. Browser clients should store IDs and URLs, not host paths.

Errors use `{"error":{"code":"..."}}` with stable codes:

| HTTP | Codes |
| --- | --- |
| 400 | `invalid_identifier`, `invalid_request`, `invalid_target_n`, `invalid_selection`, `file_required` |
| 404 | `upload_not_found`, `job_not_found`, `extraction_not_found`, `candidate_not_found`, `selection_not_found` |
| 409 | `job_not_complete`, `job_output_invalid`, `selection_exists`, `candidate_integrity_failed`, `path_conflict` |
| 413 | `upload_too_large`, `frame_limit_exceeded` |
| 415 | `mp4_required`, `invalid_mp4_content` |
| 422 | `invalid_video`, `extraction_failed` |

## ImageForge frontend connection

The existing ImageForge HTTP app is documented at `http://127.0.0.1:8000`.
Its health endpoint is `GET /api/health`, its API remains under `/api/*`, its
static browser UI is mounted at `/web/*`, and `/` serves its entry page. The
frontend proxy may route its own `/imageforge/api/*` calls to the same
`/api/*` paths on port 8000 and `/videoforge/api/*` calls to port 8001.
The existing ImageForge UI calls root-absolute `/api/*` URLs, so embedding
that UI requires `/web/*` and `/api/*` on the same browser origin, both
proxied to port 8000, or an explicit URL rewrite. Forward methods, multipart
bodies, query strings, content types, and download response headers unchanged.
Do not rewrite ImageForge session or asset URLs without preserving their
`/api/*` destination. The ImageForge routes
remain implemented by its existing `server.py`; this HTTP addition edits none
of those files. At implementation time, neither port 8000 nor 8001 was
listening, so live health was unavailable. No service was started or restarted.

## Rollback

Stop `videoforge-http` if it was started, remove this new HTTP module, its
entry point and optional dependencies from `pyproject.toml`, and remove the
frontend proxy route for port 8001. Existing MCP extraction outputs, jobs,
and ImageForge files do not need migration or rollback.
