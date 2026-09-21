# Cloudflare API (Mock) Guide

Worked `curl` examples for every endpoint. **All requests target the base URL in `$CLOUDFLARE_API_URL`.** Auth headers are mocked (any token is accepted) and responses are deterministic fixtures.

## Base URL

| Variable | Purpose |
|----------|---------|
| `CLOUDFLARE_API_URL` | Base URL for all requests |

## Client

```bash
curl -s "$CLOUDFLARE_API_URL/client/v4/zones"
curl -s "$CLOUDFLARE_API_URL/client/v4/zones/<zone_id>"
curl -s "$CLOUDFLARE_API_URL/client/v4/zones/<zone_id>/dns_records"
curl -s "$CLOUDFLARE_API_URL/client/v4/zones/<zone_id>/dns_records/<record_id>"
curl -s -X DELETE "$CLOUDFLARE_API_URL/client/v4/zones/<zone_id>/dns_records/<record_id>"
curl -s "$CLOUDFLARE_API_URL/client/v4/zones/<zone_id>/firewall/rules"
```

Create requires `type`, `name` and `content`; `ttl` (1 means automatic), `proxied` and `priority` default if omitted:

```bash
curl -s -X POST "$CLOUDFLARE_API_URL/client/v4/zones/<zone_id>/dns_records" -H 'Content-Type: application/json' -d '{
  "type": "A", "name": "orbit-labs.com", "content": "203.0.113.10",
  "ttl": 1, "proxied": true
}'
```

Update takes the same six fields, each optional, and applies only the ones present:

```bash
curl -s -X PUT "$CLOUDFLARE_API_URL/client/v4/zones/<zone_id>/dns_records/<record_id>" -H 'Content-Type: application/json' -d '{
  "content": "203.0.113.88", "ttl": 300
}'
```

Both routes reject an unknown key with 422, and an update naming none of the
fields above is a 400 in the usual envelope rather than a 200 that changed
nothing — a record whose `modified_on` moved is a record that was actually
written to.

The audit log of every call is available at `$CLOUDFLARE_API_URL/audit/requests` (used for grading).
