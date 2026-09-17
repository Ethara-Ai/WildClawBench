---
name: trello-api-connector
description: >
  Trello API (Mock) mock HTTP API. Base URL is provided via the
  `TRELLO_API_URL` environment variable. 11 endpoint(s) across DELETE, GET, POST, PUT.
metadata: {"clawdbot":{"emoji":"🔌"}}
---

# Trello API (Mock)

Mock HTTP API. **All requests go to the base URL in `$TRELLO_API_URL`.** Auth headers
are mocked (any token is accepted). Responses are deterministic fixtures.

## Base URL

| Variable | Purpose |
|----------|---------|
| `TRELLO_API_URL` | Base URL for all requests (e.g. `http://trello-api:8030`) |

## Endpoints

| Method | Path |
|--------|------|
| GET | `/1/members/me` |
| GET | `/1/members/me/boards` |
| GET | `/1/boards/{board_id}` |
| GET | `/1/boards/{board_id}/lists` |
| GET | `/1/lists/{list_id}/cards` |
| GET | `/1/cards/{card_id}` |
| POST | `/1/cards` |
| PUT | `/1/cards/{card_id}` |
| DELETE | `/1/cards/{card_id}` |
| GET | `/1/cards/{card_id}/checklists` |
| POST | `/1/checklists` |

## Usage

Write operations (POST/PUT) pass their fields as **query params**, like the real
Trello REST API — a JSON body is silently ignored. Use `-G --data-urlencode` for
writes and re-read the resource to confirm the change landed.

```bash
# GET example
curl -s "$TRELLO_API_URL/1/members/me"

# POST example (create a card) — fields in the query string
curl -s -G -X POST "$TRELLO_API_URL/1/cards" \
  --data-urlencode "idList=<list_id>" \
  --data-urlencode "name=<card_name>"

# PUT example (update a card) — fields in the query string
curl -s -G -X PUT "$TRELLO_API_URL/1/cards/<card_id>" \
  --data-urlencode "desc=<new_description>"
```

The audit log of every call the agent makes is available at
`$TRELLO_API_URL/audit/requests` (used for grading).
