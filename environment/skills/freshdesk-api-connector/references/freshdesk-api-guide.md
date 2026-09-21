# Freshdesk API (Mock) Guide

Worked `curl` examples for every endpoint. **All requests target the base URL in `$FRESHDESK_API_URL`.** Auth headers are mocked (any token is accepted) and responses are deterministic fixtures.

## Base URL

| Variable | Purpose |
|----------|---------|
| `FRESHDESK_API_URL` | Base URL for all requests |

## Api

```bash
curl -s "$FRESHDESK_API_URL/api/v2/tickets"
curl -s "$FRESHDESK_API_URL/api/v2/tickets/<ticket_id>"
curl -s "$FRESHDESK_API_URL/api/v2/contacts"
curl -s "$FRESHDESK_API_URL/api/v2/agents"
```

Create requires `subject`; `description`, `status`, `priority`, `requester_id`, `responder_id`, `type` and `tags` are optional. `status` and `priority` are integers (status 2 open, 3 pending, 4 resolved, 5 closed; priority 1 low to 4 urgent):

```bash
curl -s -X POST "$FRESHDESK_API_URL/api/v2/tickets" -H 'Content-Type: application/json' -d '{
  "subject": "Cannot log in to dashboard",
  "description": "User reports a 403 error after password reset.",
  "status": 2, "priority": 2, "type": "Incident",
  "requester_id": 90001, "tags": ["login", "auth"]
}'
```

Update takes the same fields, each optional, and applies only the ones present:

```bash
curl -s -X PUT "$FRESHDESK_API_URL/api/v2/tickets/<ticket_id>" -H 'Content-Type: application/json' -d '{
  "status": 4, "priority": 3, "responder_id": 80001
}'
```

Both routes reject an unknown key with 422, and an update naming none of the
fields above is a 400 rather than a 200 that changed nothing — a ticket whose
`updated_at` moved is a ticket that was actually written to.

The audit log of every call is available at `$FRESHDESK_API_URL/audit/requests` (used for grading).
