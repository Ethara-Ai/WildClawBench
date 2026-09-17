# Trello API (Mock) Guide

Worked `curl` examples for every endpoint. **All requests target the base URL in `$TRELLO_API_URL`.** Auth headers are mocked (any token is accepted) and responses are deterministic fixtures.

## Base URL

| Variable | Purpose |
|----------|---------|
| `TRELLO_API_URL` | Base URL for all requests |

## 1

Like the real Trello REST API, **write operations (POST/PUT) pass their fields as
query params, not a JSON body.** A JSON body is silently ignored, so use `-G
--data-urlencode` (or a literal query string) for every write. Always re-read the
resource afterwards to confirm the write landed.

```bash
curl -s "$TRELLO_API_URL/1/members/me"
curl -s "$TRELLO_API_URL/1/members/me/boards"
curl -s "$TRELLO_API_URL/1/boards/<board_id>"
curl -s "$TRELLO_API_URL/1/boards/<board_id>/lists"
curl -s "$TRELLO_API_URL/1/lists/<list_id>/cards"
curl -s "$TRELLO_API_URL/1/cards/<card_id>"
# create a card: fields go in the query string
curl -s -G -X POST "$TRELLO_API_URL/1/cards" \
  --data-urlencode "idList=<list_id>" \
  --data-urlencode "name=<card_name>" \
  --data-urlencode "desc=<description>"
# update a card: fields go in the query string (JSON body is ignored)
curl -s -G -X PUT "$TRELLO_API_URL/1/cards/<card_id>" \
  --data-urlencode "desc=<new_description>"
curl -s -X DELETE "$TRELLO_API_URL/1/cards/<card_id>"
curl -s "$TRELLO_API_URL/1/cards/<card_id>/checklists"
curl -s -G -X POST "$TRELLO_API_URL/1/checklists" \
  --data-urlencode "idCard=<card_id>" \
  --data-urlencode "name=<checklist_name>"
```

The audit log of every call is available at `$TRELLO_API_URL/audit/requests` (used for grading).
