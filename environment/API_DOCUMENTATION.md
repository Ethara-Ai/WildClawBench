# Mock API Services Documentation

## Table of Contents

16. [Whatsapp API](#16-whatsapp-api)
17. [Google Calendar API](#17-google-calendar-api)
18. [Gmail API](#18-gmail-api)
20. [Github API](#20-github-api)
21. [Eventbrite API](#21-eventbrite-api)
23. [Plaid API](#23-plaid-api)
24. [Coinbase API](#24-coinbase-api)
25. [Hubspot API](#25-hubspot-api)
26. [Zendesk API](#26-zendesk-api)
27. [Twilio API](#27-twilio-api)
29. [Zoom API](#29-zoom-api)
30. [Jira API](#30-jira-api)
31. [Trello API](#31-trello-api)
41. [Pagerduty API](#41-pagerduty-api)
42. [Square API](#42-square-api)
43. [Paypal API](#43-paypal-api)
44. [Alpaca API](#44-alpaca-api)
45. [Salesforce API](#45-salesforce-api)
46. [Confluence API](#46-confluence-api)
47. [Gitlab API](#47-gitlab-api)
48. [Sentry API](#48-sentry-api)
49. [Datadog API](#49-datadog-api)
51. [Cloudflare API](#51-cloudflare-api)
52. [Kubernetes API](#52-kubernetes-api)
54. [Docusign API](#54-docusign-api)
57. [Mixpanel API](#57-mixpanel-api)
59. [Reddit API](#59-reddit-api)
63. [Linkedin API](#63-linkedin-api)
65. [Twitch API](#65-twitch-api)
67. [Contentful API](#67-contentful-api)
70. [Intercom API](#70-intercom-api)
71. [Servicenow API](#71-servicenow-api)
72. [Bamboohr API](#72-bamboohr-api)
73. [Greenhouse API](#73-greenhouse-api)
74. [Gusto API](#74-gusto-api)
75. [Ticketmaster API](#75-ticketmaster-api)
77. [Nasa API](#77-nasa-api)
78. [Openlibrary API](#78-openlibrary-api)
80. [Monday API](#80-monday-api)
84. [Bigcommerce API](#84-bigcommerce-api)
85. [Woocommerce API](#85-woocommerce-api)
86. [Microsoft Teams API](#86-microsoft-teams-api)
87. [Outlook API](#87-outlook-api)
89. [Klaviyo API](#89-klaviyo-api)
90. [Segment API](#90-segment-api)
92. [Posthog API](#92-posthog-api)
93. [Freshdesk API](#93-freshdesk-api)
98. [Kraken API](#98-kraken-api)
100. [Webflow API](#100-webflow-api)
101. [Activecampaign API](#101-activecampaign-api)

---

## Service Overview

| Service | Port | Env Var | App Title | Version |
|---------|------|---------|-----------|---------|
| whatsapp-api | 8015 | `WHATSAPP_API_URL` | Whatsapp API | v1.0.0 |
| google-calendar-api | 8016 | `GOOGLE_CALENDAR_API_URL` | Google Calendar API | v1.0.0 |
| gmail-api | 8017 | `GMAIL_API_URL` | Gmail API | v1.0.0 |
| github-api | 8019 | `GITHUB_API_URL` | Github API | v1.0.0 |
| eventbrite-api | 8020 | `EVENTBRITE_API_URL` | Eventbrite API | v1.0.0 |
| plaid-api | 8022 | `PLAID_API_URL` | Plaid API | v1.0.0 |
| coinbase-api | 8023 | `COINBASE_API_URL` | Coinbase API | v1.0.0 |
| hubspot-api | 8024 | `HUBSPOT_API_URL` | Hubspot API | v1.0.0 |
| zendesk-api | 8025 | `ZENDESK_API_URL` | Zendesk API | v1.0.0 |
| twilio-api | 8026 | `TWILIO_API_URL` | Twilio API | v1.0.0 |
| zoom-api | 8028 | `ZOOM_API_URL` | Zoom API | v1.0.0 |
| jira-api | 8029 | `JIRA_API_URL` | Jira API | v1.0.0 |
| trello-api | 8030 | `TRELLO_API_URL` | Trello API | v1.0.0 |
| pagerduty-api | 8040 | `PAGERDUTY_API_URL` | Pagerduty API | v1.0.0 |
| square-api | 8041 | `SQUARE_API_URL` | Square API | v1.0.0 |
| paypal-api | 8042 | `PAYPAL_API_URL` | Paypal API | v1.0.0 |
| alpaca-api | 8043 | `ALPACA_API_URL` | Alpaca API | v1.0.0 |
| salesforce-api | 8044 | `SALESFORCE_API_URL` | Salesforce API | v1.0.0 |
| confluence-api | 8045 | `CONFLUENCE_API_URL` | Confluence API | v1.0.0 |
| gitlab-api | 8046 | `GITLAB_API_URL` | Gitlab API | v1.0.0 |
| sentry-api | 8047 | `SENTRY_API_URL` | Sentry API | v1.0.0 |
| datadog-api | 8048 | `DATADOG_API_URL` | Datadog API | v1.0.0 |
| cloudflare-api | 8050 | `CLOUDFLARE_API_URL` | Cloudflare API | v1.0.0 |
| kubernetes-api | 8051 | `KUBERNETES_API_URL` | Kubernetes API | v1.0.0 |
| docusign-api | 8053 | `DOCUSIGN_API_URL` | Docusign API | v1.0.0 |
| mixpanel-api | 8056 | `MIXPANEL_API_URL` | Mixpanel API | v1.0.0 |
| reddit-api | 8058 | `REDDIT_API_URL` | Reddit API | v1.0.0 |
| linkedin-api | 8062 | `LINKEDIN_API_URL` | Linkedin API | v1.0.0 |
| twitch-api | 8064 | `TWITCH_API_URL` | Twitch API | v1.0.0 |
| contentful-api | 8066 | `CONTENTFUL_API_URL` | Contentful API | v1.0.0 |
| intercom-api | 8070 | `INTERCOM_API_URL` | Intercom API | v1.0.0 |
| servicenow-api | 8071 | `SERVICENOW_API_URL` | Servicenow API | v1.0.0 |
| bamboohr-api | 8072 | `BAMBOOHR_API_URL` | Bamboohr API | v1.0.0 |
| greenhouse-api | 8073 | `GREENHOUSE_API_URL` | Greenhouse API | v1.0.0 |
| gusto-api | 8074 | `GUSTO_API_URL` | Gusto API | v1.0.0 |
| ticketmaster-api | 8075 | `TICKETMASTER_API_URL` | Ticketmaster API | v1.0.0 |
| nasa-api | 8077 | `NASA_API_URL` | Nasa API | v1.0.0 |
| openlibrary-api | 8078 | `OPENLIBRARY_API_URL` | Openlibrary API | v1.0.0 |
| monday-api | 8080 | `MONDAY_API_URL` | Monday API | v1.0.0 |
| bigcommerce-api | 8084 | `BIGCOMMERCE_API_URL` | Bigcommerce API | v1.0.0 |
| woocommerce-api | 8085 | `WOOCOMMERCE_API_URL` | Woocommerce API | v1.0.0 |
| microsoft-teams-api | 8086 | `MICROSOFT_TEAMS_API_URL` | Microsoft Teams API | v1.0.0 |
| outlook-api | 8087 | `OUTLOOK_API_URL` | Outlook API | v1.0.0 |
| klaviyo-api | 8089 | `KLAVIYO_API_URL` | Klaviyo API | v1.0.0 |
| segment-api | 8090 | `SEGMENT_API_URL` | Segment API | v1.0.0 |
| posthog-api | 8092 | `POSTHOG_API_URL` | Posthog API | v1.0.0 |
| freshdesk-api | 8093 | `FRESHDESK_API_URL` | Freshdesk API | v1.0.0 |
| kraken-api | 8098 | `KRAKEN_API_URL` | Kraken API | v1.0.0 |
| webflow-api | 8100 | `WEBFLOW_API_URL` | Webflow API | v1.0.0 |
| activecampaign-api | 8101 | `ACTIVECAMPAIGN_API_URL` | Activecampaign API | v1.0.0 |
---

## Shared Tracking/Audit Endpoints

All 10 services include tracking middleware that exposes these endpoints:

#### `GET /health`
Health check.

**Response:** `200`
```json
{"status": "ok"}
```

#### `GET /audit/requests`
Returns full audit log of all requests.

**Response:** `200`
```json
{"total": 42, "requests": [...]}
```

#### `GET /audit/requests/clear`
Clears the audit log.

**Response:** `200`
```json
{"cleared": 42}
```

#### `GET /audit/summary`
Aggregated request summary by endpoint.

**Response:** `200`
```json
{"total_requests": 42, "endpoints": {"GET /some/path": {"count": 10, "statuses": {"200": 8, "404": 2}}}}
```

Each value in `endpoints` is a dict with `count` (integer) and `statuses` (map of status code → count). Use `endpoint_data["count"]` to get the call count.

**Audit Log Entry Format:**
```json
{
  "timestamp": 1234567890.123,
  "timestamp_iso": "2026-05-07T10:30:00",
  "method": "GET",
  "path": "/some/path",
  "query_params": {"key": "value"},
  "request_body": "..." ,
  "status_code": 200,
  "response_body": "...",
  "duration_ms": 12.34
}
```

---

## 16. Whatsapp API

**Service**: `whatsapp-api` · **Port**: 8015 · **Env**: `WHATSAPP_API_URL`

Mock service mirroring Whatsapp API endpoints. See `whatsapp-api/whatsapp-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v17.0/business` — business
- `GET {{baseUrl}}/v17.0/contacts?opted_in_only=true` — list contacts opted in
- `GET {{baseUrl}}/v17.0/contacts/15551550101` — get contact
- `GET {{baseUrl}}/v17.0/message_templates?status=APPROVED` — list approved templates
- `GET {{baseUrl}}/v17.0/message_templates/order_shipped` — get template
- `GET {{baseUrl}}/v17.0/conversations` — list conversations
- `GET {{baseUrl}}/v17.0/messages?conversation_id=conv-001` — list messages
- `POST {{baseUrl}}/v17.0/messages` — send text
- `POST {{baseUrl}}/v17.0/messages` — send template
- `POST {{baseUrl}}/v17.0/messages/status` — mark read

---

## 17. Google Calendar API

**Service**: `google-calendar-api` · **Port**: 8016 · **Env**: `GOOGLE_CALENDAR_API_URL`

Mock service mirroring Google Calendar API endpoints. See `google-calendar-api/google-calendar-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/calendar/v3/users/me/calendarList` — list calendars
- `GET {{baseUrl}}/calendar/v3/calendars/primary` — get primary calendar
- `GET {{baseUrl}}/calendar/v3/calendars/primary/events?timeMin=2026-05-26T00:00:00Z&timeMax=2026-05-31T23:59:59Z&orderBy=startTime` — list events this week
- `GET {{baseUrl}}/calendar/v3/calendars/primary/events?q=auth` — search events
- `GET {{baseUrl}}/calendar/v3/calendars/primary/events/evt-003` — get event
- `POST {{baseUrl}}/calendar/v3/calendars/primary/events` — create event
- `PATCH {{baseUrl}}/calendar/v3/calendars/primary/events/evt-003` — patch event
- `DELETE {{baseUrl}}/calendar/v3/calendars/amelia.personal@gmail.com/events/evt-006` — delete event
- `POST {{baseUrl}}/calendar/v3/freeBusy` — freeBusy

---

## 18. Gmail API

**Service**: `gmail-api` · **Port**: 8017 · **Env**: `GMAIL_API_URL`

Mock service mirroring Gmail API endpoints. See `gmail-api/gmail-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/gmail/v1/users/me/profile` — profile
- `GET {{baseUrl}}/gmail/v1/users/me/labels` — labels
- `POST {{baseUrl}}/gmail/v1/users/me/labels` — create label
- `GET {{baseUrl}}/gmail/v1/users/me/messages?labelIds=INBOX` — list inbox
- `GET {{baseUrl}}/gmail/v1/users/me/messages?q=is:unread%20from:jonas` — search unread from jonas
- `GET {{baseUrl}}/gmail/v1/users/me/messages/msg-100` — get message
- `POST {{baseUrl}}/gmail/v1/users/me/messages/send` — send message
- `POST {{baseUrl}}/gmail/v1/users/me/messages/msg-101/modify` — mark message read
- `POST {{baseUrl}}/gmail/v1/users/me/messages/msg-105/modify` — star message
- `POST {{baseUrl}}/gmail/v1/users/me/messages/msg-104/trash` — trash spam
- `GET {{baseUrl}}/gmail/v1/users/me/threads?q=label:Orbit%20Labs` — list threads
- `GET {{baseUrl}}/gmail/v1/users/me/threads/thr-100` — get thread
- `POST {{baseUrl}}/gmail/v1/users/me/drafts` — create draft
- `POST {{baseUrl}}/gmail/v1/users/me/drafts/draft-001/send` — send draft

---

## 20. Github API

**Service**: `github-api` · **Port**: 8019 · **Env**: `GITHUB_API_URL`

Mock service mirroring Github API endpoints. See `github-api/github-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/user` — authenticated user
- `GET {{baseUrl}}/orgs/orbit-labs/repos` — org repos
- `GET {{baseUrl}}/repos/orbit-labs/auth-api` — repo
- `GET {{baseUrl}}/repos/orbit-labs/auth-api/issues?state=open&labels=bug` — open issues with bug label
- `GET {{baseUrl}}/repos/orbit-labs/auth-api/issues/142` — get issue
- `POST {{baseUrl}}/repos/orbit-labs/auth-api/issues` — create issue
- `PATCH {{baseUrl}}/repos/orbit-labs/docs/issues/7` — close issue
- `GET {{baseUrl}}/repos/orbit-labs/auth-api/pulls?state=open` — list open pulls
- `GET {{baseUrl}}/repos/orbit-labs/auth-api/pulls/144` — get pull
- `PUT {{baseUrl}}/repos/orbit-labs/auth-api/pulls/144/merge` — merge pull
- `GET {{baseUrl}}/repos/orbit-labs/auth-api/issues/142/comments` — list issue comments
- `POST {{baseUrl}}/repos/orbit-labs/auth-api/issues/142/comments` — post issue comment

---

## 21. Eventbrite API

**Service**: `eventbrite-api` · **Port**: 8020 · **Env**: `EVENTBRITE_API_URL`

Mock service mirroring Eventbrite API endpoints. See `eventbrite-api/eventbrite-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v3/users/me/organizations` — my organizations
- `GET {{baseUrl}}/v3/organizations/org-cascade/events?status=live` — org events
- `GET {{baseUrl}}/v3/events/search?q=postgres` — search events
- `GET {{baseUrl}}/v3/events/evt-7000001` — get event
- `POST {{baseUrl}}/v3/events` — create draft event
- `POST {{baseUrl}}/v3/events/evt-7000003/publish` — publish event
- `POST {{baseUrl}}/v3/events/evt-7000004/cancel` — cancel event
- `GET {{baseUrl}}/v3/venues` — list venues
- `GET {{baseUrl}}/v3/events/evt-7000001/ticket_classes` — ticket classes
- `POST {{baseUrl}}/v3/events/evt-7000003/ticket_classes` — create ticket class
- `GET {{baseUrl}}/v3/events/evt-7000001/attendees` — list attendees
- `POST {{baseUrl}}/v3/events/evt-7000004/attendees` — register attendee
- `POST {{baseUrl}}/v3/attendees/att-001/check_in` — check in

---

## 23. Plaid API

**Service**: `plaid-api` · **Port**: 8022 · **Env**: `PLAID_API_URL`

Mock service mirroring Plaid API endpoints. See `plaid-api/plaid-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `POST {{baseUrl}}/accounts/get` — accounts get
- `POST {{baseUrl}}/accounts/balance/get` — accounts balance get
- `POST {{baseUrl}}/transactions/get` — transactions get
- `POST {{baseUrl}}/institutions/get_by_id` — institutions get by id
- `POST {{baseUrl}}/identity/get` — identity get

---

## 24. Coinbase API

**Service**: `coinbase-api` · **Port**: 8023 · **Env**: `COINBASE_API_URL`

Mock service mirroring Coinbase API endpoints. See `coinbase-api/coinbase-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v2/user` — get user
- `GET {{baseUrl}}/v2/accounts` — list accounts
- `GET {{baseUrl}}/v2/accounts/acct-btc-001` — get account
- `GET {{baseUrl}}/v2/prices/BTC-USD/spot` — get spot price BTC-USD
- `GET {{baseUrl}}/v2/prices/ETH-USD/spot` — get spot price ETH-USD
- `POST {{baseUrl}}/v2/accounts/acct-btc-001/buys` — create buy
- `POST {{baseUrl}}/v2/accounts/acct-eth-002/sells` — create sell
- `GET {{baseUrl}}/v2/accounts/acct-btc-001/transactions` — list transactions

---

## 25. Hubspot API

**Service**: `hubspot-api` · **Port**: 8024 · **Env**: `HUBSPOT_API_URL`

Mock service mirroring Hubspot API endpoints. See `hubspot-api/hubspot-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/crm/v3/objects/contacts?limit=5` — list contacts
- `GET {{baseUrl}}/crm/v3/objects/contacts/201` — get contact
- `POST {{baseUrl}}/crm/v3/objects/contacts` — create contact
- `PATCH {{baseUrl}}/crm/v3/objects/contacts/204` — update contact
- `GET {{baseUrl}}/crm/v3/objects/companies` — list companies
- `GET {{baseUrl}}/crm/v3/objects/deals?limit=10` — list deals
- `GET {{baseUrl}}/crm/v3/objects/deals/402` — get deal
- `POST {{baseUrl}}/crm/v3/objects/deals` — create deal
- `PATCH {{baseUrl}}/crm/v3/objects/deals/403` — move deal to new stage
- `GET {{baseUrl}}/crm/v3/pipelines/deals` — list deal pipelines

---

## 26. Zendesk API

**Service**: `zendesk-api` · **Port**: 8025 · **Env**: `ZENDESK_API_URL`

Mock service mirroring Zendesk API endpoints. See `zendesk-api/zendesk-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/v2/tickets?status=open` — list tickets
- `GET {{baseUrl}}/api/v2/tickets/701` — get ticket
- `POST {{baseUrl}}/api/v2/tickets` — create ticket
- `PUT {{baseUrl}}/api/v2/tickets/704` — update ticket (status/assignee/priority)
- `GET {{baseUrl}}/api/v2/tickets/701/comments` — list ticket comments
- `POST {{baseUrl}}/api/v2/tickets/701/comments` — create comment
- `GET {{baseUrl}}/api/v2/users?role=agent` — list users
- `GET {{baseUrl}}/api/v2/users/602` — get user
- `GET {{baseUrl}}/api/v2/organizations` — list organizations

---

## 27. Twilio API

**Service**: `twilio-api` · **Port**: 8026 · **Env**: `TWILIO_API_URL`

Mock service mirroring Twilio API endpoints. See `twilio-api/twilio-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/2010-04-01/Accounts/{{accountSid}}/Messages.json?PageSize=10` — list messages
- `GET {{baseUrl}}/2010-04-01/Accounts/{{accountSid}}/Messages.json?Status=received` — list inbound messages
- `GET {{baseUrl}}/2010-04-01/Accounts/{{accountSid}}/Messages/SM0a1b2c3d4e5f60718293a4b5c6d7e801.json` — get message
- `POST {{baseUrl}}/2010-04-01/Accounts/{{accountSid}}/Messages.json?To=%2B14155557777&From=%2B14155550123&Body=Hello%20from%20the%20mock` — create message
- `GET {{baseUrl}}/2010-04-01/Accounts/{{accountSid}}/Calls.json` — list calls
- `POST {{baseUrl}}/2010-04-01/Accounts/{{accountSid}}/Calls.json?To=%2B14155557777&From=%2B14155550123` — create call
- `GET {{baseUrl}}/2010-04-01/Accounts/{{accountSid}}/IncomingPhoneNumbers.json` — list incoming phone numbers
- `GET {{baseUrl}}/v1/PhoneNumbers/+14155550123` — lookup phone number

---

## 29. Zoom API

**Service**: `zoom-api` · **Port**: 8028 · **Env**: `ZOOM_API_URL`

Mock service mirroring Zoom API endpoints. See `zoom-api/zoom-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v2/users/me` — get me
- `GET {{baseUrl}}/v2/users/me/meetings?type=scheduled` — list scheduled meetings
- `GET {{baseUrl}}/v2/users/me/meetings?type=previous_meetings` — list previous meetings
- `POST {{baseUrl}}/v2/users/me/meetings` — create meeting
- `GET {{baseUrl}}/v2/meetings/85012345678` — get meeting
- `PATCH {{baseUrl}}/v2/meetings/85012345678` — update meeting
- `DELETE {{baseUrl}}/v2/meetings/85012345680` — delete meeting
- `GET {{baseUrl}}/v2/meetings/85012345670/recordings` — get recordings
- `GET {{baseUrl}}/v2/meetings/85012345679/registrants?status=approved` — list registrants

---

## 30. Jira API

**Service**: `jira-api` · **Port**: 8029 · **Env**: `JIRA_API_URL`

Mock service mirroring Jira API endpoints. See `jira-api/jira-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/rest/api/3/project` — list projects
- `POST {{baseUrl}}/rest/api/3/issue` — create issue
- `GET {{baseUrl}}/rest/api/3/issue/ENG-102` — get issue
- `PUT {{baseUrl}}/rest/api/3/issue/ENG-102` — update issue
- `GET {{baseUrl}}/rest/api/3/issue/ENG-104/transitions` — get transitions
- `POST {{baseUrl}}/rest/api/3/issue/ENG-104/transitions` — transition issue
- `GET {{baseUrl}}/rest/api/3/search?jql=project %3D ENG AND status %3D "In Progress"` — search jql
- `GET {{baseUrl}}/rest/agile/1.0/board` — list boards
- `GET {{baseUrl}}/rest/agile/1.0/board/1/sprint?state=active` — list sprints

---

## 31. Trello API

**Service**: `trello-api` · **Port**: 8030 · **Env**: `TRELLO_API_URL`

Mock service mirroring Trello API endpoints. See `trello-api/trello-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/1/members/me/boards` — list my boards
- `GET {{baseUrl}}/1/boards/60b1000000000000000000b1` — get board
- `GET {{baseUrl}}/1/boards/60b1000000000000000000b1/lists` — list board lists
- `GET {{baseUrl}}/1/lists/61c1000000000000000000c1/cards` — list cards in list
- `POST {{baseUrl}}/1/cards?idList=61c1000000000000000000c1&name=Investigate%20webhook%20retries&desc=Add%20exponential%20backoff` — create card
- `PUT {{baseUrl}}/1/cards/62d1000000000000000000d4?idList=61c1000000000000000000c2` — move card to Doing
- `DELETE {{baseUrl}}/1/cards/62d1000000000000000000da` — delete card
- `GET {{baseUrl}}/1/cards/62d1000000000000000000d2/checklists` — list card checklists
- `POST {{baseUrl}}/1/checklists?idCard=62d1000000000000000000d4&name=Spike%20tasks` — create checklist

---

## 41. Pagerduty API

**Service**: `pagerduty-api` · **Port**: 8040 · **Env**: `PAGERDUTY_API_URL`

Mock service mirroring Pagerduty API endpoints. See `pagerduty-api/pagerduty-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/services` — list services
- `GET {{baseUrl}}/services/PS001` — get service
- `GET {{baseUrl}}/incidents?statuses[]=triggered&statuses[]=acknowledged` — list incidents (open)
- `GET {{baseUrl}}/incidents/PI001` — get incident
- `POST {{baseUrl}}/incidents` — trigger incident
- `PUT {{baseUrl}}/incidents/PI001` — acknowledge incident
- `PUT {{baseUrl}}/incidents/PI001` — resolve incident
- `POST {{baseUrl}}/incidents/PI001/notes` — add incident note
- `GET {{baseUrl}}/oncalls` — list oncalls
- `GET {{baseUrl}}/schedules` — list schedules
- `GET {{baseUrl}}/escalation_policies` — list escalation policies
- `GET {{baseUrl}}/users` — list users

---

## 42. Square API

**Service**: `square-api` · **Port**: 8041 · **Env**: `SQUARE_API_URL`

Mock service mirroring Square API endpoints. See `square-api/square-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v2/merchants/me` — get merchant
- `GET {{baseUrl}}/v2/payments?limit=10` — list payments
- `GET {{baseUrl}}/v2/payments/PAY_AURORA01` — get payment
- `POST {{baseUrl}}/v2/payments` — create payment
- `POST {{baseUrl}}/v2/refunds` — create refund
- `GET {{baseUrl}}/v2/customers?limit=10` — list customers
- `GET {{baseUrl}}/v2/customers/CUST_MAYA03` — get customer
- `POST {{baseUrl}}/v2/customers` — create customer
- `GET {{baseUrl}}/v2/catalog/list?types=ITEM` — list catalog
- `POST {{baseUrl}}/v2/orders` — create order
- `GET {{baseUrl}}/v2/orders/ORD_AURORA01` — get order
- `GET {{baseUrl}}/v2/inventory/VAR_BEANS_12` — get inventory

---

## 43. Paypal API

**Service**: `paypal-api` · **Port**: 8042 · **Env**: `PAYPAL_API_URL`

Mock service mirroring Paypal API endpoints. See `paypal-api/paypal-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `POST {{baseUrl}}/v2/checkout/orders` — create checkout order
- `GET {{baseUrl}}/v2/checkout/orders/ORDER-8AB54321CD987654E` — get checkout order
- `POST {{baseUrl}}/v2/checkout/orders/ORDER-8AB54321CD987654E/capture` — capture order
- `POST {{baseUrl}}/v2/payments/refunds` — create refund
- `GET {{baseUrl}}/v2/payments/refunds/REF_1A234567BC890123` — get refund
- `GET {{baseUrl}}/v2/invoicing/invoices?status=PAID` — list invoices
- `POST {{baseUrl}}/v2/invoicing/invoices` — create invoice
- `POST {{baseUrl}}/v1/payments/payouts` — create payout

---

## 44. Alpaca API

**Service**: `alpaca-api` · **Port**: 8043 · **Env**: `ALPACA_API_URL`

Mock service mirroring Alpaca API endpoints. See `alpaca-api/alpaca-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v2/account` — get account
- `GET {{baseUrl}}/v2/positions` — list positions
- `GET {{baseUrl}}/v2/positions/AAPL` — get position
- `GET {{baseUrl}}/v2/orders?status=open` — list orders
- `GET {{baseUrl}}/v2/orders/ORD-aurora-0001` — get order
- `POST {{baseUrl}}/v2/orders` — create buy order
- `POST {{baseUrl}}/v2/orders` — create sell order
- `DELETE {{baseUrl}}/v2/orders/ORD-delta-0004` — cancel order
- `GET {{baseUrl}}/v2/assets?asset_class=us_equity` — list assets
- `GET {{baseUrl}}/v2/stocks/AAPL/quotes/latest` — latest quote

---

## 45. Salesforce API

**Service**: `salesforce-api` · **Port**: 8044 · **Env**: `SALESFORCE_API_URL`

Mock service mirroring Salesforce API endpoints. See `salesforce-api/salesforce-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/services/data/v59.0/sobjects/Account` — list accounts
- `GET {{baseUrl}}/services/data/v59.0/sobjects/Account/001Ax000001AAAAAA1` — get account
- `POST {{baseUrl}}/services/data/v59.0/sobjects/Account` — create account
- `PATCH {{baseUrl}}/services/data/v59.0/sobjects/Account/001Ax000001AAAAAA1` — update account
- `GET {{baseUrl}}/services/data/v59.0/sobjects/Contact` — list contacts
- `GET {{baseUrl}}/services/data/v59.0/sobjects/Contact/003Ax000002BBBBBB2` — get contact
- `POST {{baseUrl}}/services/data/v59.0/sobjects/Contact` — create contact
- `GET {{baseUrl}}/services/data/v59.0/sobjects/Lead` — list leads
- `GET {{baseUrl}}/services/data/v59.0/sobjects/Lead/00QAx000001AAAAAA1` — get lead
- `POST {{baseUrl}}/services/data/v59.0/sobjects/Lead` — create lead
- `PATCH {{baseUrl}}/services/data/v59.0/sobjects/Lead/00QAx000001AAAAAA1` — update lead
- `GET {{baseUrl}}/services/data/v59.0/sobjects/Opportunity` — list opportunities
- `GET {{baseUrl}}/services/data/v59.0/sobjects/Opportunity/006Ax000001AAAAAA1` — get opportunity
- `POST {{baseUrl}}/services/data/v59.0/sobjects/Opportunity` — create opportunity
- `GET {{baseUrl}}/services/data/v59.0/query?q=SELECT Id, Name, Industry FROM Account WHERE Industry = 'Technology'` — soql query accounts
- `GET {{baseUrl}}/services/data/v59.0/query?q=SELECT Id, Name, Amount FROM Opportunity WHERE StageName = 'Closed Won'` — soql query opportunities

---

## 46. Confluence API

**Service**: `confluence-api` · **Port**: 8045 · **Env**: `CONFLUENCE_API_URL`

Mock service mirroring Confluence API endpoints. See `confluence-api/confluence-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/wiki/rest/api/space` — list spaces
- `GET {{baseUrl}}/wiki/rest/api/space/ENG` — get space
- `GET {{baseUrl}}/wiki/rest/api/content?type=page&spaceKey=ENG` — list content
- `POST {{baseUrl}}/wiki/rest/api/content` — create content
- `GET {{baseUrl}}/wiki/rest/api/content/100103` — get content
- `PUT {{baseUrl}}/wiki/rest/api/content/100103` — update content
- `GET {{baseUrl}}/wiki/rest/api/content/100101/child/page` — list child pages
- `GET {{baseUrl}}/wiki/rest/api/content/100103/label` — list labels
- `GET {{baseUrl}}/wiki/rest/api/content/100103/child/comment` — list comments
- `GET {{baseUrl}}/wiki/rest/api/content/search?cql=space=ENG` — search by space
- `GET {{baseUrl}}/wiki/rest/api/content/search?cql=title~"Runbook"` — search by title

---

## 47. Gitlab API

**Service**: `gitlab-api` · **Port**: 8046 · **Env**: `GITLAB_API_URL`

Mock service mirroring Gitlab API endpoints. See `gitlab-api/gitlab-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/v4/user` — get current user
- `GET {{baseUrl}}/api/v4/projects` — list projects
- `GET {{baseUrl}}/api/v4/projects/101` — get project
- `GET {{baseUrl}}/api/v4/projects/101/issues?state=opened` — list issues
- `GET {{baseUrl}}/api/v4/projects/101/issues/1` — get issue
- `POST {{baseUrl}}/api/v4/projects/101/issues` — create issue
- `PUT {{baseUrl}}/api/v4/projects/101/issues/2` — update issue (close)
- `GET {{baseUrl}}/api/v4/projects/101/merge_requests?state=opened` — list merge requests
- `POST {{baseUrl}}/api/v4/projects/101/merge_requests` — create merge request
- `PUT {{baseUrl}}/api/v4/projects/101/merge_requests/1/merge` — merge merge request
- `GET {{baseUrl}}/api/v4/projects/101/pipelines` — list pipelines

---

## 48. Sentry API

**Service**: `sentry-api` · **Port**: 8047 · **Env**: `SENTRY_API_URL`

Mock service mirroring Sentry API endpoints. See `sentry-api/sentry-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/0/organizations/orbit-labs/projects/` — list org projects
- `GET {{baseUrl}}/api/0/projects/orbit-labs/auth-service/issues/?status=unresolved` — list project issues
- `GET {{baseUrl}}/api/0/projects/orbit-labs/web-frontend/issues/?level=error` — list project issues by level
- `GET {{baseUrl}}/api/0/organizations/orbit-labs/issues/40001/` — get issue
- `PUT {{baseUrl}}/api/0/organizations/orbit-labs/issues/40001/` — resolve issue
- `PUT {{baseUrl}}/api/0/organizations/orbit-labs/issues/40002/` — ignore issue
- `GET {{baseUrl}}/api/0/organizations/orbit-labs/issues/40001/events/` — list issue events
- `GET {{baseUrl}}/api/0/organizations/orbit-labs/releases/` — list releases
- `GET {{baseUrl}}/api/0/organizations/orbit-labs/releases/?project=auth-service` — list releases for project

---

## 49. Datadog API

**Service**: `datadog-api` · **Port**: 8048 · **Env**: `DATADOG_API_URL`

Mock service mirroring Datadog API endpoints. See `datadog-api/datadog-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/v1/query?from=1748160000&to=1748250600&query=avg:trace.http.request.duration{service:auth-service}` — query metric series
- `GET {{baseUrl}}/api/v1/monitor` — list monitors
- `GET {{baseUrl}}/api/v1/monitor?overall_state=Alert` — list monitors alerting
- `GET {{baseUrl}}/api/v1/monitor/1001` — get monitor
- `POST {{baseUrl}}/api/v1/monitor` — create monitor
- `PUT {{baseUrl}}/api/v1/monitor/1001` — update monitor (mute via state)
- `GET {{baseUrl}}/api/v1/dashboard` — list dashboards
- `GET {{baseUrl}}/api/v1/dashboard/abc-123-def` — get dashboard
- `GET {{baseUrl}}/api/v1/events` — list events
- `POST {{baseUrl}}/api/v1/events` — create event
- `GET {{baseUrl}}/api/v1/hosts` — list hosts

---

## 51. Cloudflare API

**Service**: `cloudflare-api` · **Port**: 8050 · **Env**: `CLOUDFLARE_API_URL`

Mock service mirroring Cloudflare API endpoints. See `cloudflare-api/cloudflare-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/client/v4/zones` — list zones
- `GET {{baseUrl}}/client/v4/zones/{{zoneId}}` — get zone
- `GET {{baseUrl}}/client/v4/zones/{{zoneId}}/dns_records` — list dns records
- `GET {{baseUrl}}/client/v4/zones/{{zoneId}}/dns_records?type=A` — list dns records by type
- `GET {{baseUrl}}/client/v4/zones/{{zoneId}}/dns_records/rec0001aaaa` — get dns record
- `POST {{baseUrl}}/client/v4/zones/{{zoneId}}/dns_records` — create dns record
- `PUT {{baseUrl}}/client/v4/zones/{{zoneId}}/dns_records/rec0001aaaa` — update dns record
- `DELETE {{baseUrl}}/client/v4/zones/{{zoneId}}/dns_records/rec0005eeee` — delete dns record
- `GET {{baseUrl}}/client/v4/zones/{{zoneId}}/firewall/rules` — list firewall rules

---

## 52. Kubernetes API

**Service**: `kubernetes-api` · **Port**: 8051 · **Env**: `KUBERNETES_API_URL`

Mock service mirroring Kubernetes API endpoints. See `kubernetes-api/kubernetes-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/v1/namespaces` — list namespaces
- `GET {{baseUrl}}/api/v1/namespaces/prod/pods` — list pods
- `GET {{baseUrl}}/api/v1/namespaces/prod/pods/api-gateway-5d8f7c` — get pod
- `DELETE {{baseUrl}}/api/v1/namespaces/prod/pods/billing-worker-9af21` — delete pod
- `GET {{baseUrl}}/apis/apps/v1/namespaces/prod/deployments` — list deployments
- `GET {{baseUrl}}/apis/apps/v1/namespaces/prod/deployments/api-gateway` — get deployment
- `PATCH {{baseUrl}}/apis/apps/v1/namespaces/prod/deployments/api-gateway/scale` — scale deployment
- `GET {{baseUrl}}/api/v1/namespaces/prod/services` — list services
- `GET {{baseUrl}}/api/v1/nodes` — list nodes

---

## 54. Docusign API

**Service**: `docusign-api` · **Port**: 8053 · **Env**: `DOCUSIGN_API_URL`

Mock service mirroring Docusign API endpoints. See `docusign-api/docusign-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/restapi/v2.1/accounts/{{accountId}}/envelopes?status=sent` — list envelopes
- `POST {{baseUrl}}/restapi/v2.1/accounts/{{accountId}}/envelopes` — create envelope
- `GET {{baseUrl}}/restapi/v2.1/accounts/{{accountId}}/envelopes/env-2001` — get envelope
- `PUT {{baseUrl}}/restapi/v2.1/accounts/{{accountId}}/envelopes/env-2001` — void envelope
- `GET {{baseUrl}}/restapi/v2.1/accounts/{{accountId}}/envelopes/env-2003/recipients` — list recipients
- `GET {{baseUrl}}/restapi/v2.1/accounts/{{accountId}}/envelopes/env-2001/documents` — list documents
- `GET {{baseUrl}}/restapi/v2.1/accounts/{{accountId}}/templates` — list templates

---

## 57. Mixpanel API

**Service**: `mixpanel-api` · **Port**: 8056 · **Env**: `MIXPANEL_API_URL`

Mock service mirroring Mixpanel API endpoints. See `mixpanel-api/mixpanel-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `POST {{baseUrl}}/track` — track event
- `GET {{baseUrl}}/api/2.0/events?event=App Open,Checkout&from_date=2025-05-01&to_date=2025-05-04` — events counts
- `GET {{baseUrl}}/api/2.0/funnels/list` — funnels list
- `GET {{baseUrl}}/api/2.0/funnels?funnel_id=7461001` — funnel
- `GET {{baseUrl}}/api/2.0/segmentation?event=App Open&from_date=2025-05-01&to_date=2025-05-04&on=country` — segmentation
- `GET {{baseUrl}}/api/2.0/engage?where=plan==paid` — engage profiles
- `GET {{baseUrl}}/api/2.0/engage?distinct_id=user-aria` — engage one profile

---

## 59. Reddit API

**Service**: `reddit-api` · **Port**: 8058 · **Env**: `REDDIT_API_URL`

Mock service mirroring Reddit API endpoints. See `reddit-api/reddit-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/r/programming/about` — subreddit about
- `GET {{baseUrl}}/r/programming/hot?limit=10` — subreddit hot
- `GET {{baseUrl}}/r/homelab/new?limit=10` — subreddit new
- `GET {{baseUrl}}/comments/t3_p001` — post comments
- `POST {{baseUrl}}/api/submit` — submit post
- `POST {{baseUrl}}/api/vote` — vote up
- `GET {{baseUrl}}/user/devkat/about` — user about

---

## 63. Linkedin API

**Service**: `linkedin-api` · **Port**: 8062 · **Env**: `LINKEDIN_API_URL`

Mock service mirroring Linkedin API endpoints. See `linkedin-api/linkedin-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v2/me` — get me
- `GET {{baseUrl}}/v2/connections?count=10` — list connections
- `GET {{baseUrl}}/v2/posts` — list posts
- `GET {{baseUrl}}/v2/posts?author_id=urn:li:person:amelia-ortega` — list posts by author
- `GET {{baseUrl}}/v2/posts/6003` — get post
- `POST {{baseUrl}}/v2/posts` — create post
- `GET {{baseUrl}}/v2/organizations/5001` — get organization
- `GET {{baseUrl}}/v2/jobs?keywords=backend&location=Remote` — search jobs
- `GET {{baseUrl}}/v2/jobs/7001` — get job

---

## 65. Twitch API

**Service**: `twitch-api` · **Port**: 8064 · **Env**: `TWITCH_API_URL`

Mock service mirroring Twitch API endpoints. See `twitch-api/twitch-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/helix/users?login=pixelpaladin` — get users
- `GET {{baseUrl}}/helix/streams` — get streams (live)
- `GET {{baseUrl}}/helix/streams?user_login=sprintqueen` — get streams by login
- `GET {{baseUrl}}/helix/channels?broadcaster_id=40001` — get channel
- `GET {{baseUrl}}/helix/channels/followers?broadcaster_id=40003` — get channel followers
- `GET {{baseUrl}}/helix/games/top?first=5` — get top games
- `GET {{baseUrl}}/helix/games?name=Elden Ring` — get game by name
- `GET {{baseUrl}}/helix/clips?broadcaster_id=40001` — get clips by broadcaster

---

## 67. Contentful API

**Service**: `contentful-api` · **Port**: 8066 · **Env**: `CONTENTFUL_API_URL`

Mock service mirroring Contentful API endpoints. See `contentful-api/contentful-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/spaces/space-orbit-cms` — get space
- `GET {{baseUrl}}/spaces/space-orbit-cms/environments/master/content_types` — list content types
- `GET {{baseUrl}}/spaces/space-orbit-cms/environments/master/content_types/blogPost` — get content type
- `GET {{baseUrl}}/spaces/space-orbit-cms/environments/master/entries?content_type=blogPost&limit=10` — list entries
- `GET {{baseUrl}}/spaces/space-orbit-cms/environments/master/entries?content_type=blogPost&fields.slug=getting-started` — list entries by field
- `GET {{baseUrl}}/spaces/space-orbit-cms/environments/master/entries/post-getting-started` — get entry
- `POST {{baseUrl}}/spaces/space-orbit-cms/environments/master/entries` — create entry
- `PUT {{baseUrl}}/spaces/space-orbit-cms/environments/master/entries/post-draft-webhooks` — update entry
- `DELETE {{baseUrl}}/spaces/space-orbit-cms/environments/master/entries/post-content-modeling` — delete entry
- `GET {{baseUrl}}/spaces/space-orbit-cms/environments/master/assets` — list assets
- `GET {{baseUrl}}/spaces/space-orbit-cms/environments/master/assets/asset-hero-1` — get asset

---

## 70. Intercom API

**Service**: `intercom-api` · **Port**: 8070 · **Env**: `INTERCOM_API_URL`

Mock service mirroring Intercom API endpoints. See `intercom-api/intercom-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/contacts?role=user` — list contacts
- `GET {{baseUrl}}/contacts/contact-mara` — get contact
- `POST {{baseUrl}}/contacts` — create contact
- `GET {{baseUrl}}/conversations?state=open` — list conversations
- `GET {{baseUrl}}/conversations/conv-1001` — get conversation
- `POST {{baseUrl}}/conversations` — create conversation
- `POST {{baseUrl}}/conversations/conv-1001/reply` — reply to conversation
- `POST {{baseUrl}}/conversations/conv-1003/parts` — assign conversation
- `POST {{baseUrl}}/conversations/conv-1001/parts` — close conversation
- `GET {{baseUrl}}/companies` — list companies
- `GET {{baseUrl}}/companies/company-brightpath` — get company

---

## 71. Servicenow API

**Service**: `servicenow-api` · **Port**: 8071 · **Env**: `SERVICENOW_API_URL`

Mock service mirroring Servicenow API endpoints. See `servicenow-api/servicenow-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/now/table/incident` — list incidents
- `GET {{baseUrl}}/api/now/table/incident?sysparm_query=state=2^priority=1&sysparm_limit=5` — list incidents filtered
- `GET {{baseUrl}}/api/now/table/incident/inc-0001001` — get incident
- `POST {{baseUrl}}/api/now/table/incident` — create incident
- `PATCH {{baseUrl}}/api/now/table/incident/inc-0001003` — update incident
- `GET {{baseUrl}}/api/now/table/change_request` — list change requests
- `GET {{baseUrl}}/api/now/table/change_request/chg-0002001` — get change request
- `GET {{baseUrl}}/api/now/table/problem` — list problems
- `GET {{baseUrl}}/api/now/table/problem/prb-0003001` — get problem
- `GET {{baseUrl}}/api/now/table/sys_user` — list users
- `GET {{baseUrl}}/api/now/table/sys_user/usr-amelia` — get user

---

## 72. Bamboohr API

**Service**: `bamboohr-api` · **Port**: 8072 · **Env**: `BAMBOOHR_API_URL`

Mock service mirroring Bamboohr API endpoints. See `bamboohr-api/bamboohr-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/gateway.php/{{company}}/v1/company` — get company
- `GET {{baseUrl}}/api/gateway.php/{{company}}/v1/employees/directory` — employees directory
- `GET {{baseUrl}}/api/gateway.php/{{company}}/v1/employees/emp-102` — get employee
- `POST {{baseUrl}}/api/gateway.php/{{company}}/v1/employees` — create employee
- `GET {{baseUrl}}/api/gateway.php/{{company}}/v1/time_off/requests?status=requested` — list time off requests
- `POST {{baseUrl}}/api/gateway.php/{{company}}/v1/time_off/requests` — create time off request
- `PUT {{baseUrl}}/api/gateway.php/{{company}}/v1/time_off/requests/tor-5003/status` — approve time off request
- `GET {{baseUrl}}/api/gateway.php/{{company}}/v1/time_off/whos_out` — whos out
- `GET {{baseUrl}}/api/gateway.php/{{company}}/v1/reports/1` — get report

---

## 73. Greenhouse API

**Service**: `greenhouse-api` · **Port**: 8073 · **Env**: `GREENHOUSE_API_URL`

Mock service mirroring Greenhouse API endpoints. See `greenhouse-api/greenhouse-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v1/candidates` — list candidates
- `GET {{baseUrl}}/v1/candidates/cand-7001` — get candidate
- `POST {{baseUrl}}/v1/candidates` — create candidate
- `GET {{baseUrl}}/v1/jobs?status=open` — list jobs open
- `GET {{baseUrl}}/v1/jobs/job-3001` — get job
- `GET {{baseUrl}}/v1/applications?job_id=job-3001` — list applications
- `GET {{baseUrl}}/v1/applications/app-4001` — get application
- `POST {{baseUrl}}/v1/applications/app-4001/advance` — advance application
- `POST {{baseUrl}}/v1/applications/app-4007/reject` — reject application
- `GET {{baseUrl}}/v1/scorecards?application_id=app-4002` — list scorecards

---

## 74. Gusto API

**Service**: `gusto-api` · **Port**: 8074 · **Env**: `GUSTO_API_URL`

Mock service mirroring Gusto API endpoints. See `gusto-api/gusto-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v1/companies/{{companyId}}` — get company
- `GET {{baseUrl}}/v1/companies/{{companyId}}/employees` — list company employees
- `GET {{baseUrl}}/v1/employees/gemp-202` — get employee
- `GET {{baseUrl}}/v1/companies/{{companyId}}/payrolls` — list company payrolls
- `GET {{baseUrl}}/v1/companies/{{companyId}}/payrolls?processed=false` — list unprocessed payrolls
- `GET {{baseUrl}}/v1/payrolls/pay-401` — get payroll
- `POST {{baseUrl}}/v1/companies/{{companyId}}/payrolls` — create payroll
- `PUT {{baseUrl}}/v1/payrolls/pay-404/submit` — submit payroll
- `GET {{baseUrl}}/v1/companies/{{companyId}}/contractors` — list company contractors

---

## 75. Ticketmaster API

**Service**: `ticketmaster-api` · **Port**: 8075 · **Env**: `TICKETMASTER_API_URL`

Mock service mirroring Ticketmaster API endpoints. See `ticketmaster-api/ticketmaster-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/discovery/v2/events` — search events
- `GET {{baseUrl}}/discovery/v2/events?keyword=Aria` — search events by keyword
- `GET {{baseUrl}}/discovery/v2/events?city=New York&classificationName=Music` — search events by city + classification
- `GET {{baseUrl}}/discovery/v2/events?startDateTime=2026-09-01T00:00:00Z` — search events by startDateTime
- `GET {{baseUrl}}/discovery/v2/events/evt-1001` — get event
- `GET {{baseUrl}}/discovery/v2/venues?keyword=Arena` — search venues
- `GET {{baseUrl}}/discovery/v2/venues/ven-001` — get venue
- `GET {{baseUrl}}/discovery/v2/attractions?keyword=Echoes` — search attractions
- `GET {{baseUrl}}/discovery/v2/attractions/att-001` — get attraction
- `GET {{baseUrl}}/discovery/v2/classifications` — list classifications

---

## 77. Nasa API

**Service**: `nasa-api` · **Port**: 8077 · **Env**: `NASA_API_URL`

Mock service mirroring Nasa API endpoints. See `nasa-api/nasa-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/planetary/apod` — apod latest
- `GET {{baseUrl}}/planetary/apod?date=2026-05-24` — apod by date
- `GET {{baseUrl}}/planetary/apod?start_date=2026-05-20&end_date=2026-05-23` — apod range
- `GET {{baseUrl}}/mars-photos/api/v1/rovers/curiosity/photos?sol=4100&camera=MAST` — rover photos
- `GET {{baseUrl}}/mars-photos/api/v1/rovers/perseverance` — rover manifest
- `GET {{baseUrl}}/neo/rest/v1/feed?start_date=2026-05-20&end_date=2026-05-21` — neo feed
- `GET {{baseUrl}}/neo/rest/v1/neo/3726710` — neo by id
- `GET {{baseUrl}}/EPIC/api/natural` — epic natural

---

## 78. Openlibrary API

**Service**: `openlibrary-api` · **Port**: 8078 · **Env**: `OPENLIBRARY_API_URL`

Mock service mirroring Openlibrary API endpoints. See `openlibrary-api/openlibrary-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/search.json?q=foundation` — search by q
- `GET {{baseUrl}}/search.json?author=Le%20Guin` — search by author
- `GET {{baseUrl}}/search.json?title=Dune` — search by title
- `GET {{baseUrl}}/works/OL893415W.json` — get work
- `GET {{baseUrl}}/works/OL27448W/editions.json` — get work editions
- `GET {{baseUrl}}/authors/OL26320A.json` — get author
- `GET {{baseUrl}}/authors/OL34184A/works.json` — get author works
- `GET {{baseUrl}}/subjects/science_fiction.json` — get subject
- `GET {{baseUrl}}/isbn/9780441013593.json` — get isbn

---

## 80. Monday API

**Service**: `monday-api` · **Port**: 8080 · **Env**: `MONDAY_API_URL`

Mock service mirroring Monday API endpoints. See `monday-api/monday-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v2/workspaces` — list workspaces
- `GET {{baseUrl}}/v2/boards?workspace_id=ws-1` — list boards
- `GET {{baseUrl}}/v2/boards/board-101` — get board
- `GET {{baseUrl}}/v2/boards/board-101/items` — board items
- `GET {{baseUrl}}/v2/items?board_id=board-101&group_id=grp-todo` — list items
- `GET {{baseUrl}}/v2/items/item-1001` — get item
- `POST {{baseUrl}}/v2/items` — create item
- `PUT {{baseUrl}}/v2/items/item-1002` — update item (change status)
- `PUT {{baseUrl}}/v2/items/item-1002` — update item (move group)
- `DELETE {{baseUrl}}/v2/items/item-1004` — delete item
- `GET {{baseUrl}}/v2/users` — list users

---

## 84. Bigcommerce API

**Service**: `bigcommerce-api` · **Port**: 8084 · **Env**: `BIGCOMMERCE_API_URL`

Mock service mirroring Bigcommerce API endpoints. See `bigcommerce-api/bigcommerce-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v3/catalog/products?limit=5&page=1` — list products
- `GET {{baseUrl}}/v3/catalog/products?name=wireless` — filter products by name
- `GET {{baseUrl}}/v3/catalog/products/101` — get product
- `GET {{baseUrl}}/v2/orders?customer_id=1001` — list orders
- `GET {{baseUrl}}/v2/orders/2001` — get order
- `POST {{baseUrl}}/v2/orders` — create order
- `GET {{baseUrl}}/v3/customers?email=olivia` — list customers

---

## 85. Woocommerce API

**Service**: `woocommerce-api` · **Port**: 8085 · **Env**: `WOOCOMMERCE_API_URL`

Mock service mirroring Woocommerce API endpoints. See `woocommerce-api/woocommerce-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/wp-json/wc/v3/products?per_page=5&page=1` — list products
- `GET {{baseUrl}}/wp-json/wc/v3/products?search=mug` — search products
- `GET {{baseUrl}}/wp-json/wc/v3/products/201` — get product
- `GET {{baseUrl}}/wp-json/wc/v3/orders?customer=301` — list orders
- `GET {{baseUrl}}/wp-json/wc/v3/orders/401` — get order
- `POST {{baseUrl}}/wp-json/wc/v3/orders` — create order
- `GET {{baseUrl}}/wp-json/wc/v3/customers?email=emma` — list customers

---

## 86. Microsoft Teams API

**Service**: `microsoft-teams-api` · **Port**: 8086 · **Env**: `MICROSOFT_TEAMS_API_URL`

Mock service mirroring Microsoft Teams API endpoints. See `microsoft-teams-api/microsoft-teams-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v1.0/me/joinedTeams` — joined teams
- `GET {{baseUrl}}/v1.0/teams/19:team-eng0001@thread.tacv2` — get team
- `GET {{baseUrl}}/v1.0/teams/19:team-eng0001@thread.tacv2/channels` — list channels
- `GET {{baseUrl}}/v1.0/teams/19:team-eng0001@thread.tacv2/channels/19:chan-eng-gen01@thread.tacv2/messages` — list channel messages
- `POST {{baseUrl}}/v1.0/teams/19:team-eng0001@thread.tacv2/channels/19:chan-eng-gen01@thread.tacv2/messages` — send channel message

---

## 87. Outlook API

**Service**: `outlook-api` · **Port**: 8087 · **Env**: `OUTLOOK_API_URL`

Mock service mirroring Outlook API endpoints. See `outlook-api/outlook-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v1.0/me/messages` — list messages
- `GET {{baseUrl}}/v1.0/me/messages?isRead=false` — list unread messages
- `GET {{baseUrl}}/v1.0/me/messages/AAMkAGmsg0000001` — get message
- `POST {{baseUrl}}/v1.0/me/sendMail` — send mail
- `GET {{baseUrl}}/v1.0/me/events` — list events
- `GET {{baseUrl}}/v1.0/me/contacts` — list contacts

---

## 89. Klaviyo API

**Service**: `klaviyo-api` · **Port**: 8089 · **Env**: `KLAVIYO_API_URL`

Mock service mirroring Klaviyo API endpoints. See `klaviyo-api/klaviyo-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/profiles` — list profiles
- `GET {{baseUrl}}/api/profiles?email=jane.doe@example.com` — filter profiles by email
- `GET {{baseUrl}}/api/profiles/01HZPROF000000000000000001` — get profile
- `POST {{baseUrl}}/api/profiles` — create profile
- `GET {{baseUrl}}/api/lists` — list lists
- `GET {{baseUrl}}/api/campaigns` — list campaigns
- `GET {{baseUrl}}/api/campaigns?status=Sent&channel=email` — list sent email campaigns

---

## 90. Segment API

**Service**: `segment-api` · **Port**: 8090 · **Env**: `SEGMENT_API_URL`

Mock service mirroring Segment API endpoints. See `segment-api/segment-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `POST {{baseUrl}}/v1/track` — track
- `POST {{baseUrl}}/v1/identify` — identify
- `POST {{baseUrl}}/v1/page` — page
- `POST {{baseUrl}}/v1/batch` — batch
- `GET {{baseUrl}}/v1/events` — events
- `GET {{baseUrl}}/v1/events?type=track&userId=user_1001` — events by type
- `GET {{baseUrl}}/v1/sources` — sources
- `GET {{baseUrl}}/v1/destinations` — destinations

---

## 92. Posthog API

**Service**: `posthog-api` · **Port**: 8092 · **Env**: `POSTHOG_API_URL`

Mock service mirroring Posthog API endpoints. See `posthog-api/posthog-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `POST {{baseUrl}}/capture` — capture
- `POST {{baseUrl}}/decide` — decide
- `GET {{baseUrl}}/api/projects/1/events` — events
- `GET {{baseUrl}}/api/projects/1/events?event=$pageview&distinct_id=user_3001` — events filtered
- `GET {{baseUrl}}/api/projects/1/feature_flags` — feature flags
- `GET {{baseUrl}}/api/projects/1/persons` — persons

---

## 93. Freshdesk API

**Service**: `freshdesk-api` · **Port**: 8093 · **Env**: `FRESHDESK_API_URL`

Mock service mirroring Freshdesk API endpoints. See `freshdesk-api/freshdesk-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/v2/tickets` — list tickets
- `GET {{baseUrl}}/api/v2/tickets?status=2&priority=2` — list tickets filtered
- `GET {{baseUrl}}/api/v2/tickets/70001` — get ticket
- `POST {{baseUrl}}/api/v2/tickets` — create ticket
- `PUT {{baseUrl}}/api/v2/tickets/70001` — update ticket
- `GET {{baseUrl}}/api/v2/contacts` — list contacts
- `GET {{baseUrl}}/api/v2/agents` — list agents

---

## 98. Kraken API

**Service**: `kraken-api` · **Port**: 8098 · **Env**: `KRAKEN_API_URL`

Mock service mirroring Kraken API endpoints. See `kraken-api/kraken-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/0/public/Ticker?pair=XBTUSD` — ticker single
- `GET {{baseUrl}}/0/public/Ticker?pair=XBTUSD,ETHUSD` — ticker multi
- `GET {{baseUrl}}/0/public/Ticker` — ticker all
- `GET {{baseUrl}}/0/public/OHLC?pair=XBTUSD&interval=60` — ohlc
- `GET {{baseUrl}}/0/public/AssetPairs` — asset pairs all
- `GET {{baseUrl}}/0/public/AssetPairs?pair=ETHUSD` — asset pairs filter
- `GET {{baseUrl}}/0/public/Assets` — assets all
- `GET {{baseUrl}}/0/public/Assets?asset=XBT,ETH` — assets filter
- `POST {{baseUrl}}/0/private/Balance` — balance

---

## 100. Webflow API

**Service**: `webflow-api` · **Port**: 8100 · **Env**: `WEBFLOW_API_URL`

Mock service mirroring Webflow API endpoints. See `webflow-api/webflow-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/v2/sites` — list sites
- `GET {{baseUrl}}/v2/sites/650a1f0000000000000001a1` — get site
- `GET {{baseUrl}}/v2/sites/650a1f0000000000000001a1/collections` — list collections
- `GET {{baseUrl}}/v2/collections/660b2a0000000000000002b1/items?limit=100&offset=0` — list items
- `POST {{baseUrl}}/v2/collections/660b2a0000000000000002b1/items` — create item

---

## 101. Activecampaign API

**Service**: `activecampaign-api` · **Port**: 8101 · **Env**: `ACTIVECAMPAIGN_API_URL`

Mock service mirroring Activecampaign API endpoints. See `activecampaign-api/activecampaign-api_postman_collection.json`*` for the runnable request collection.

### Endpoints

#### Endpoints

- `GET {{baseUrl}}/health` — health
- `GET {{baseUrl}}/api/3/contacts?limit=20&offset=0` — list contacts
- `GET {{baseUrl}}/api/3/contacts?email=olivia.bennett@example.com` — filter contacts by email
- `GET {{baseUrl}}/api/3/contacts/4` — get contact
- `POST {{baseUrl}}/api/3/contacts` — create contact
- `GET {{baseUrl}}/api/3/lists` — list lists
- `GET {{baseUrl}}/api/3/campaigns` — list campaigns
- `GET {{baseUrl}}/api/3/deals` — list deals

---

