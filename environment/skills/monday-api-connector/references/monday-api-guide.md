# monday.com API (Mock) Guide

Worked `curl` examples for every endpoint. **All requests target the base URL in `$MONDAY_API_URL`.** Auth headers are mocked (any token is accepted) and responses are deterministic fixtures.

## Base URL

| Variable | Purpose |
|----------|---------|
| `MONDAY_API_URL` | Base URL for all requests |

## Boards

```bash
curl -s "$MONDAY_API_URL/v2/boards"
curl -s "$MONDAY_API_URL/v2/boards/<board_id>"
curl -s "$MONDAY_API_URL/v2/boards/<board_id>/items"
```

## Items

```bash
curl -s "$MONDAY_API_URL/v2/items"
curl -s "$MONDAY_API_URL/v2/items/<item_id>"
curl -s -X DELETE "$MONDAY_API_URL/v2/items/<item_id>"
```

Create takes `board_id` and `item_name`; `group_id` defaults to the board's first group. Cell contents go in `column_values`, keyed by column id — either a bare string or a `{"text": ..., "value": ...}` object:

```bash
curl -s -X POST "$MONDAY_API_URL/v2/items" -H 'Content-Type: application/json' -d '{
  "board_id": "board-101",
  "item_name": "Session 3 - Tanneries of the French Broad",
  "group_id": "grp-todo",
  "column_values": {"status": {"text": "Hold for correction"}, "owner": "Priya Nair"}
}'
```

Update takes the same `column_values` dict, and `item_name` to rename the row:

```bash
curl -s -X PUT "$MONDAY_API_URL/v2/items/<item_id>" -H 'Content-Type: application/json' -d '{
  "item_name": "Session 3 - Tanneries of the French Broad (reader p. 51)",
  "column_values": {"status": {"text": "Clear to print"}}
}'
```

One cell at a time is also accepted, naming the column with `column_id` and its
contents with `text` and/or `value`:

```bash
curl -s -X PUT "$MONDAY_API_URL/v2/items/<item_id>" -H 'Content-Type: application/json' -d '{
  "column_id": "status", "text": "Clear to print"
}'
```

`group_id` moves the row between groups on its own board. Both routes reject an
unknown key with 422, and an update naming none of `column_values`, `column_id`,
`item_name` or `group_id` is a 400 rather than a 200 that changed nothing —
`text` without `column_id` has no cell to write to and counts as naming none of
them.

## Users

```bash
curl -s "$MONDAY_API_URL/v2/users"
```

## Workspaces

```bash
curl -s "$MONDAY_API_URL/v2/workspaces"
```

The audit log of every call is available at `$MONDAY_API_URL/audit/requests` (used for grading).
