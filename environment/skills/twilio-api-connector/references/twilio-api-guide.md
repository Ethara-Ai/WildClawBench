# Twilio API (Mock) Guide

Worked `curl` examples for every endpoint. **All requests target the base URL in `$TWILIO_API_URL`.** Auth headers are mocked (any token is accepted) and responses are deterministic fixtures.

## Base URL

| Variable | Purpose |
|----------|---------|
| `TWILIO_API_URL` | Base URL for all requests |

## 2010 04 01

Write operations (POST) pass their fields as **query params**, not a JSON body —
a JSON body is silently ignored and required fields (`To`/`From`) will 422. Use
`-G --data-urlencode` for writes.

```bash
curl -s "$TWILIO_API_URL/2010-04-01/Accounts/<account_sid>/Messages.json"
curl -s "$TWILIO_API_URL/2010-04-01/Accounts/<account_sid>/Messages/<sid>.json"
# send a message: fields go in the query string
curl -s -G -X POST "$TWILIO_API_URL/2010-04-01/Accounts/<account_sid>/Messages.json" \
  --data-urlencode "To=<to_number>" \
  --data-urlencode "From=<from_number>" \
  --data-urlencode "Body=<message_text>"
curl -s "$TWILIO_API_URL/2010-04-01/Accounts/<account_sid>/Calls.json"
# create a call: fields go in the query string
curl -s -G -X POST "$TWILIO_API_URL/2010-04-01/Accounts/<account_sid>/Calls.json" \
  --data-urlencode "To=<to_number>" \
  --data-urlencode "From=<from_number>"
curl -s "$TWILIO_API_URL/2010-04-01/Accounts/<account_sid>/IncomingPhoneNumbers.json"
```

## Phonenumbers

```bash
curl -s "$TWILIO_API_URL/v1/PhoneNumbers/<phone_number>"
```

The audit log of every call is available at `$TWILIO_API_URL/audit/requests` (used for grading).
