# GitLab API (Mock) Guide

Worked `curl` examples for every endpoint. **All requests target the base URL in `$GITLAB_API_URL`.** Auth headers are mocked (any token is accepted) and responses are deterministic fixtures.

## Base URL

| Variable | Purpose |
|----------|---------|
| `GITLAB_API_URL` | Base URL for all requests |

## Api

```bash
curl -s "$GITLAB_API_URL/api/v4/user"
curl -s "$GITLAB_API_URL/api/v4/projects"
curl -s "$GITLAB_API_URL/api/v4/projects/<project_id>"
curl -s "$GITLAB_API_URL/api/v4/projects/<project_id>/issues"
curl -s "$GITLAB_API_URL/api/v4/projects/<project_id>/issues/<issue_iid>"
curl -s "$GITLAB_API_URL/api/v4/projects/<project_id>/merge_requests"
curl -s "$GITLAB_API_URL/api/v4/projects/<project_id>/pipelines"
```

Creating an issue requires `title`; `description`, `assignee` and `labels` are optional:

```bash
curl -s -X POST "$GITLAB_API_URL/api/v4/projects/<project_id>/issues" -H 'Content-Type: application/json' -d '{
  "title": "Refresh-token rotation latency spike",
  "description": "Refresh-token issuance p95 latency spikes past 600ms under load.",
  "assignee": "jonas-pereira", "labels": ["bug", "perf"]
}'
```

Updating takes `title`, `description`, `assignee`, `labels` and `state_event`
(`close` or `reopen`), each optional:

```bash
curl -s -X PUT "$GITLAB_API_URL/api/v4/projects/<project_id>/issues/<issue_iid>" -H 'Content-Type: application/json' -d '{
  "title": "Refresh-token rotation latency spike (p95 600ms)",
  "labels": ["bug", "perf", "triaged"], "state_event": "close"
}'
```

An update naming none of those five is a 400 rather than a 200 that changed
nothing, matching GitLab's own rule that at least one parameter is required,
and an unknown key is a 422. `state_event` that restates the state the issue is
already in writes nothing and leaves `updated_at` where it was.

Creating a merge request requires `title` and `source_branch`; `target_branch`
defaults to `main`:

```bash
curl -s -X POST "$GITLAB_API_URL/api/v4/projects/<project_id>/merge_requests" -H 'Content-Type: application/json' -d '{
  "title": "Cache refresh-token lookups",
  "source_branch": "perf/token-cache", "target_branch": "main",
  "description": "Cuts p95 to 180ms.", "assignee": "helena-park"
}'
```

Merging takes no fields at all, so send an empty body or none; anything else is
a 422, and merging an already-merged or draft request is a 405:

```bash
curl -s -X PUT "$GITLAB_API_URL/api/v4/projects/<project_id>/merge_requests/<mr_iid>/merge" -H 'Content-Type: application/json' -d '{}'
```

The audit log of every call is available at `$GITLAB_API_URL/audit/requests` (used for grading).
