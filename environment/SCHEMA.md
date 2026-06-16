# WildClawBench Mock-API Fleet — Data Schema Reference

This document catalogs the data schema of every mock API in the WildClawBench fleet. Each of the 101 services is a self-contained FastAPI app pairing:

- **`<name>_data.py`** — in-memory `_store` data layer: declares entities via `_store.register("<table>", primary_key=..., initial_loader=...)`, exposes top-level operation functions (list/get/create/update/delete), and provides `_coerce_<entity>` (internal field shape) plus `_serialize_<entity>` (wire/response field shape).
- **`server.py`** — FastAPI routes (`@app.<method>("<path>")`) that call into the data-layer functions.

### How to read each section

- **Base path** — common URL prefix (or "varies" when routes are heterogeneous).
- **Entities** — every `_store.register(...)` table and `_store.register_document(...)` singleton, with primary key, internal fields (from `_coerce_<entity>`), and wire fields (from `_serialize_<entity>`). Renamed wire keys are shown as `wire_name (← internal_name)`. "Same as internal" means no serializer rewrites keys.
- **Endpoints** — every route in source order, `METHOD path → data-layer function`.
- **Relationships** — foreign-key style links between entities.
- **Notes** — pagination, envelope shapes, error formats, and notable quirks.

### Conventions across the fleet

- Type coercion via shared helpers: `strict_int`, `strict_bool`, `opt_csv_list`, etc.
- Shared infrastructure (at `environment/` root): `tracking_middleware.py`, `admin_plane.py`, `_mutable_store.py`.
- Most services expose admin / debug endpoints inherited from the shared admin plane (`/__admin/*`).
- Timestamps are ISO-8601 unless noted.

### Extraction methodology

The first 7 sections (`activecampaign-api` … `amazon-seller-api`) were authored by hand from a deep read of each `_data.py` + `server.py`. The remaining 94 sections are derived automatically by a static AST scan that enumerates `_store.register(...)` calls, recognised shaping helpers (`_coerce_*`, `_serialize_*`, `_format_*`, `_view_*`, `_<entity>_obj`, `_<entity>_view`, `_fmt_*`, `_public_*`, etc.), and FastAPI route decorators (`@app.<method>(...)`).

Helper-to-entity matching uses both name-pattern templates (with singular/plural alias rules and a small abbreviation map for `namespaces`→`ns`, `scheduled_events`→`event`, etc.) **and** callsite proximity — i.e. when a public data-layer function reads `_store.list("foo")` and calls `_format_bar(...)`, `_format_bar` is associated with the `foo` table. This catches mismatches between table names and helper names.

Field-list conventions:
- A leading `…raw row…` marker indicates the helper spreads `{**row, …}` (or `{**_strip_ctx(row), …}`); subsequent identifiers are the explicit overrides added on top.
- `passthrough — no field rewrites` means the helper exists but returns rows unchanged (e.g. `[r for r in rows]`) — no field transformations occur in the data layer.
- `no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape)` means no shaping helper was found at all and route handlers construct the wire payload inline; check `server.py` for the exact dict shape.

Route paths are resolved through string-literal decorators, `_BASE + "..."` concatenations, and f-strings whose components are module-level string constants.

---

## Table of Contents

1. [activecampaign-api](#activecampaign-api)
2. [airbnb-api](#airbnb-api)
3. [airtable-api](#airtable-api)
4. [algolia-api](#algolia-api)
5. [alpaca-api](#alpaca-api)
6. [amadeus-api](#amadeus-api)
7. [amazon-seller-api](#amazon-seller-api)
8. [amplitude-api](#amplitude-api)
9. [asana-api](#asana-api)
10. [bamboohr-api](#bamboohr-api)
11. [bigcommerce-api](#bigcommerce-api)
12. [binance-api](#binance-api)
13. [box-api](#box-api)
14. [calendly-api](#calendly-api)
15. [cloudflare-api](#cloudflare-api)
16. [coinbase-api](#coinbase-api)
17. [confluence-api](#confluence-api)
18. [contentful-api](#contentful-api)
19. [datadog-api](#datadog-api)
20. [discord-api](#discord-api)
21. [docusign-api](#docusign-api)
22. [doordash-api](#doordash-api)
23. [dropbox-api](#dropbox-api)
24. [etsy-api](#etsy-api)
25. [eventbrite-api](#eventbrite-api)
26. [fedex-api](#fedex-api)
27. [figma-api](#figma-api)
28. [freshdesk-api](#freshdesk-api)
29. [github-api](#github-api)
30. [gitlab-api](#gitlab-api)
31. [gmail-api](#gmail-api)
32. [google-analytics-api](#google-analytics-api)
33. [google-calendar-api](#google-calendar-api)
34. [google-classroom-api](#google-classroom-api)
35. [google-drive-api](#google-drive-api)
36. [google-maps-api](#google-maps-api)
37. [greenhouse-api](#greenhouse-api)
38. [gusto-api](#gusto-api)
39. [hubspot-api](#hubspot-api)
40. [instacart-api](#instacart-api)
41. [instagram-api](#instagram-api)
42. [intercom-api](#intercom-api)
43. [jira-api](#jira-api)
44. [klaviyo-api](#klaviyo-api)
45. [kraken-api](#kraken-api)
46. [kubernetes-api](#kubernetes-api)
47. [linear-api](#linear-api)
48. [linkedin-api](#linkedin-api)
49. [mailchimp-api](#mailchimp-api)
50. [mailgun-api](#mailgun-api)
51. [microsoft-teams-api](#microsoft-teams-api)
52. [mixpanel-api](#mixpanel-api)
53. [monday-api](#monday-api)
54. [myfitnesspal-api](#myfitnesspal-api)
55. [nasa-api](#nasa-api)
56. [notion-api](#notion-api)
57. [obsidian-api](#obsidian-api)
58. [okta-api](#okta-api)
59. [openlibrary-api](#openlibrary-api)
60. [openweather-api](#openweather-api)
61. [outlook-api](#outlook-api)
62. [pagerduty-api](#pagerduty-api)
63. [paypal-api](#paypal-api)
64. [pinterest-api](#pinterest-api)
65. [plaid-api](#plaid-api)
66. [posthog-api](#posthog-api)
67. [quickbooks-api](#quickbooks-api)
68. [reddit-api](#reddit-api)
69. [ring-api](#ring-api)
70. [salesforce-api](#salesforce-api)
71. [segment-api](#segment-api)
72. [sendgrid-api](#sendgrid-api)
73. [sentry-api](#sentry-api)
74. [servicenow-api](#servicenow-api)
75. [shippo-api](#shippo-api)
76. [slack-api](#slack-api)
77. [spotify-api](#spotify-api)
78. [square-api](#square-api)
79. [strava-api](#strava-api)
80. [stripe-api](#stripe-api)
81. [telegram-api](#telegram-api)
82. [ticketmaster-api](#ticketmaster-api)
83. [tmdb-api](#tmdb-api)
84. [trello-api](#trello-api)
85. [twilio-api](#twilio-api)
86. [twitch-api](#twitch-api)
87. [twitter-api](#twitter-api)
88. [typeform-api](#typeform-api)
89. [uber-api](#uber-api)
90. [ups-api](#ups-api)
91. [vimeo-api](#vimeo-api)
92. [webflow-api](#webflow-api)
93. [whatsapp-api](#whatsapp-api)
94. [woocommerce-api](#woocommerce-api)
95. [wordpress-api](#wordpress-api)
96. [xero-api](#xero-api)
97. [yelp-api](#yelp-api)
98. [youtube-api](#youtube-api)
99. [zendesk-api](#zendesk-api)
100. [zillow-api](#zillow-api)
101. [zoom-api](#zoom-api)

---

### activecampaign-api

**Base path**: `/api/3`

**Entities** (from `_store.register(...)` in `activecampaign_data.py`):

- **contacts** (pk=`id`)
  - Internal fields (from `_coerce_contacts`): `id` (str), `email` (str), `first_name` (str), `last_name` (str), `phone` (str), `status` (str), `created_timestamp` (iso-timestamp str), `updated_timestamp` (iso-timestamp str)
  - Wire fields (from `_serialize_contact`): `id`, `email`, `firstName (← first_name)`, `lastName (← last_name)`, `phone`, `status`, `cdate (← created_timestamp)`, `udate (← updated_timestamp)`, `links` (object with `contactLists` and `deals` URL templates)
- **lists** (pk=`id`)
  - Internal fields (from `_coerce_lists`): `id` (str), `name` (str), `stringid` (str), `subscriber_count` (int), `sender_url` (str), `sender_reminder` (str), `created_timestamp` (iso-timestamp str)
  - Wire fields (from `_serialize_list`): `id`, `name`, `stringid`, `subscriber_count`, `sender_url`, `sender_reminder`, `cdate (← created_timestamp)`
- **campaigns** (pk=`id`)
  - Internal fields (from `_coerce_campaigns`): `id` (str), `name` (str), `type` (str), `status` (str), `list_id` (str), `subject` (str), `send_amt` (int), `opens` (int), `clicks` (int), `sdate` (iso-timestamp str), `created_timestamp` (iso-timestamp str)
  - Wire fields (from `_serialize_campaign`): `id`, `name`, `type`, `status`, `listid (← list_id)`, `subject`, `send_amt`, `opens`, `linkclicks (← clicks)`, `sdate`, `cdate (← created_timestamp)`
- **deals** (pk=`id`)
  - Internal fields (from `_coerce_deals`): `id` (str), `title` (str), `contact_id` (str), `value` (number), `currency` (str), `status` (str), `stage` (str), `owner` (str), `created_timestamp` (iso-timestamp str), `updated_timestamp` (iso-timestamp str)
  - Wire fields (from `_serialize_deal`): `id`, `title`, `contact (← contact_id)`, `value`, `currency`, `status`, `stage`, `owner`, `cdate (← created_timestamp)`, `mdate (← updated_timestamp)`

**Endpoints**:
- `GET /health` → `{"status": "ok"}`
- `GET /api/3/contacts` → `list_contacts(email, status, limit, offset)`
- `GET /api/3/contacts/{contact_id}` → `get_contact(contact_id)`
- `POST /api/3/contacts` → `create_contact(email, first_name, last_name, phone, status)` (reads payload `contact` envelope)
- `GET /api/3/lists` → `list_lists(limit, offset)`
- `GET /api/3/campaigns` → `list_campaigns(limit, offset)`
- `GET /api/3/deals` → `list_deals(limit, offset)`

**Relationships**:
- `campaigns.list_id` → `lists.id`
- `deals.contact_id` → `contacts.id`

**Notes**:
- Pagination: `limit`/`offset` query params (default limit=20, offset=0).
- List envelope: top-level plural key (`contacts`, `lists`, `campaigns`, `deals`) plus `meta: {total: "<str>", page_input: {offset, limit}}` (note total is stringified).
- Get/create envelope: `{contact: {...}}` (singular).
- Error envelope: `{error: <code>, message: <str>}`. `create_contact` returns 422 for `validation` / `duplicate`, 404 for other errors.
- Admin plane mounted via `install_admin_plane`.

---

### airbnb-api

**Base path**: `/v2`

**Entities** (from `_store.register(...)` in `airbnb_data.py`):

- **listings** (pk=`listing_id`)
  - Internal fields (from `_coerce_listings`): all raw JSON fields (passed via `_strip_ctx`) plus coerced: `price_per_night` (float), `cleaning_fee` (float), `beds` (int), `baths` (float), `max_guests` (int), `rating` (float), `review_count` (int), `instant_book` (bool). Raw JSON columns include at minimum: `listing_id`, `host_id`, `title`, `city`, `country`, `price_per_night`, `cleaning_fee`, `beds`, `baths`, `max_guests`, `rating`, `review_count`, `instant_book` (plus any others present in `listings.json`).
  - Wire fields: no `_serialize_` helper — listings are returned via `_attach_host` which adds a `host` sub-object pulled from the hosts table; otherwise same as internal.
- **hosts** (pk=`host_id`)
  - Internal fields (from `_coerce_hosts`): raw JSON fields plus coerced: `superhost` (bool), `joined_year` (int), `response_rate` (int), `languages` (list[str] split from CSV).
  - Wire fields: no `_serialize_` helper — same as internal; embedded under `listing.host`.
- **availability** (pk=`_pk` where `_pk = f"{listing_id}@{start_date}"`)
  - Internal fields (from `_coerce_availability` + loader): raw JSON fields (`listing_id`, `start_date`, `end_date`, `available`) plus coerced `available` (bool) and synthetic `_pk` (str).
  - Wire fields: no `_serialize_` helper — same as internal; returned inside `windows` array.
- **reviews** (pk=`review_id`)
  - Internal fields (from `_coerce_reviews`): raw JSON fields (`review_id`, `listing_id`, etc.) plus coerced `rating` (int).
  - Wire fields: no `_serialize_` helper — same as internal.
- **reservations** (pk=`reservation_id`, empty initial loader)
  - Internal fields (built in `create_reservation`): `reservation_id` (str, `res-<hex10>`), `listing_id` (str), `guest_name` (str), `checkin` (iso-date str), `checkout` (iso-date str), `nights` (int), `guests` (int), `status` (str: `"confirmed"`/`"cancelled"`), `nightly_subtotal` (float), `cleaning_fee` (float), `service_fee` (float), `total` (float), `created_at` (iso-timestamp str).
  - Wire fields: no `_serialize_` helper — same as internal.

**Endpoints**:
- `GET /health` → `{"status": "ok"}`
- `GET /v2/listings/search` → `search_listings(location, checkin, checkout, guests, min_price, max_price)`
- `GET /v2/listings/{listing_id}` → `get_listing(listing_id)`
- `GET /v2/listings/{listing_id}/availability` → `get_availability(listing_id)`
- `GET /v2/listings/{listing_id}/reviews` → `get_reviews(listing_id)`
- `POST /v2/reservations` → `create_reservation(listing_id, checkin, checkout, guests, guest_name)` (Pydantic `ReservationBody`)
- `GET /v2/reservations/{reservation_id}` → `get_reservation(reservation_id)`
- `DELETE /v2/reservations/{reservation_id}` → `cancel_reservation(reservation_id)`

**Relationships**:
- `listings.host_id` → `hosts.host_id`
- `availability.listing_id` → `listings.listing_id`
- `reviews.listing_id` → `listings.listing_id`
- `reservations.listing_id` → `listings.listing_id`

**Notes**:
- Search response envelope: `{count, listings: [...]}` (sorted by `rating` desc).
- Availability/reviews envelope: `{listing_id, windows: [...]}` / `{listing_id, count, reviews: [...]}`.
- Error envelope: `{error: "<message>"}`. 404 when "not found" in message, 400 otherwise.
- Pricing: `SERVICE_FEE_PCT = 14.0%` applied to nightly subtotal in `create_reservation`.
- Availability rule: stay must be fully covered by an `available=true` window and not intersect any `available=false` window.

---

### airtable-api

**Base path**: `/v0` (meta under `/v0/meta`)

**Entities** (from `_store.register(...)` in `airtable_data.py`):

- **bases** (pk=`id`)
  - Internal fields: raw JSON columns (no `_coerce_`), at minimum `id`, `name`, `permissionLevel`.
  - Wire fields (via `list_bases`): `id`, `name`, `permissionLevel` (subset projection).
- **tables** (pk=`id`)
  - Internal fields: raw JSON columns (no `_coerce_`), at minimum `id`, `name`, `baseId`, `primaryFieldId`, `records_csv`.
  - Wire fields (via `list_tables`): `id`, `name`, `primaryFieldId`, `fields` (list of `{id, name, type}` from in-memory `_field_meta`).
- **records_<tableId>** — one mutable table per Airtable table (pk=`id`, e.g. `recXXXXX`).
  - Internal fields (from `_coerce_records`): `id` (str), `createdTime` (iso-timestamp str), `fields` (dict; each non-`id`/non-`createdTime` column cast via `_cast_field`: `number` → int/float, `checkbox` → bool, else string; `None` values dropped).
  - Wire fields: no separate `_serialize_` — same as internal `{id, createdTime, fields}`.

**Endpoints**:
- `GET /health` → `{"status": "ok"}`
- `GET /v0/meta/bases` → `list_bases()`
- `GET /v0/meta/bases/{base_id}/tables` → `list_tables(base_id)`
- `GET /v0/{base_id}/{table_id_or_name}` → `list_records(base_id, table_id_or_name, page_size, offset, filter_by_formula)`
- `GET /v0/{base_id}/{table_id_or_name}/{record_id}` → `get_record(base_id, table_id_or_name, record_id)`
- `POST /v0/{base_id}/{table_id_or_name}` → `create_records(base_id, table_id_or_name, records)` (Pydantic `RecordsCreateBody{records: [{fields}]}`)
- `PATCH /v0/{base_id}/{table_id_or_name}/{record_id}` → `update_record(base_id, table_id_or_name, record_id, fields)` (Pydantic `RecordPatchBody{fields}`)
- `DELETE /v0/{base_id}/{table_id_or_name}/{record_id}` → `delete_record(base_id, table_id_or_name, record_id)`

**Relationships**:
- `tables.baseId` → `bases.id`
- `records_<tid>` belong to `tables.id == <tid>`

**Notes**:
- Pagination: `pageSize` (1–100, default 100) + `offset` cursor string. Response includes `offset` (str) when more records exist.
- `filterByFormula` supports only `{Field}='Value'` equality (regex `_FORMULA_RE`).
- Table lookup tolerates id OR case-insensitive name.
- Field metadata is a process-global dict built from `fields.json` (`_field_meta`, `_field_types`).
- Error envelope: `{error: "<message>"}`, returned as 404.
- Delete returns `{id, deleted: true}` on success.

---

### algolia-api

**Base path**: `/1`

**Entities** (from `_store.register(...)` in `algolia_data.py`):

- **indices** (pk=`name`)
  - Internal fields: `name` (str), `entries` (int via `opt_int`), `dataSize (← data_size)` (int via `opt_int`), `createdAt (← created_at)` (str), `updatedAt (← updated_at)` (str).
  - Wire fields: no `_serialize_` — same as internal (returned inside `{items: [...], nbPages: 1}`).
- **records__<index>** — one mutable table per index (pk=`objectID`, camelCase).
  - Internal fields (from `_coerce_record`): arbitrary user-supplied keys; `in_stock` cast to bool; `price` cast via `_maybe_number` (int/float); other fields kept as strings.
  - Wire fields: no `_serialize_` — same as internal.
- **settings** (pk=`index`, one row per index)
  - Internal fields (from `_coerce_settings`): `index` (str), `searchableAttributes` (list[str]), `attributesForFaceting` (list[str]), `hitsPerPage` (int, default 20), `ranking` (list[str]).
  - Wire fields (via `get_settings`): same as internal but the `index` key is stripped before returning.

**Endpoints**:
- `GET /health` → `{"status": "ok"}`
- `GET /1/indexes` → `list_indexes()`
- `GET /1/indexes/{index}/settings` → `get_settings(index)`
- `POST /1/indexes/{index}/query` → `query_index(index, query, filters, hits_per_page, page)` (Pydantic `QueryBody`)
- `GET /1/indexes/{index}/{object_id}` → `get_object(index, object_id)`
- `POST /1/indexes/{index}` (201) → `add_object(index, body)`
- `PUT /1/indexes/{index}/{object_id}` → `update_object(index, object_id, body)`
- `DELETE /1/indexes/{index}/{object_id}` → `delete_object(index, object_id)`

**Relationships**:
- `records__<index>` belong to `indices.name == <index>`.
- `settings.index` → `indices.name`.

**Notes**:
- Auto-creates an index on first write via `_ensure_index` (dynamic table registration).
- Query response envelope: `{hits, nbHits, page, nbPages, hitsPerPage, query, params}`.
- Filter syntax: `attr:value` or `attr:value AND attr2:value2`.
- `add_object` returns `{objectID, createdAt, taskID}`; `update_object` returns `{objectID, updatedAt, taskID}`; `delete_object` returns `{objectID, deletedAt, taskID}`.
- Error envelope: `{error: "<message>"}` with 404 (or 400 on add).
- "Deliberately tolerant" boolean/number coercion (free-form schemas).

---

### alpaca-api

**Base path**: `/v2`

**Entities** (from `_store.register(...)` in `alpaca_data.py`):

- **positions** (pk=`asset_id`)
  - Internal fields (from `_coerce_positions`): `asset_id`, `symbol`, `qty` (str), `avg_entry_price` (str), `current_price` (str), `side`, `market_value` (str), `cost_basis` (str), `unrealized_pl` (str), `asset_class` (constant `"us_equity"`), `exchange` (constant `"NASDAQ"`).
  - Wire fields: no `_serialize_` — same as internal.
- **orders** (pk=`id`)
  - Internal fields (from `_coerce_orders`): `id` (str), `client_order_id` (str), `symbol` (str), `qty` (str), `filled_qty` (str), `side` (str), `type` (str), `time_in_force` (str), `limit_price` (str|None via `opt_str`), `status` (str), `filled_avg_price` (str|None), `submitted_at` (iso-timestamp str), `filled_at` (str|None).
  - Wire fields: no `_serialize_` — same as internal.
- **assets** (pk=`id`)
  - Internal fields (from `_coerce_assets`): `id` (str), `symbol` (str), `name` (str), `exchange` (str), `class (← asset_class)` (str), `tradable` (bool via `strict_bool`), `fractionable` (bool via `strict_bool`), `status` (constant `"active"`).
  - Wire fields: no `_serialize_` — same as internal.
- **quotes** (singleton document, via `_store.register_document`)
  - Shape (from `_coerce_quotes`): `{<SYMBOL>: {t (← timestamp), bp (← bid_price float), bs (← bid_size int), ap (← ask_price float), as (← ask_size int)}}`.
- **account** (singleton document) — loaded verbatim from `account.json`; includes at least `buying_power` (str) consumed by `create_order`.

**Endpoints**:
- `GET /health`
- `GET /v2/account` → `get_account()`
- `GET /v2/positions` → `list_positions()`
- `GET /v2/positions/{symbol}` → `get_position(symbol)`
- `GET /v2/orders` → `list_orders(status)` (status values: `all`, `open`, `closed`, or exact status)
- `GET /v2/orders/{order_id}` → `get_order(order_id)`
- `POST /v2/orders` (201) → `create_order(symbol, qty, side, type, time_in_force, limit_price)` (Pydantic `OrderCreateBody`)
- `DELETE /v2/orders/{order_id}` → `cancel_order(order_id)`
- `GET /v2/assets` → `list_assets(status, asset_class)`
- `GET /v2/stocks/{symbol}/quotes/latest` → `get_latest_quote(symbol)`

**Relationships**:
- `positions.symbol` ↔ `assets.symbol`
- `orders.symbol` ↔ `assets.symbol`

**Notes**:
- Numeric fields returned as **strings** to match Alpaca conventions (e.g. `qty:"40"`).
- Lists are returned as bare JSON arrays (no envelope).
- Error envelope: `{error, code}` where `code` is a 5-digit Alpaca-style status (e.g. `40410000`, `40310000`, `42210000`). HTTP status derived from prefix: 404 → 404, 403 → 403, else 422.
- `create_order` validates buying power for `buy`, position size for `sell`, asset existence + tradability, positive qty.
- `cancel_order` only succeeds when status not in `{filled, canceled, expired}`.
- Quote response: `{symbol, quote: {t, bp, bs, ap, as}}`.

---

### amadeus-api

**Base path**: varies — `/v2/shopping/...` and `/v1/...`

**Entities** (from `_store.register(...)` in `amadeus_data.py`):

- **airports** (pk=`iata_code`)
  - Internal fields (from `_coerce_airports`): raw JSON columns (via `_strip_ctx`) plus coerced `latitude` (float via `strict_float`), `longitude` (float via `strict_float`). Raw columns: `iata_code`, `name`, `city_name`, `city_code`, `country_name`, `country_code`, `timezone`, (+ lat/long).
  - Wire fields (via `_location_view`): `type:"location"`, `subType:"AIRPORT"`, `id` (`A<iata>`), `name`, `iataCode (← iata_code)`, `address: {cityName, cityCode, countryName, countryCode}`, `geoCode: {latitude, longitude}`, `timeZone: {offset (← timezone)}`. For CITY view: `subType:"CITY"`, `id=C<city_code>`, `name=city_name`.
- **airlines** (pk=`iata_code`)
  - Internal fields (from `_coerce_airlines`): raw JSON columns (`iata_code`, `icao_code`, `business_name`, `common_name`).
  - Wire fields (via `get_airlines`): `type:"airline"`, `iataCode (← iata_code)`, `icaoCode (← icao_code)`, `businessName (← business_name)`, `commonName (← common_name)`.
- **offers** (singleton document) — loaded verbatim from `flight_offers.json`; each offer has `id`, `originLocationCode`, `destinationLocationCode`, `departureDate`, `price: {base, total, currency}`, plus standard Amadeus flight-offer fields.

**Endpoints**:
- `GET /health`
- `GET /v2/shopping/flight-offers` → `search_flight_offers(origin, destination, departure_date, adults, max_results)`
- `POST /v1/shopping/flight-offers/pricing` → `price_flight_offer(offer)` (Pydantic `PricingBody{data: {type, flightOffers: [{}]}}`)
- `GET /v1/reference-data/locations` → `search_locations(keyword, sub_type)`
- `GET /v1/reference-data/locations/{location_id}` → `get_location(location_id)`
- `GET /v1/reference-data/airlines` → `get_airlines(airline_codes)` (comma-separated)

**Relationships**:
- `offers.originLocationCode` / `offers.destinationLocationCode` ↔ `airports.iata_code`

**Notes**:
- Flight offers response: `{meta: {count}, data: [...], dictionaries: {carriers, locations}}`. Multi-adult pricing scales base/fees per `adults` count.
- Locations response: `{meta: {count}, data: [_location_view(...)]}`; `subType` may be `AIRPORT`, `CITY`, or both (comma-separated).
- Location ID lookup: prefix `A` → IATA code; prefix `C` → city code.
- Error envelope: `{error: "<message>"}`; HTTP 404 on lookups, 400 on pricing/invalid payload.

---

### amazon-seller-api

**Base path**: varies — Selling Partner API style routes (e.g. `/sellers/...`, `/orders/...`, `/listings/...`, `/inventory/...`, `/catalog/...`, `/reports/...`, `/products/pricing/...`).

**Entities** (from `_store.register(...)` in `amazon_seller_data.py`):

- **catalog_items** (pk=`sku`)
  - Internal fields (from `_coerce_catalog_items`): raw JSON columns plus coerced `price` (float via `strict_float`), `quantity` (int via `strict_int`), `itemWeight` (float|None via `opt_float`), `itemLength` (float|None), `itemWidth` (float|None), `itemHeight` (float|None), `bulletPoints` (list[str] via `opt_csv_list` sep="|"). Raw fields include `sku`, `asin`, `sellerId`, `title`, `description`, `brand`, `currency`, `fulfillmentChannel`, `status`, `condition`, `productType`, `itemWeightUnit`, `itemDimensionsUnit`, `mainImageUrl`, `category`, `createdDate`, `lastUpdatedDate`.
  - Wire fields (via `_format_catalog_item` / `get_listing_item`): nested SP-API shape — `asin`, `attributes: {item_name, brand, bullet_point, list_price, item_weight, item_dimensions, condition_type, product_type}` (each value an array of `{value, marketplace_id}`), `images`, `salesRanks`, `summaries`. Listing-item shape adds `sku`, `sellerId`, `productType`, `status`, `fulfillmentChannel`, `createdDate`, `lastUpdatedDate`, `issues`.
- **orders** (pk=`AmazonOrderId`)
  - Internal fields (from `_coerce_orders`): raw JSON columns plus coerced `OrderTotal_Amount` (float), `NumberOfItemsShipped` (int), `NumberOfItemsUnshipped` (int), `IsPrime`/`IsBusinessOrder`/`IsSoldByAB` (bool via `.lower()=="true"`). Includes `PurchaseDate`, `LastUpdateDate`, `OrderStatus`, `FulfillmentChannel`, `SalesChannel`, `ShipServiceLevel`, `OrderTotal_CurrencyCode`, `PaymentMethod`, `MarketplaceId`, `ShipmentServiceLevelCategory`, `OrderType`, `EarliestShipDate`, `LatestShipDate`, `ShippingAddress_*` (Name, AddressLine1, City, StateOrRegion, PostalCode, CountryCode), `BuyerEmail`, `BuyerName`.
  - Wire fields (via `_format_order`): re-nested SP-API shape — `OrderTotal: {CurrencyCode, Amount (str)}`, `ShippingAddress: {Name, AddressLine1, City, StateOrRegion, PostalCode, CountryCode}`, `BuyerInfo: {BuyerEmail, BuyerName}`; other fields flat.
- **order_items** (pk=`OrderItemId`)
  - Internal fields (from `_coerce_order_items`): raw JSON + coerced `QuantityOrdered`/`QuantityShipped` (int), `ItemPrice_Amount`/`ItemTax_Amount`/`PromotionDiscount_Amount` (float), `IsGift` (bool). Plus `AmazonOrderId`, `ASIN`, `SellerSKU`, `Title`, `ItemPrice_CurrencyCode`, `Condition`.
  - Wire fields (via `get_order_items`): `{OrderItemId, ASIN, SellerSKU, Title, QuantityOrdered, QuantityShipped, ItemPrice: {CurrencyCode, Amount (str)}, ItemTax: {...}, PromotionDiscount: {...}, IsGift, ConditionId (← Condition)}`.
- **inventory** (pk=`fnSku`)
  - Internal fields (from `_coerce_inventory`): raw + coerced ints for `totalQuantity`, `inStockSupplyQuantity`, `inboundWorkingQuantity`, `inboundShippedQuantity`, `inboundReceivingQuantity`, `reservedQuantity`, `unfulfillableQuantity`. Plus `asin`, `sellerSku`, `productName`, `condition`, `lastUpdatedTime`.
  - Wire fields (via `get_inventory_summaries`): `{asin, fnSku, sellerSku, productName, condition, granularity: {granularityType, granularityId}, inventoryDetails: {fulfillableQuantity (← inStockSupplyQuantity), inboundWorkingQuantity, inboundShippedQuantity, inboundReceivingQuantity, totalQuantity, reservedQuantity, unfulfillableQuantity}, lastUpdatedTime}`.
- **returns** (pk=`returnId`)
  - Internal fields (from `_coerce_returns`): raw + coerced `returnQuantity` (int), `refundAmount` (float).
  - Wire fields: returned in formatted shape; no separate `_serialize_` helper.
- **reports** (pk=`reportId`)
  - Internal fields (from `_coerce_reports`): raw + `processingEndTime` (str|None via `opt_str`), `reportDocumentId` (str|None). Plus `reportType`, `reportStatus`, `dataStartTime`, `dataEndTime`, `createdTime`.
  - Wire fields (via `get_reports`/`get_report`): `{reportId, reportType, processingStatus (← reportStatus), dataStartTime, dataEndTime, createdTime, processingEndTime, reportDocumentId}`.
- **pricing** (pk=`asin`)
  - Internal fields (from `_coerce_pricing`): raw + coerced `competitivePrice_Amount`, `listingPrice_Amount`, `landedPrice_Amount`, `shipping_Amount`, `buyBoxPrice_Amount` (float), `numberOfOffers` (int), `buyBoxWinner` (bool). Plus `sellerSku`, `competitivePrice_CurrencyCode`, `competitivePrice_Condition`, `buyBoxPrice_CurrencyCode`.
  - Wire fields (via `get_competitive_pricing`): deeply nested SP-API `Product.CompetitivePricing.CompetitivePrices`, `Product.Offers.BuyBoxPrices`, `NumberOfOfferListings`.
- **seller_account** (singleton document) — JSON object including `accountHealth`, `performanceNotifications`, etc.
- **buying_notes** (singleton document, file `buying_notes_fw26.json`).

**Endpoints**: (resolved from `server.py`; SP-API style)
- `GET /health`
- `GET /sellers/v1/account` → `get_seller_account()`
- `GET /sellers/v1/account/buying-notes` → `get_buying_notes()`
- `GET /sellers/v1/account/health` → `get_account_health()`
- `GET /sellers/v1/account/notifications` → `get_performance_notifications(severity)`
- `GET /catalog/2022-04-01/items` → `search_catalog_items(keywords, identifiers, identifiers_type, page_size, status)`
- `GET /catalog/2022-04-01/items/{asin}` → `get_catalog_item(asin)`
- `GET /listings/2021-08-01/items/{seller_id}/{sku}` → `get_listing_item(seller_id, sku)`
- `PUT /listings/2021-08-01/items/{seller_id}/{sku}` → `create_listing_item(seller_id, sku, data)`
- `PATCH /listings/2021-08-01/items/{seller_id}/{sku}` → `update_listing_item(seller_id, sku, data)`
- `DELETE /listings/2021-08-01/items/{seller_id}/{sku}` → `delete_listing_item(seller_id, sku)`
- `GET /orders/v0/orders` → `get_orders(created_after, created_before, order_statuses, fulfillment_channels, max_results)`
- `GET /orders/v0/orders/{order_id}` → `get_order(order_id)`
- `GET /orders/v0/orders/{order_id}/orderItems` → `get_order_items(order_id)`
- `POST /orders/v0/orders/{order_id}/shipmentConfirmation` → `confirm_shipment(order_id, data)`
- `GET /fba/inventory/v1/summaries` → `get_inventory_summaries(seller_skus, granularity_type, marketplace_id)`
- `PUT /fba/inventory/v1/summaries/{seller_sku}` → `update_inventory(seller_sku, quantity)`
- `GET /reports/2021-06-30/reports` → `get_reports(report_types, processing_statuses)`
- `GET /reports/2021-06-30/reports/{report_id}` → `get_report(report_id)`
- `POST /reports/2021-06-30/reports` → `create_report(report_type, data_start_time, data_end_time)`
- `GET /products/pricing/v0/competitivePrice` → `get_competitive_pricing(asin, sku)`
- Additional returns endpoints listed in `server.py` for `/returns/...`.

**Relationships**:
- `order_items.AmazonOrderId` → `orders.AmazonOrderId`
- `catalog_items.sku` ↔ `inventory.sellerSku`
- `catalog_items.asin` ↔ `pricing.asin`
- `returns.AmazonOrderId` → `orders.AmazonOrderId`

### amplitude-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `amplitude_data.py`):

- **events** (pk=`event_id`)
  - Internal fields (from `_coerce_events`): `event_id`, `user_id`, `device_id`, `event_type`, `event_time`, `event_properties`
- **users** (pk=`user_id`)
  - Internal fields (from `_coerce_users`): `user_id`, `device_id`, `country`, `platform`, `version`, `first_seen`, `last_seen`
- **segmentation** (pk=`event_type`)
  - Internal fields (from `_coerce_segmentation`): `event_type`, `date`, `count`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /2/httpapi` → `httpapi(body)`
- `GET /api/2/events/segmentation` → `segmentation(e, start, end)`
- `GET /api/2/useractivity` → `user_activity(user)`

**Additional data-layer functions** (referenced indirectly): `ingest(payload)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### asana-api

**Base path**: `/api/1.0`

**Entities** (from `_store.register(...)` in `asana_data.py`):

- **users** (pk=`gid`)
  - Internal fields (from `_coerce_users`): passthrough — no field rewrites (returns rows unchanged).
- **projects** (pk=`gid`)
  - Internal fields (from `_coerce_projects`): `…raw row…`, `archived`
- **sections** (pk=`gid`)
  - Internal fields (from `_coerce_sections`): passthrough — no field rewrites (returns rows unchanged).
- **tasks** (pk=`gid`)
  - Internal fields (from `_coerce_tasks`): `…raw row…`, `completed`, `assignee_gid`, `due_on`, `section_gid`
  - Wire fields (from `_task_view`): `gid`, `resource_type`, `name`, `completed`, `due_on`, `notes`, `created_at`, `modified_at`, `assignee`, `memberships`, `project`, `section`
- **workspace** (singleton, via `_store.register_document`)
  - Wire fields (from `_workspace_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/1.0/workspaces` → `list_workspaces()`
- `GET /api/1.0/users` → `list_users(workspace)`
- `GET /api/1.0/projects` → `list_projects(workspace, archived)`
- `GET /api/1.0/projects/{project_gid}` → `get_project(project_gid)`
- `GET /api/1.0/projects/{project_gid}/sections` → `list_project_sections(project_gid)`
- `GET /api/1.0/projects/{project_gid}/tasks` → `list_project_tasks(project_gid, completed_since)`
- `GET /api/1.0/tasks` → `list_tasks(project, assignee, completed)`
- `POST /api/1.0/tasks` → `create_task(body)`
- `GET /api/1.0/tasks/{task_gid}` → `get_task(task_gid)`
- `PUT /api/1.0/tasks/{task_gid}` → `update_task(task_gid, body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### bamboohr-api

**Base path**: `/api/gateway.php`

**Entities** (from `_store.register(...)` in `bamboohr_data.py`):

- **employees** (pk=`id`)
  - Internal fields (from `_coerce_employees`): `supervisorId`
- **time_off** (pk=`id`)
  - Internal fields (from `_coerce_time_off`): `amount`
- **whos_out** (pk=`id`)
  - Internal fields (from `_coerce_whos_out`): passthrough — no field rewrites (returns rows unchanged).
- **company** (singleton, via `_store.register_document`)
  - Wire fields (from `_company_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/gateway.php/{company}/v1/company` → `get_company(company)`
- `GET /api/gateway.php/{company}/v1/employees/directory` → `employees_directory(company)`
- `GET /api/gateway.php/{company}/v1/employees/{employee_id}` → `get_employee(company, employee_id)`
- `POST /api/gateway.php/{company}/v1/employees` → `create_employee(company, body)`
- `GET /api/gateway.php/{company}/v1/time_off/requests` → `list_time_off_requests(company, status, employeeId)`
- `POST /api/gateway.php/{company}/v1/time_off/requests` → `create_time_off_request(company, body)`
- `PUT /api/gateway.php/{company}/v1/time_off/requests/{request_id}/status` → `update_time_off_status(company, request_id, body)`
- `GET /api/gateway.php/{company}/v1/time_off/whos_out` → `whos_out(company, start, end)`
- `GET /api/gateway.php/{company}/v1/reports/{report_id}` → `get_report(company, report_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### bigcommerce-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `bigcommerce_data.py`):

- **products** (pk=`id`)
  - Internal fields (from `_coerce_products`): `id`, `name`, `sku`, `type`, `price`, `sale_price`, `cost_price`, `weight`, `inventory_level`, `inventory_tracking`, `is_visible`, `brand_id`, `categories`, `description`, `date_created`
  - Wire fields (from `_serialize_product`): same as internal
- **customers** (pk=`id`)
  - Internal fields (from `_coerce_customers`): `id`, `first_name`, `last_name`, `email`, `company`, `phone`, `customer_group_id`, `date_created`
  - Wire fields (from `_serialize_customer`): same as internal
- **orders** (pk=`id`)
  - Internal fields (from `_coerce_orders`): `id`, `customer_id`, `status_id`, `status`, `total_inc_tax`, `subtotal_inc_tax`, `currency_code`, `payment_method`, `items_total`, `date_created`, `billing_first_name`, `billing_last_name`, `billing_email`
  - Wire fields (from `_serialize_order`): `id`, `customer_id`, `status_id`, `status`, `total_inc_tax`, `subtotal_inc_tax`, `currency_code`, `payment_method`, `items_total`, `date_created`, `billing_address`, `first_name`, `last_name`, `email`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v3/catalog/products` → `list_products(name, sku, is_visible, page, limit)`
- `GET /v3/catalog/products/{product_id}` → `get_product(product_id)`
- `GET /v2/orders` → `list_orders(customer_id, status_id, page, limit)`
- `GET /v2/orders/{order_id}` → `get_order(order_id)`
- `POST /v2/orders` → `create_order(body)`
- `GET /v3/customers` → `list_customers(email, company, page, limit)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### binance-api

**Base path**: `/api/v3`

**Entities** (from `_store.register(...)` in `binance_data.py`):

- **prices** (pk=`symbol`)
  - Internal fields (from `_coerce_prices`): `symbol`, `price`, `priceChange`, `priceChangePercent`, `highPrice`, `lowPrice`, `volume`
- **klines** (pk=`symbol`)
  - Internal fields (from `_coerce_klines`): `symbol`, `interval`, `open_time`, `open`, `high`, `low`, `close`, `volume`, `close_time`
- **balances** (pk=`asset`)
  - Internal fields (from `_coerce_balances`): `asset`, `free`, `locked`
- **depth** (pk=`symbol`)
  - Internal fields (from `_coerce_depth`): `symbol`, `side`, `price`, `qty`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v3/ticker/price` → `ticker_price(symbol)`
- `GET /api/v3/ticker/24hr` → `ticker_24hr(symbol)`
- `GET /api/v3/depth` → `depth(symbol, limit)`
- `GET /api/v3/klines` → `klines(symbol, interval, limit)`
- `GET /api/v3/account` → `account()`

**Additional data-layer functions** (referenced indirectly): `get_ticker_price(symbol)`, `get_ticker_24hr(symbol)`, `get_depth(symbol, limit)`, `get_klines(symbol, interval, limit)`, `get_account()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### box-api

**Base path**: `/2.0`

**Entities** (from `_store.register(...)` in `box_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `id`, `name`, `login`, `role`, `status`, `language`, `timezone`, `space_amount`, `space_used`, `max_upload_size`, `job_title`, `phone`, `created_at`
  - Wire fields (from `_serialize_user`): `type`, `id`, `name`, `login`, `role`, `status`, `language`, `timezone`, `space_amount`, `space_used`, `max_upload_size`, `job_title`, `phone`, `created_at`
- **folders** (pk=`id`)
  - Internal fields (from `_coerce_folders`): `id`, `name`, `parent_id`, `owner_id`, `description`, `created_at`, `modified_at`, `item_count`
  - Wire fields (from `_serialize_folder`): `type`, `id`, `name`, `description`, `size`, `created_at`, `modified_at`, `item_count`, `parent`, `owned_by`
- **files** (pk=`id`)
  - Internal fields (from `_coerce_files`): `id`, `name`, `parent_id`, `owner_id`, `description`, `size`, `extension`, `sha1`, `created_at`, `modified_at`
  - Wire fields (from `_serialize_file`): `type`, `id`, `name`, `description`, `size`, `extension`, `sha1`, `created_at`, `modified_at`, `parent`, `owned_by`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /2.0/users/me` → `get_me()`
- `GET /2.0/folders/{folder_id}` → `get_folder(folder_id)`
- `GET /2.0/folders/{folder_id}/items` → `get_folder_items(folder_id, limit, offset)`
- `GET /2.0/files/{file_id}` → `get_file(file_id)`
- `GET /2.0/files/{file_id}/content` → `download_file(file_id)`
- `GET /2.0/search` → `search(query, type, limit, offset)`

**Additional data-layer functions** (referenced indirectly): `download_file_content(file_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### calendly-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `calendly_data.py`):

- **event_types** (pk=`uuid`)
  - Internal fields (from `_coerce_event_types`): `…raw row…`, `duration`, `active`
  - Wire fields (from `_event_type_obj`): `uri`, `name`, `slug`, `duration`, `kind`, `color`, `active`, `description_plain`, `scheduling_url`, `profile`, `created_at`, `owner`
- **scheduled_events** (pk=`uuid`)
  - Internal fields (from `_coerce_scheduled_events`): `…raw row…`, `canceled_reason`
  - Wire fields (from `_event_obj`): `uri`, `name`, `status`, `start_time`, `end_time`, `event_type`, `location`, `event_memberships`, `cancellation`, `created_at`, `type`, `user`, `reason`, `canceler_type`
- **invitees** (pk=`uuid`)
  - Internal fields (from `_coerce_invitees`): `…raw row…`, `questions_and_answers`
  - Wire fields (from `_invitee_obj`): `uri`, `name`, `email`, `status`, `timezone`, `event`, `questions_and_answers`, `created_at`
- **availability** (pk=`owner`)
  - Internal fields (from `_coerce_availability`): `…raw row pass-through…`
- **user** (singleton, via `_store.register_document`)
  - Wire fields (from `_user_obj`): `uri`, `name`, `slug`, `email`, `scheduling_url`, `timezone`, `current_organization`, `created_at`, `updated_at`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /users/me` → `get_me()`
- `GET /event_types` → `list_event_types(user)`
- `GET /event_types/{uuid}` → `get_event_type(uuid)`
- `GET /scheduled_events` → `list_scheduled_events(user, status)`
- `GET /scheduled_events/{uuid}` → `get_scheduled_event(uuid)`
- `GET /scheduled_events/{uuid}/invitees` → `list_invitees(uuid)`
- `POST /scheduled_events` → `book_event(body)`
- `POST /scheduled_events/{uuid}/cancellation` → `cancel_event(uuid, body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### cloudflare-api

**Base path**: `/client/v4/zones`

**Entities** (from `_store.register(...)` in `cloudflare_data.py`):

- **zones** (pk=`id`)
  - Internal fields (from `_coerce_zones`): `…raw row…`, `paused`, `development_mode`
  - Wire fields (from `_serialize_zone`): `id`, `name`, `status`, `paused`, `type`, `development_mode`, `plan`, `created_on`, `modified_on`
- **dns** (pk=`id`)
  - Internal fields (from `_coerce_dns`): `…raw row…`, `ttl`, `proxied`, `priority`
  - Wire fields (from `_serialize_dns`): `id`, `zone_id`, `type`, `name`, `content`, `ttl`, `proxied`, `priority`, `created_on`, `modified_on`
- **firewall** (pk=`id`)
  - Internal fields (from `_coerce_firewall`): `…raw row…`, `paused`, `priority`
  - Wire fields (from `_serialize_firewall`): `id`, `description`, `action`, `filter`, `paused`, `priority`, `created_on`, `expression`
- **page_rules** (pk=`id`)
  - Internal fields (from `_coerce_page_rules`): `…raw row…`, `priority`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /client/v4/zones` → `list_zones(name, status)`
- `GET /client/v4/zones/{zone_id}` → `get_zone(zone_id)`
- `GET /client/v4/zones/{zone_id}/dns_records` → `list_dns_records(zone_id, type, name)`
- `GET /client/v4/zones/{zone_id}/dns_records/{record_id}` → `get_dns_record(zone_id, record_id)`
- `POST /client/v4/zones/{zone_id}/dns_records` → `create_dns_record(zone_id, body)`
- `PUT /client/v4/zones/{zone_id}/dns_records/{record_id}` → `update_dns_record(zone_id, record_id, body)`
- `DELETE /client/v4/zones/{zone_id}/dns_records/{record_id}` → `delete_dns_record(zone_id, record_id)`
- `GET /client/v4/zones/{zone_id}/firewall/rules` → `list_firewall_rules(zone_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### coinbase-api

**Base path**: `/v2`

**Entities** (from `_store.register(...)` in `coinbase_data.py`):

- **accounts** (pk=`id`)
  - Internal fields (from `_coerce_accounts`): `id`, `name`, `primary`, `type`, `currency`, `balance`, `native_balance`, `created_at`, `updated_at`, `_balance_num`, `_native_num`, `code`, `amount`
  - Wire fields (from `_public_account`): passthrough — same as internal.
- **prices** (pk=`pair`)
  - Internal fields (from `_coerce_prices`): `pair`, `base`, `currency`, `amount`, `_amount_num`
- **transactions** (pk=`id`)
  - Internal fields (from `_coerce_transactions`): `id`, `account_id`, `type`, `status`, `amount`, `native_amount`, `description`, `created_at`, `updated_at`, `currency`
- **user** (singleton, via `_store.register_document`)
  - Wire fields (from `_user_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v2/user` → `get_user()`
- `GET /v2/accounts` → `list_accounts()`
- `GET /v2/accounts/{account_id}` → `get_account(account_id)`
- `GET /v2/prices/{pair}/spot` → `get_spot_price(pair)`
- `POST /v2/accounts/{account_id}/buys` → `create_buy(account_id, body)`
- `POST /v2/accounts/{account_id}/sells` → `create_sell(account_id, body)`
- `GET /v2/accounts/{account_id}/transactions` → `list_transactions(account_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### confluence-api

**Base path**: `/wiki/rest/api`

**Entities** (from `_store.register(...)` in `confluence_data.py`):

- **spaces** (pk=`id`)
  - Internal fields (from `_coerce_spaces`): `id`, `key`, `name`, `type`, `status`, `description`, `plain`, `value`, `representation`
- **pages** (pk=`id`)
  - Internal fields (from `_coerce_pages`): `id`, `type`, `status`, `title`, `space_key`, `parent_id`, `version`, `body`, `created_by`, `created_at`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `id`, `page_id`, `author`, `body`, `created_at`
- **labels** (pk=`id`)
  - Internal fields (from `_coerce_labels`): `id`, `page_id`, `name`, `prefix`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /wiki/rest/api/space` → `list_spaces(limit)`
- `GET /wiki/rest/api/space/{space_key}` → `get_space(space_key)`
- `GET /wiki/rest/api/content/search` → `search_content(cql)`
- `GET /wiki/rest/api/content` → `list_content(type, spaceKey, limit)`
- `POST /wiki/rest/api/content` → `create_content(body)`
- `GET /wiki/rest/api/content/{content_id}` → `get_content(content_id)`
- `PUT /wiki/rest/api/content/{content_id}` → `update_content(content_id, body)`
- `GET /wiki/rest/api/content/{content_id}/child/page` → `list_child_pages(content_id, limit)`
- `GET /wiki/rest/api/content/{content_id}/label` → `list_labels(content_id)`
- `GET /wiki/rest/api/content/{content_id}/child/comment` → `list_comments(content_id)`

**Additional data-layer functions** (referenced indirectly): `search(cql)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### contentful-api

**Base path**: `/spaces`

**Entities** (from `_store.register(...)` in `contentful_data.py`):

- **content_types** (pk=`id`)
  - Internal fields (from `_coerce_content_types`): `id`, `name`, `displayField`, `description`, `fields`
  - Wire fields (from `_content_type_obj`): `sys`, `name`, `displayField`, `description`, `fields`, `id`, `type`
- **entries** (pk=`id`)
  - Internal fields (from `_coerce_entries`): `id`, `content_type`, `created_at`, `updated_at`, `published_version`, `fields`
  - Wire fields (from `_entry_obj`): `sys`, `fields`, `id`, `type`, `createdAt`, `updatedAt`, `publishedVersion`, `contentType`, `linkType`
- **assets** (pk=`id`)
  - Internal fields (from `_coerce_assets`): `id`, `created_at`, `updated_at`, `published_version`, `title`, `description`, `file_url`, `content_type`, `file_name`, `size`
  - Wire fields (from `_asset_obj`): `sys`, `fields`, `id`, `type`, `createdAt`, `updatedAt`, `publishedVersion`, `title`, `description`, `file`, `url`, `fileName`, `contentType`, `details`, `size`
- **space** (singleton, via `_store.register_document`)
  - Wire fields (from `_space_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /spaces/{space_id}` → `get_space(space_id)`
- `GET /spaces/{space_id}/environments/{env_id}/content_types` → `list_content_types(space_id, env_id)`
- `GET /spaces/{space_id}/environments/{env_id}/content_types/{content_type_id}` → `get_content_type(space_id, env_id, content_type_id)`
- `GET /spaces/{space_id}/environments/{env_id}/entries` → `list_entries(space_id, env_id, content_type, limit, skip)`
- `GET /spaces/{space_id}/environments/{env_id}/entries/{entry_id}` → `get_entry(space_id, env_id, entry_id)`
- `POST /spaces/{space_id}/environments/{env_id}/entries` → `create_entry(space_id, env_id, body)`
- `PUT /spaces/{space_id}/environments/{env_id}/entries/{entry_id}` → `update_entry(space_id, env_id, entry_id, body)`
- `DELETE /spaces/{space_id}/environments/{env_id}/entries/{entry_id}` → `delete_entry(space_id, env_id, entry_id)`
- `GET /spaces/{space_id}/environments/{env_id}/assets` → `list_assets(space_id, env_id)`
- `GET /spaces/{space_id}/environments/{env_id}/assets/{asset_id}` → `get_asset(space_id, env_id, asset_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### datadog-api

**Base path**: `/api/v1`

**Entities** (from `_store.register(...)` in `datadog_data.py`):

- **monitors** (pk=`id`)
  - Internal fields (from `_coerce_monitors`): `…raw row…`, `id`, `priority`, `tags`
- **dashboards** (pk=`id`)
  - Internal fields (from `_coerce_dashboards`): `…raw row…`, `widget_count`, `is_read_only`
- **events** (pk=`id`)
  - Internal fields (from `_coerce_events`): `…raw row…`, `id`, `tags`, `date_happened`
- **hosts** (pk=`name`)
  - Internal fields (from `_coerce_hosts`): `…raw row…`, `up`, `apps`, `cpu_pct`, `mem_pct`, `last_reported`
- **metrics** (pk=`metric`)
  - Internal fields (from `_coerce_metrics`): `…raw row…`, `base_value`, `amplitude`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v1/query` → `query_metrics(query, from_, to)`
- `GET /api/v1/monitor` → `list_monitors(overall_state)`
- `GET /api/v1/monitor/{monitor_id}` → `get_monitor(monitor_id)`
- `POST /api/v1/monitor` → `create_monitor(body)`
- `PUT /api/v1/monitor/{monitor_id}` → `update_monitor(monitor_id, body)`
- `GET /api/v1/dashboard` → `list_dashboards()`
- `GET /api/v1/dashboard/{dashboard_id}` → `get_dashboard(dashboard_id)`
- `GET /api/v1/events` → `list_events(start, end)`
- `POST /api/v1/events` → `create_event(body)`
- `GET /api/v1/hosts` → `list_hosts()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### discord-api

**Base path**: `/api/v10`

**Entities** (from `_store.register(...)` in `discord_data.py`):

- **guilds** (pk=`id`)
  - Internal fields (from `_coerce_guilds`): `id`, `name`, `owner_id`, `approximate_member_count`, `description`, `icon`, `region`
- **channels** (pk=`id`)
  - Internal fields (from `_coerce_channels`): `id`, `guild_id`, `name`, `type`, `position`, `topic`, `nsfw`
- **messages** (pk=`id`)
  - Internal fields (from `_coerce_messages`): `id`, `channel_id`, `author`, `content`, `timestamp`, `pinned`, `edited_timestamp`, `username`
- **members** (pk=`guild_id`)
  - Internal fields (from `_coerce_members`): `guild_id`, `user`, `nick`, `joined_at`, `roles`, `id`, `username`, `global_name`, `bot`
- **roles** (pk=`id`)
  - Internal fields (from `_coerce_roles`): `id`, `guild_id`, `name`, `color`, `position`, `hoist`, `mentionable`, `permissions`
- **me** (singleton, via `_store.register_document`)
  - Wire fields (from `_me_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v10/users/@me` → `get_me()`
- `GET /api/v10/users/@me/guilds` → `list_my_guilds()`
- `GET /api/v10/guilds/{guild_id}` → `get_guild(guild_id)`
- `GET /api/v10/guilds/{guild_id}/channels` → `list_guild_channels(guild_id)`
- `GET /api/v10/guilds/{guild_id}/members` → `list_guild_members(guild_id, limit)`
- `GET /api/v10/guilds/{guild_id}/roles` → `list_guild_roles(guild_id)`
- `GET /api/v10/channels/{channel_id}` → `get_channel(channel_id)`
- `GET /api/v10/channels/{channel_id}/messages` → `list_channel_messages(channel_id, limit)`
- `POST /api/v10/channels/{channel_id}/messages` → `create_message(channel_id, body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### docusign-api

**Base path**: `/restapi/v2.1/accounts`

**Entities** (from `_store.register(...)` in `docusign_data.py`):

- **envelopes** (pk=`envelope_id`)
  - Internal fields (from `_coerce_envelopes`): `…raw row…`, `sent_time`, `completed_time`, `template_id`
  - Wire fields (from `_envelope_obj`): `envelopeId`, `status`, `emailSubject`, `sender`, `createdDateTime`, `sentDateTime`, `completedDateTime`, `templateId`, `userName`, `email`
- **recipients** (pk=`recipient_id`)
  - Internal fields (from `_coerce_recipients`): `…raw row…`, `routing_order`, `signed_time`
  - Wire fields (from `_recipient_obj`): `recipientId`, `name`, `email`, `recipientType`, `status`, `routingOrder`, `signedDateTime`
- **documents** (pk=`document_id`)
  - Internal fields (from `_coerce_documents`): `…raw row…`, `page_count`, `order`
  - Wire fields (from `_document_obj`): `documentId`, `name`, `type`, `pages`, `order`
- **templates** (pk=`template_id`)
  - Internal fields (from `_coerce_templates`): `…raw row…`, `shared`
  - Wire fields (from `_template_obj`): `templateId`, `name`, `description`, `shared`, `owner`, `created`, `userName`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /restapi/v2.1/accounts/{accountId}/envelopes` → `list_envelopes(accountId, status)`
- `POST /restapi/v2.1/accounts/{accountId}/envelopes` → `create_envelope(accountId, body)`
- `GET /restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}` → `get_envelope(accountId, envelopeId)`
- `PUT /restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}` → `update_envelope(accountId, envelopeId, body)`
- `GET /restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}/recipients` → `list_recipients(accountId, envelopeId)`
- `GET /restapi/v2.1/accounts/{accountId}/envelopes/{envelopeId}/documents` → `list_documents(accountId, envelopeId)`
- `GET /restapi/v2.1/accounts/{accountId}/templates` → `list_templates(accountId)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### doordash-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `doordash_data.py`):

- **stores** (pk=`store_id`)
  - Internal fields (from `_coerce_stores`): `…raw row…`, `rating`, `review_count`, `delivery_fee`, `eta_minutes`, `latitude`, `longitude`, `is_open`
- **menu_items** (pk=`item_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **orders** (pk=`order_id`)
  - Internal fields (from `_coerce_orders`): `…raw row…`, `subtotal`, `delivery_fee`, `service_fee`, `tip`, `total`
- **order_items** (pk=`order_id`)
  - Internal fields (from `_coerce_order_items`): `…raw row…`, `quantity`, `unit_price`, `line_total`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/stores` → `list_stores(latitude, longitude, cuisine, open_only)`
- `GET /v1/stores/{store_id}` → `get_store(store_id)`
- `GET /v1/stores/{store_id}/menu` → `get_menu(store_id)`
- `POST /v1/carts` → `create_cart(body)`
- `GET /v1/carts/{cart_id}` → `get_cart(cart_id)`
- `POST /v1/carts/{cart_id}/items` → `add_cart_item(cart_id, body)`
- `POST /v1/carts/{cart_id}/checkout` → `checkout(cart_id, body)`
- `GET /v1/orders/{order_id}` → `get_order(order_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### dropbox-api

**Base path**: `/2`

**Entities** (from `_store.register(...)` in `dropbox_data.py`):

- **account** (singleton, via `_store.register_document`)
  - Internal fields (from `_coerce_account`): `account_id`, `name`, `email`, `email_verified`, `country`, `locale`, `account_type`, `is_paired`, `disabled`, `given_name`, `surname`, `display_name`, `familiar_name`, `abbreviated_name`, `.tag`
  - Wire fields (from `_account_doc`): passthrough — same as internal.
- **files** (pk=`id`)
  - Internal fields (from `_coerce_files`): `id`, `name`, `path_lower`, `path_display`, `is_folder`, `size`, `client_modified`, `rev`
- **shared_links** (pk=`id`)
  - Internal fields (from `_coerce_shared_links`): `id`, `url`, `name`, `path_lower`, `visibility`, `file_id`
  - Wire fields (from `_serialize_link`): `.tag`, `id`, `url`, `name`, `path_lower`, `link_permissions`, `resolved_visibility`, `can_revoke`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /2/users/get_current_account` → `get_current_account()`
- `POST /2/files/list_folder` → `list_folder(body)`
- `POST /2/files/get_metadata` → `get_metadata(body)`
- `POST /2/files/download` → `download_file(body)`
- `POST /2/files/search_v2` → `search_v2(body)`
- `POST /2/sharing/list_shared_links` → `list_shared_links(body)`

**Additional data-layer functions** (referenced indirectly): `download_file_content(path)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 2 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### etsy-api

**Base path**: `/v3/application`

**Entities** (from `_store.register(...)` in `etsy_data.py`):

- **listings** (pk=`listing_id`)
  - Internal fields (from `_coerce_listings`): `…raw row…`, `listing_id`, `shop_id`, `price`, `quantity`, `taxonomy_id`, `tags`, `materials`, `shop_section_id`, `processing_min`, `processing_max`, `item_weight`, `item_length`, `item_width`, `item_height`, `views`, `num_favorers`, `shipping_profile_id`, `return_policy_id`, `is_supply`, `is_customizable`, `is_personalizable`
- **listing_images** (pk=`listing_image_id`)
  - Internal fields (from `_coerce_listing_images`): `…raw row…`, `listing_image_id`, `listing_id`, `shop_id`, `rank`
- **receipts** (pk=`receipt_id`)
  - Internal fields (from `_coerce_receipts`): `…raw row…`, `receipt_id`, `shop_id`, `buyer_user_id`, `grandtotal`, `subtotal`, `total_shipping_cost`, `total_tax_cost`, `discount_amt`, `is_gift`, `gift_message`, `shipped_timestamp`, `estimated_delivery`, `shipping_carrier`, `tracking_code`
- **transactions** (pk=`transaction_id`)
  - Internal fields (from `_coerce_transactions`): `…raw row…`, `transaction_id`, `receipt_id`, `listing_id`, `shop_id`, `buyer_user_id`, `quantity`, `price`, `shipping_cost`, `is_digital`
- **reviews** (pk=`review_id`)
  - Internal fields (from `_coerce_reviews`): `…raw row…`, `review_id`, `shop_id`, `listing_id`, `buyer_user_id`, `rating`, `image_url`
- **shop_sections** (pk=`shop_section_id`)
  - Internal fields (from `_coerce_shop_sections`): `…raw row…`, `shop_section_id`, `shop_id`, `rank`, `active_listing_count`
- **shipping_profiles** (pk=`shipping_profile_id`)
  - Internal fields (from `_coerce_shipping_profiles`): `…raw row…`, `shipping_profile_id`, `shop_id`, `processing_min`, `processing_max`, `min_delivery_days`, `max_delivery_days`, `cost`, `secondary_cost`
- **return_policies** (pk=`return_policy_id`)
  - Internal fields (from `_coerce_return_policies`): `…raw row…`, `return_policy_id`, `shop_id`, `accepts_returns`, `accepts_exchanges`, `return_deadline`
- **shop** (singleton, via `_store.register_document`)
  - Wire fields (from `_shop_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v3/application/users/me` → `get_current_user()`
- `GET /v3/application/shops/{shop_id}` → `get_shop(shop_id)`
- `PUT /v3/application/shops/{shop_id}` → `update_shop(shop_id, body)`
- `GET /v3/application/shops/{shop_id}/sections` → `list_shop_sections(shop_id)`
- `GET /v3/application/shops/{shop_id}/sections/{section_id}` → `get_shop_section(shop_id, section_id)`
- `GET /v3/application/shops/{shop_id}/listings` → `list_listings(shop_id, state, sort_on, sort_order, limit, offset, section_id, q)`
- `GET /v3/application/listings/{listing_id}` → `get_listing(listing_id)`
- `POST /v3/application/shops/{shop_id}/listings` → `create_listing(shop_id, body)`
- `PUT /v3/application/listings/{listing_id}` → `update_listing(listing_id, body)`
- `DELETE /v3/application/listings/{listing_id}` → `delete_listing(listing_id)`
- `GET /v3/application/listings/{listing_id}/images` → `list_listing_images(listing_id)`
- `GET /v3/application/listings/{listing_id}/images/{image_id}` → `get_listing_image(listing_id, image_id)`
- `DELETE /v3/application/listings/{listing_id}/images/{image_id}` → `delete_listing_image(listing_id, image_id)`
- `GET /v3/application/shops/{shop_id}/receipts` → `list_receipts(shop_id, status, min_created, max_created, sort_on, sort_order, limit, offset, was_shipped, was_paid)`
- `GET /v3/application/shops/{shop_id}/receipts/{receipt_id}` → `get_receipt(shop_id, receipt_id)`
- `PUT /v3/application/shops/{shop_id}/receipts/{receipt_id}` → `update_receipt(shop_id, receipt_id, body)`
- `GET /v3/application/shops/{shop_id}/receipts/{receipt_id}/transactions` → `list_receipt_transactions(shop_id, receipt_id)`
- `GET /v3/application/shops/{shop_id}/transactions/{transaction_id}` → `get_transaction(shop_id, transaction_id)`
- `GET /v3/application/shops/{shop_id}/reviews` → `list_shop_reviews(shop_id, listing_id, min_rating, limit, offset)`
- `GET /v3/application/listings/{listing_id}/reviews` → `list_listing_reviews(listing_id, min_rating, limit, offset)`
- `GET /v3/application/shops/{shop_id}/shipping-profiles` → `list_shipping_profiles(shop_id)`
- `GET /v3/application/shops/{shop_id}/shipping-profiles/{profile_id}` → `get_shipping_profile(shop_id, profile_id)`
- `GET /v3/application/shops/{shop_id}/return-policies` → `list_return_policies(shop_id)`
- `GET /v3/application/shops/{shop_id}/return-policies/{policy_id}` → `get_return_policy(shop_id, policy_id)`

**Additional data-layer functions** (referenced indirectly): `list_reviews(shop_id, listing_id, min_rating, limit, offset)`, `get_review(review_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 8 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### eventbrite-api

**Base path**: `/v3`

**Entities** (from `_store.register(...)` in `eventbrite_data.py`):

- **events** (pk=`id`)
  - Internal fields (from `_coerce_events`): `…raw row…`, `capacity`, `is_free`, `online_event`
  - Wire fields (from `_serialize_event`): `…raw row…`, `name`, `summary`, `start`, `end`, `venue`, `text`, `html`, `timezone`, `utc`
- **venues** (pk=`id`)
  - Internal fields (from `_coerce_venues`): `…raw row…`, `latitude`, `longitude`
- **ticket_classes** (pk=`id`)
  - Internal fields (from `_coerce_ticket_classes`): `…raw row…`, `quantity_total`, `quantity_sold`, `cost`, `fee`, `free`
- **attendees** (pk=`id`)
  - Internal fields (from `_coerce_attendees`): `…raw row…`, `checked_in`
- **organizations** (pk=`id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v3/users/me/organizations` → `list_organizations()`
- `GET /v3/organizations/{org_id}` → `get_organization(org_id)`
- `GET /v3/organizations/{org_id}/events` → `list_org_events(org_id, status, q, page_size)`
- `GET /v3/events/search` → `search_events(q, status, page_size)`
- `GET /v3/events/{event_id}` → `get_event(event_id)`
- `POST /v3/events` → `create_event(body)`
- `POST /v3/events/{event_id}/publish` → `publish_event(event_id)`
- `POST /v3/events/{event_id}/cancel` → `cancel_event(event_id)`
- `GET /v3/venues` → `list_venues()`
- `GET /v3/venues/{venue_id}` → `get_venue(venue_id)`
- `GET /v3/events/{event_id}/ticket_classes` → `list_ticket_classes(event_id)`
- `POST /v3/events/{event_id}/ticket_classes` → `create_ticket_class(event_id, body)`
- `GET /v3/events/{event_id}/attendees` → `list_attendees(event_id, status, checked_in)`
- `POST /v3/events/{event_id}/attendees` → `register_attendee(event_id, body)`
- `POST /v3/attendees/{attendee_id}/check_in` → `check_in_attendee(attendee_id)`

**Additional data-layer functions** (referenced indirectly): `list_events(organization_id, status, q, page_size)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### fedex-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `fedex_data.py`):

- **rates** (pk=`service_type`)
  - Internal fields (from `_coerce_rates`): `service_type`, `service_name`, `origin_zip`, `dest_zip`, `weight_lb`, `currency`, `net_charge`, `transit_days`, `delivery_day`
- **shipments** (pk=`tracking_number`)
  - Internal fields (from `_coerce_shipments`): `tracking_number`, `service_type`, `service_name`, `ship_date`, `origin_zip`, `dest_zip`, `weight_lb`, `currency`, `net_charge`, `label_url`
- **tracking** (pk=`tracking_number`)
  - Internal fields (from `_coerce_tracking`): `tracking_number`, `status_code`, `status_description`, `carrier_code`, `service_name`, `ship_date`, `estimated_delivery`, `latest_event`, `latest_event_location`, `latest_event_time`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /rate/v1/rates/quotes` → `rate_quotes(origin_zip, dest_zip, weight_lb, service_type)`
- `POST /ship/v1/shipments` → `create_shipment(origin_zip, dest_zip, weight_lb, service_type)`
- `POST /track/v1/trackingnumbers` → `track(tracking_number)`

**Additional data-layer functions** (referenced indirectly): `get_rate_quote(origin_zip, dest_zip, weight_lb, service_type)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### figma-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `figma_data.py`):

- **projects** (pk=`project_id`)
  - Internal fields (from `_coerce_projects`): passthrough — no field rewrites (returns rows unchanged).
- **files** (pk=`file_key`)
  - Internal fields (from `_coerce_files`): passthrough — no field rewrites (returns rows unchanged).
- **components** (pk=`component_key`)
  - Internal fields (from `_coerce_components`): passthrough — no field rewrites (returns rows unchanged).
- **comments** (pk=`comment_id`)
  - Internal fields (from `_coerce_comments`): `…raw row…`, `resolved`
  - Wire fields (from `_comment_view`): `id`, `file_key`, `message`, `client_meta`, `user`, `resolved_at`, `created_at`, `handle`, `node_id`, `img_url`
- **team** (singleton, via `_store.register_document`)
  - Wire fields (from `_team_doc`): passthrough — same as internal.
- **file_nodes** (singleton, via `_store.register_document`)
  - Wire fields (from `_file_nodes_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/me` → `get_me()`
- `GET /v1/teams/{team_id}/projects` → `team_projects(team_id)`
- `GET /v1/projects/{project_id}/files` → `project_files(project_id)`
- `GET /v1/files/{file_key}` → `get_file(file_key)`
- `GET /v1/files/{file_key}/nodes` → `get_file_nodes(file_key, ids)`
- `GET /v1/files/{file_key}/comments` → `get_comments(file_key)`
- `POST /v1/files/{file_key}/comments` → `create_comment(file_key, body)`
- `GET /v1/files/{file_key}/components` → `get_components(file_key)`

**Additional data-layer functions** (referenced indirectly): `get_team_projects(team_id)`, `get_project_files(project_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 2 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### freshdesk-api

**Base path**: `/api/v2`

**Entities** (from `_store.register(...)` in `freshdesk_data.py`):

- **tickets** (pk=`id`)
  - Internal fields (from `_coerce_tickets`): `id`, `subject`, `description`, `status`, `priority`, `requester_id`, `responder_id`, `type`, `tags`, `created_at`, `updated_at`
- **contacts** (pk=`id`)
  - Internal fields (from `_coerce_contacts`): `id`, `name`, `email`, `phone`, `company_id`, `active`, `created_at`
- **agents** (pk=`id`)
  - Internal fields (from `_coerce_agents`): `id`, `available`, `ticket_scope`, `occasional`, `created_at`, `contact`, `name`, `email`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v2/tickets` → `list_tickets(status, priority, requester_id)`
- `GET /api/v2/tickets/{ticket_id}` → `get_ticket(ticket_id)`
- `POST /api/v2/tickets` → `create_ticket(body)`
- `PUT /api/v2/tickets/{ticket_id}` → `update_ticket(ticket_id, body)`
- `GET /api/v2/contacts` → `list_contacts()`
- `GET /api/v2/agents` → `list_agents()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### github-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `github_data.py`):

- **repos** (pk=`id`)
  - Internal fields (from `_coerce_repos`): `…raw row…`, `id`, `private`, `stars`, `forks`, `open_issues`
  - Wire fields (from `_serialize_repo`): `id`, `name`, `full_name`, `owner`, `private`, `description`, `default_branch`, `language`, `stargazers_count`, `forks_count`, `open_issues_count`, `created_at`, `updated_at`, `login`
- **issues** (pk=`id`)
  - Internal fields (from `_coerce_issues`): `…raw row…`, `id`, `number`, `is_pull_request`, `labels`, `closed_at`, `milestone`
  - Wire fields (from `_serialize_issue`): `id`, `number`, `title`, `body`, `state`, `user`, `assignee`, `labels`, `milestone`, `created_at`, `updated_at`, `closed_at`, `pull_request`, `login`, `name`, `url`
- **pulls** (pk=`number`)
  - Internal fields (from `_coerce_pulls`): `…raw row…`, `number`, `merged`, `mergeable`, `draft`, `additions`, `deletions`, `changed_files`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `…raw row…`, `id`, `issue_number`
- **user** (singleton, via `_store.register_document`)
  - Wire fields (from `_user_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /user` → `get_user()`
- `GET /users/{owner}/repos` → `list_user_repos(owner)`
- `GET /orgs/{owner}/repos` → `list_org_repos(owner)`
- `GET /repos/{owner}/{repo}` → `get_repo(owner, repo)`
- `GET /repos/{owner}/{repo}/issues` → `list_issues(owner, repo, state, labels, assignee, per_page)`
- `GET /repos/{owner}/{repo}/issues/{number}` → `get_issue(owner, repo, number)`
- `POST /repos/{owner}/{repo}/issues` → `create_issue(owner, repo, body)`
- `PATCH /repos/{owner}/{repo}/issues/{number}` → `update_issue(owner, repo, number, body)`
- `GET /repos/{owner}/{repo}/pulls` → `list_pulls(owner, repo, state)`
- `GET /repos/{owner}/{repo}/pulls/{number}` → `get_pull(owner, repo, number)`
- `PUT /repos/{owner}/{repo}/pulls/{number}/merge` → `merge_pull(owner, repo, number)`
- `GET /repos/{owner}/{repo}/issues/{number}/comments` → `list_comments(owner, repo, number)`
- `POST /repos/{owner}/{repo}/issues/{number}/comments` → `create_comment(owner, repo, number, body)`

**Additional data-layer functions** (referenced indirectly): `list_repos(owner)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### gitlab-api

**Base path**: `/api/v4`

**Entities** (from `_store.register(...)` in `gitlab_data.py`):

- **projects** (pk=`id`)
  - Internal fields (from `_coerce_projects`): `…raw row…`, `id`, `star_count`, `forks_count`, `open_issues_count`
- **issues** (pk=`id`)
  - Internal fields (from `_coerce_issues`): `…raw row…`, `id`, `iid`, `project_id`, `labels`, `closed_at`
- **merge_requests** (pk=`id`)
  - Internal fields (from `_coerce_merge_requests`): `…raw row…`, `id`, `iid`, `project_id`, `draft`, `merged_at`
- **pipelines** (pk=`id`)
  - Internal fields (from `_coerce_pipelines`): `…raw row…`, `id`, `project_id`, `duration`
- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `id`, `is_admin`
- **current_user** (singleton, via `_store.register_document`)
  - Wire fields (from `_current_user_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v4/user` → `get_user()`
- `GET /api/v4/projects` → `list_projects(visibility)`
- `GET /api/v4/projects/{project_id}` → `get_project(project_id)`
- `GET /api/v4/projects/{project_id}/issues` → `list_issues(project_id, state, labels)`
- `GET /api/v4/projects/{project_id}/issues/{issue_iid}` → `get_issue(project_id, issue_iid)`
- `POST /api/v4/projects/{project_id}/issues` → `create_issue(project_id, body)`
- `PUT /api/v4/projects/{project_id}/issues/{issue_iid}` → `update_issue(project_id, issue_iid, body)`
- `GET /api/v4/projects/{project_id}/merge_requests` → `list_merge_requests(project_id, state)`
- `POST /api/v4/projects/{project_id}/merge_requests` → `create_merge_request(project_id, body)`
- `PUT /api/v4/projects/{project_id}/merge_requests/{mr_iid}/merge` → `merge_merge_request(project_id, mr_iid)`
- `GET /api/v4/projects/{project_id}/pipelines` → `list_pipelines(project_id, status)`

**Additional data-layer functions** (referenced indirectly): `get_current_user()`, `list_users()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### gmail-api

**Base path**: `/gmail/v1/users/me`

**Entities** (from `_store.register(...)` in `gmail_data.py`):

- **labels** (pk=`id`)
  - Internal fields (from `_coerce_labels`): `…raw row…`, `messagesTotal`, `messagesUnread`, `threadsTotal`, `threadsUnread`
- **messages** (pk=`id`)
  - Internal fields (from `_coerce_messages`): `…raw row…`, `body`, `internal_date`, `size_estimate`, `labels`, `is_unread`, `is_starred`
  - Wire fields (from `_serialize_message`): `id`, `threadId`, `labelIds`, `snippet`, `internalDate`, `sizeEstimate`, `payload`, `headers`, `body`, `mimeType`, `data`, `size`, `name`, `value`
- **drafts** (pk=`id`)
  - Internal fields (from `_coerce_drafts`): `…raw row…`, `body`
- **profile** (singleton, via `_store.register_document`)
  - Wire fields (from `_profile_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /gmail/v1/users/me/profile` → `get_profile()`
- `GET /gmail/v1/users/me/labels` → `list_labels()`
- `GET /gmail/v1/users/me/labels/{label_id}` → `get_label(label_id)`
- `POST /gmail/v1/users/me/labels` → `create_label(body)`
- `GET /gmail/v1/users/me/messages` → `list_messages(q, maxResults, labelIds)`
- `GET /gmail/v1/users/me/messages/{message_id}` → `get_message(message_id, format)`
- `POST /gmail/v1/users/me/messages/send` → `send_message(body)`
- `POST /gmail/v1/users/me/messages/{message_id}/modify` → `modify_message(message_id, body)`
- `POST /gmail/v1/users/me/messages/{message_id}/trash` → `trash_message(message_id)`
- `DELETE /gmail/v1/users/me/messages/{message_id}` → `delete_message(message_id)`
- `GET /gmail/v1/users/me/threads` → `list_threads(q)`
- `GET /gmail/v1/users/me/threads/{thread_id}` → `get_thread(thread_id)`
- `GET /gmail/v1/users/me/drafts` → `list_drafts()`
- `GET /gmail/v1/users/me/drafts/{draft_id}` → `get_draft(draft_id)`
- `POST /gmail/v1/users/me/drafts` → `create_draft(body)`
- `POST /gmail/v1/users/me/drafts/{draft_id}/send` → `send_draft(draft_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### google-analytics-api

**Base path**: `/v1beta/properties`

**Entities**: (no `_store.register(...)` calls detected — inspect data module manually)

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /v1beta/properties/{property_id}:runReport` → `run_report(property_id, body)`
- `POST /v1beta/properties/{property_id}:runRealtimeReport` → `run_realtime_report(property_id, body)`
- `POST /v1beta/properties/{property_id}:batchRunReports` → `batch_run_reports(property_id, body)`
- `GET /v1beta/properties/{property_id}/metadata` → `get_metadata(property_id)`
- `GET /v1beta/properties/{property_id}` → `get_property(property_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### google-calendar-api

**Base path**: `/calendar/v3`

**Entities**: (no `_store.register(...)` calls detected — inspect data module manually)

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /calendar/v3/users/me/calendarList` → `list_calendars()`
- `GET /calendar/v3/calendars/{calendar_id}` → `get_calendar(calendar_id)`
- `GET /calendar/v3/calendars/{calendar_id}/events` → `list_events(calendar_id, timeMin, timeMax, q, singleEvents, orderBy, maxResults, pageToken)`
- `GET /calendar/v3/calendars/{calendar_id}/events/{event_id}` → `get_event(calendar_id, event_id)`
- `POST /calendar/v3/calendars/{calendar_id}/events` → `create_event(calendar_id, body)`
- `PATCH /calendar/v3/calendars/{calendar_id}/events/{event_id}` → `update_event(calendar_id, event_id, body)`
- `DELETE /calendar/v3/calendars/{calendar_id}/events/{event_id}` → `delete_event(calendar_id, event_id)`
- `POST /calendar/v3/freeBusy` → `freebusy(body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### google-classroom-api

**Base path**: `/v1/courses`

**Entities**: (no `_store.register(...)` calls detected — inspect data module manually)

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/courses` → `list_courses(courseStates, pageSize, pageToken)`
- `GET /v1/courses/{course_id}` → `get_course(course_id)`
- `POST /v1/courses` → `create_course(body)`
- `PATCH /v1/courses/{course_id}` → `update_course(course_id, body)`
- `POST /v1/courses/{course_id}:archive` → `archive_course(course_id)`
- `GET /v1/courses/{course_id}/courseWork` → `list_coursework(course_id, topicId, courseWorkStates, orderBy, pageSize, pageToken)`
- `GET /v1/courses/{course_id}/courseWork/{coursework_id}` → `get_coursework(course_id, coursework_id)`
- `POST /v1/courses/{course_id}/courseWork` → `create_coursework(course_id, body)`
- `PATCH /v1/courses/{course_id}/courseWork/{coursework_id}` → `update_coursework(course_id, coursework_id, body)`
- `DELETE /v1/courses/{course_id}/courseWork/{coursework_id}` → `delete_coursework(course_id, coursework_id)`
- `GET /v1/courses/{course_id}/topics` → `list_topics(course_id, pageSize, pageToken)`
- `GET /v1/courses/{course_id}/topics/{topic_id}` → `get_topic(course_id, topic_id)`
- `POST /v1/courses/{course_id}/topics` → `create_topic(course_id, body)`
- `PATCH /v1/courses/{course_id}/topics/{topic_id}` → `update_topic(course_id, topic_id, body)`
- `DELETE /v1/courses/{course_id}/topics/{topic_id}` → `delete_topic(course_id, topic_id)`
- `GET /v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions` → `list_submissions(course_id, coursework_id, states, late, pageSize, pageToken)`
- `GET /v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions/{submission_id}` → `get_submission(course_id, coursework_id, submission_id)`
- `PATCH /v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions/{submission_id}` → `grade_submission(course_id, coursework_id, submission_id, body)`
- `POST /v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions/{submission_id}:return` → `return_submission(course_id, coursework_id, submission_id)`
- `POST /v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions/{submission_id}:reclaim` → `reclaim_submission(course_id, coursework_id, submission_id)`
- `POST /v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions/{submission_id}:turnIn` → `turn_in_submission(course_id, coursework_id, submission_id)`
- `POST /v1/courses/{course_id}/courseWork/{coursework_id}/studentSubmissions/{submission_id}:modifyAttachments` → `modify_submission_attachments(course_id, coursework_id, submission_id, body)`
- `GET /v1/courses/{course_id}/students` → `list_students(course_id, pageSize, pageToken)`
- `GET /v1/courses/{course_id}/students/{user_id}` → `get_student(course_id, user_id)`
- `POST /v1/courses/{course_id}/students` → `invite_student(course_id, body)`
- `DELETE /v1/courses/{course_id}/students/{user_id}` → `remove_student(course_id, user_id)`
- `GET /v1/courses/{course_id}/teachers` → `list_teachers(course_id)`
- `GET /v1/courses/{course_id}/teachers/{user_id}` → `get_teacher(course_id, user_id)`
- `GET /v1/courses/{course_id}/announcements` → `list_announcements(course_id, announcementStates, pageSize, pageToken)`
- `GET /v1/courses/{course_id}/announcements/{announcement_id}` → `get_announcement(course_id, announcement_id)`
- `POST /v1/courses/{course_id}/announcements` → `create_announcement(course_id, body)`
- `PATCH /v1/courses/{course_id}/announcements/{announcement_id}` → `update_announcement(course_id, announcement_id, body)`
- `DELETE /v1/courses/{course_id}/announcements/{announcement_id}` → `delete_announcement(course_id, announcement_id)`
- `GET /v1/courses/{course_id}/courseWorkMaterials` → `list_materials(course_id, pageSize, pageToken)`
- `GET /v1/courses/{course_id}/courseWorkMaterials/{material_id}` → `get_material(course_id, material_id)`
- `POST /v1/courses/{course_id}/courseWorkMaterials` → `create_material(course_id, body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### google-drive-api

**Base path**: `/drive/v3`

**Entities**: (no `_store.register(...)` calls detected — inspect data module manually)

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /drive/v3/about` → `get_about()`
- `GET /drive/v3/files` → `list_files(q, pageSize, pageToken, orderBy)`
- `GET /drive/v3/files/{file_id}` → `get_file(file_id, alt)`
- `POST /drive/v3/files` → `create_file(body)`
- `PATCH /drive/v3/files/{file_id}` → `update_file(file_id, body)`
- `POST /drive/v3/files/{file_id}/trash` → `trash_file(file_id)`
- `DELETE /drive/v3/files/{file_id}` → `delete_file(file_id)`
- `GET /drive/v3/files/{file_id}/permissions` → `list_permissions(file_id)`
- `POST /drive/v3/files/{file_id}/permissions` → `create_permission(file_id, body)`
- `DELETE /drive/v3/files/{file_id}/permissions/{permission_id}` → `delete_permission(file_id, permission_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### google-maps-api

**Base path**: `/maps/api`

**Entities**: (no `_store.register(...)` calls detected — inspect data module manually)

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /maps/api/place/textsearch/json` → `text_search(query)`
- `GET /maps/api/place/details/json` → `place_details(place_id)`
- `GET /maps/api/place/nearbysearch/json` → `nearby_search(location, radius, type)`
- `GET /maps/api/geocode/json` → `geocode(address)`
- `GET /maps/api/directions/json` → `directions(origin, destination, mode)`
- `GET /maps/api/distancematrix/json` → `distance_matrix(origins, destinations, mode)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### greenhouse-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `greenhouse_data.py`):

- **candidates** (pk=`id`)
  - Internal fields (from `_coerce_candidates`): passthrough — no field rewrites (returns rows unchanged).
- **jobs** (pk=`id`)
  - Internal fields (from `_coerce_jobs`): `closed_at`
- **applications** (pk=`id`)
  - Internal fields (from `_coerce_applications`): passthrough — no field rewrites (returns rows unchanged).
- **scorecards** (pk=`id`)
  - Internal fields (from `_coerce_scorecards`): `rating`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/candidates` → `list_candidates()`
- `GET /v1/candidates/{candidate_id}` → `get_candidate(candidate_id)`
- `POST /v1/candidates` → `create_candidate(body)`
- `GET /v1/jobs` → `list_jobs(status)`
- `GET /v1/jobs/{job_id}` → `get_job(job_id)`
- `GET /v1/applications` → `list_applications(job_id, candidate_id, status)`
- `GET /v1/applications/{application_id}` → `get_application(application_id)`
- `POST /v1/applications/{application_id}/advance` → `advance_application(application_id)`
- `POST /v1/applications/{application_id}/reject` → `reject_application(application_id, body)`
- `GET /v1/scorecards` → `list_scorecards(application_id, candidate_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### gusto-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `gusto_data.py`):

- **employees** (pk=`id`)
  - Internal fields (from `_coerce_employees`): `rate`, `terminated`
- **compensations** (pk=`id`)
  - Internal fields (from `_coerce_compensations`): `rate`
- **payrolls** (pk=`id`)
  - Internal fields (from `_coerce_payrolls`): `processed`, `gross_pay`, `net_pay`, `employee_count`
- **contractors** (pk=`id`)
  - Internal fields (from `_coerce_contractors`): `hourly_rate`
- **company** (singleton, via `_store.register_document`)
  - Wire fields (from `_company_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/companies/{company_id}` → `get_company(company_id)`
- `GET /v1/companies/{company_id}/employees` → `list_company_employees(company_id)`
- `GET /v1/employees/{employee_id}` → `get_employee(employee_id)`
- `GET /v1/companies/{company_id}/payrolls` → `list_company_payrolls(company_id, processed)`
- `GET /v1/payrolls/{payroll_id}` → `get_payroll(payroll_id)`
- `POST /v1/companies/{company_id}/payrolls` → `create_payroll(company_id, body)`
- `PUT /v1/payrolls/{payroll_id}/submit` → `submit_payroll(payroll_id)`
- `GET /v1/companies/{company_id}/contractors` → `list_company_contractors(company_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### hubspot-api

**Base path**: `/crm/v3`

**Entities** (from `_store.register(...)` in `hubspot_data.py`):

- **pipelines** (pk=`id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /crm/v3/objects/contacts` → `list_contacts(limit, after)`
- `GET /crm/v3/objects/contacts/{contact_id}` → `get_contact(contact_id)`
- `POST /crm/v3/objects/contacts` → `create_contact(body)`
- `PATCH /crm/v3/objects/contacts/{contact_id}` → `update_contact(contact_id, body)`
- `GET /crm/v3/objects/companies` → `list_companies(limit, after)`
- `GET /crm/v3/objects/companies/{company_id}` → `get_company(company_id)`
- `GET /crm/v3/objects/deals` → `list_deals(limit, after)`
- `GET /crm/v3/objects/deals/{deal_id}` → `get_deal(deal_id)`
- `POST /crm/v3/objects/deals` → `create_deal(body)`
- `PATCH /crm/v3/objects/deals/{deal_id}` → `update_deal(deal_id, body)`
- `GET /crm/v3/pipelines/deals` → `list_deal_pipelines()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 1 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### instacart-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `instacart_data.py`):

- **retailers** (pk=`retailer_id`)
  - Internal fields (from `_coerce_retailers`): `…raw row…`, `min_basket`, `delivery_fee`, `service_fee_pct`, `eta_minutes`, `delivers_to_zips`
- **products** (pk=`product_id`)
  - Internal fields (from `_coerce_products`): `…raw row…`, `price`, `sale_price`, `in_stock`
- **orders** (pk=`order_id`)
  - Internal fields (from `_coerce_orders`): `…raw row…`, `subtotal`, `delivery_fee`, `service_fee`, `tip`, `total`
- **order_items** (pk=`order_id`)
  - Internal fields (from `_coerce_order_items`): `…raw row…`, `quantity`, `unit_price`, `line_total`, `replacement_for`
- **user** (singleton, via `_store.register_document`)
  - Wire fields (from `_user_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/users/me` → `get_user()`
- `GET /v1/retailers` → `list_retailers(zip_code)`
- `GET /v1/retailers/{retailer_id}` → `get_retailer(retailer_id)`
- `GET /v1/products` → `search_products(retailer_id, q, category, in_stock_only, limit, offset)`
- `GET /v1/products/{product_id}` → `get_product(product_id)`
- `POST /v1/carts` → `create_cart(body)`
- `GET /v1/carts/{cart_id}` → `get_cart(cart_id)`
- `POST /v1/carts/{cart_id}/items` → `add_to_cart(cart_id, body)`
- `PATCH /v1/carts/{cart_id}/items/{product_id}` → `update_cart_item(cart_id, product_id, body)`
- `POST /v1/carts/{cart_id}/checkout` → `checkout(cart_id, body)`
- `GET /v1/orders` → `list_orders(user_id, status)`
- `GET /v1/orders/{order_id}` → `get_order(order_id)`
- `POST /v1/orders/{order_id}/cancel` → `cancel_order(order_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### instagram-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `instagram_data.py`):

- **media** (pk=`id`)
  - Internal fields (from `_coerce_media`): `id`, `user_id`, `caption`, `media_type`, `media_url`, `permalink`, `thumbnail_url`, `timestamp`, `like_count`, `comments_count`, `is_comment_enabled`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `id`, `media_id`, `user_id`, `username`, `text`, `timestamp`, `like_count`, `hidden`, `parent_id`
- **stories** (pk=`id`)
  - Internal fields (from `_coerce_stories`): `id`, `user_id`, `media_type`, `media_url`, `timestamp`, `expiring_at`, `caption`, `link`, `poll_question`, `poll_options`
- **media_insights** (pk=`media_id`)
  - Internal fields (from `_coerce_media_insights`): `media_id`, `impressions`, `reach`, `engagement`, `saves`, `shares`, `profile_visits`, `follows`
- **carousel_children** (pk=`id`)
  - Internal fields (from `_coerce_carousel_children`): `id`, `media_id`, `media_type`, `media_url`, `timestamp`
- **hashtags** (pk=`id`)
  - Internal fields (from `_coerce_hashtags`): `id`, `name`, `media_count`
- **mentions** (pk=`id`)
  - Internal fields (from `_coerce_mentions`): `id`, `media_id`, `mentioned_by_user_id`, `mentioned_by_username`, `media_url`, `timestamp`, `caption`
- **users** (pk=`id`)
  - Wire fields (from `_users_dict`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /ig_hashtag_search` → `search_hashtags(q)`
- `GET /hashtag/{hashtag_id}` → `get_hashtag(hashtag_id, fields)`
- `GET /hashtag/{hashtag_id}/recent_media` → `get_hashtag_recent_media(hashtag_id, user_id, fields, limit)`
- `GET /media/{media_id}/children` → `get_media_children(media_id, fields)`
- `GET /media/{media_id}/comments` → `list_media_comments(media_id, fields, limit, offset)`
- `GET /media/{media_id}/insights` → `get_media_insights(media_id, metric)`
- `POST /media/{media_id}/comments` → `create_comment(media_id, body)`
- `DELETE /media/{media_id}/comments/{comment_id}` → `delete_comment(media_id, comment_id)`
- `PUT /media/{media_id}/comments/{comment_id}/hide` → `hide_comment(media_id, comment_id, body)`
- `GET /media/{media_id}` → `get_media(media_id, fields)`
- `DELETE /media/{media_id}` → `delete_media(media_id)`
- `GET /comment/{comment_id}/replies` → `get_comment_replies(comment_id, fields, limit, offset)`
- `GET /comment/{comment_id}` → `get_comment(comment_id, fields)`
- `GET /stories/{story_id}` → `get_story(story_id, fields)`
- `GET /container/{container_id}` → `get_container_status(container_id)`
- `GET /ig_user_search` → `search_users(q)`
- `GET /{user_id}/media` → `list_user_media(user_id, media_type, fields, limit, offset)`
- `GET /{user_id}/stories` → `list_user_stories(user_id, fields)`
- `GET /{user_id}/insights` → `get_user_insights(user_id, metric, period)`
- `GET /{user_id}/tags` → `list_user_mentions(user_id, fields, limit, offset)`
- `POST /{user_id}/media` → `create_media_container(user_id, body)`
- `POST /{user_id}/media_publish` → `publish_media_container(user_id, body)`
- `PUT /{user_id}` → `update_user(user_id, body)`
- `GET /{user_id}` → `get_user(user_id, fields)`

**Additional data-layer functions** (referenced indirectly): `get_media_container_status(container_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 8 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### intercom-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `intercom_data.py`):

- **contacts** (pk=`id`)
  - Internal fields (from `_coerce_contacts`): `id`, `role`, `name`, `email`, `phone`, `company_id`, `created_at`, `last_seen_at`
- **companies** (pk=`id`)
  - Internal fields (from `_coerce_companies`): `id`, `company_id`, `name`, `plan`, `monthly_spend`, `user_count`, `industry`, `created_at`
- **conversations** (pk=`id`)
  - Internal fields (from `_coerce_conversations`): `id`, `contact_id`, `state`, `title`, `created_at`, `updated_at`, `assignee_id`, `open`
  - Wire fields (from `_conversation_obj`): `type`, `id`, `state`, `open`, `title`, `created_at`, `updated_at`, `contact_id`, `admin_assignee_id`, `conversation_parts`, `total_count`
- **parts** (pk=`id`)
  - Internal fields (from `_coerce_parts`): `id`, `conversation_id`, `part_type`, `author_type`, `author_id`, `body`, `created_at`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /contacts` → `list_contacts(role)`
- `POST /contacts` → `create_contact(body)`
- `GET /contacts/{contact_id}` → `get_contact(contact_id)`
- `GET /conversations` → `list_conversations(state)`
- `POST /conversations` → `create_conversation(body)`
- `GET /conversations/{conversation_id}` → `get_conversation(conversation_id)`
- `POST /conversations/{conversation_id}/reply` → `reply_conversation(conversation_id, body)`
- `POST /conversations/{conversation_id}/parts` → `add_part(conversation_id, body)`
- `GET /companies` → `list_companies()`
- `GET /companies/{company_id}` → `get_company(company_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### jira-api

**Base path**: `/rest`

**Entities** (from `_store.register(...)` in `jira_data.py`):

- **projects** (pk=`id`)
  - Internal fields (from `_coerce_projects`): `…raw row…`, `id`
  - Wire fields (from `_serialize_project`): `id`, `key`, `name`, `projectTypeKey`, `lead`, `description`
- **users** (pk=`account_id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `active`
  - Wire fields (from `_user_obj`): `accountId`, `displayName`, `emailAddress`, `active`
- **boards** (pk=`id`)
  - Internal fields (from `_coerce_boards`): `…raw row…`, `id`
- **sprints** (pk=`id`)
  - Internal fields (from `_coerce_sprints`): `…raw row…`, `id`, `board_id`, `start_date`, `end_date`
- **issues** (pk=`id`)
  - Internal fields (from `_coerce_issues`): `…raw row…`, `id`, `sprint_id`, `story_points`, `assignee`
  - Wire fields (from `_serialize_issue`): `id`, `key`, `fields`, `summary`, `description`, `issuetype`, `project`, `status`, `priority`, `assignee`, `reporter`, `customfield_10016`, `created`, `updated`, `name`, `statusCategory`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /rest/api/3/project` → `list_projects()`
- `POST /rest/api/3/issue` → `create_issue(body)`
- `GET /rest/api/3/issue/{issue_key}` → `get_issue(issue_key)`
- `PUT /rest/api/3/issue/{issue_key}` → `update_issue(issue_key, body)`
- `GET /rest/api/3/issue/{issue_key}/transitions` → `get_transitions(issue_key)`
- `POST /rest/api/3/issue/{issue_key}/transitions` → `transition_issue(issue_key, body)`
- `GET /rest/api/3/search` → `search(jql, maxResults)`
- `GET /rest/agile/1.0/board` → `list_boards()`
- `GET /rest/agile/1.0/board/{board_id}/sprint` → `list_sprints(board_id, state)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### klaviyo-api

**Base path**: `/api`

**Entities** (from `_store.register(...)` in `klaviyo_data.py`):

- **profiles** (pk=`id`)
  - Internal fields (from `_coerce_profiles`): `id`, `email`, `phone_number`, `first_name`, `last_name`, `organization`, `title`, `city`, `region`, `country`, `created`, `updated`
  - Wire fields (from `_serialize_profile`): `type`, `id`, `attributes`, `email`, `phone_number`, `first_name`, `last_name`, `organization`, `title`, `location`, `created`, `updated`, `city`, `region`, `country`
- **lists** (pk=`id`)
  - Internal fields (from `_coerce_lists`): `id`, `name`, `profile_count`, `created`, `updated`
  - Wire fields (from `_serialize_list`): `type`, `id`, `attributes`, `name`, `profile_count`, `created`, `updated`
- **campaigns** (pk=`id`)
  - Internal fields (from `_coerce_campaigns`): `id`, `name`, `status`, `channel`, `subject`, `from_email`, `from_label`, `list_id`, `send_time`, `created`, `updated`
  - Wire fields (from `_serialize_campaign`): `type`, `id`, `attributes`, `relationships`, `name`, `status`, `channel`, `subject`, `from_email`, `from_label`, `send_time`, `created`, `updated`, `list`, `data`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/profiles` → `list_profiles(email)`
- `GET /api/profiles/{profile_id}` → `get_profile(profile_id)`
- `POST /api/profiles` → `create_profile(body)`
- `GET /api/lists` → `list_lists()`
- `GET /api/campaigns` → `list_campaigns(status, channel)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### kraken-api

**Base path**: `/0`

**Entities** (from `_store.register(...)` in `kraken_data.py`):

- **tickers** (pk=`pair`)
  - Internal fields (from `_coerce_tickers`): `pair`, `altname`, `ask`, `bid`, `last`, `volume`, `high`, `low`, `open`
- **ohlc** (pk=`_pk`)
  - Internal fields (from `_coerce_ohlc`): `pair`, `time`, `open`, `high`, `low`, `close`, `vwap`, `volume`, `count`
- **pairs** (pk=`pair`)
  - Internal fields (from `_coerce_pairs`): `pair`, `altname`, `wsname`, `base`, `quote`, `pair_decimals`, `lot_decimals`, `ordermin`, `status`
- **assets** (pk=`asset`)
  - Internal fields (from `_coerce_assets`): `asset`, `altname`, `aclass`, `decimals`, `display_decimals`
- **balances** (pk=`asset`)
  - Internal fields (from `_coerce_balances`): `asset`, `balance`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /0/public/Ticker` → `ticker(pair)`
- `GET /0/public/OHLC` → `ohlc(pair, interval)`
- `GET /0/public/AssetPairs` → `asset_pairs(pair)`
- `GET /0/public/Assets` → `assets(asset)`
- `POST /0/private/Balance` → `balance()`

**Additional data-layer functions** (referenced indirectly): `get_ticker(pair)`, `get_ohlc(pair, interval)`, `get_asset_pairs(pair)`, `get_assets(asset)`, `get_balance()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### kubernetes-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `kubernetes_data.py`):

- **namespaces** (pk=`name`)
  - Internal fields (from `_coerce_namespaces`): `…raw row…`, `labels`
  - Wire fields (from `_ns_obj`): `kind`, `apiVersion`, `metadata`, `status`, `name`, `labels`, `creationTimestamp`, `phase`
- **nodes** (pk=`name`)
  - Internal fields (from `_coerce_nodes`): `…raw row pass-through…`
  - Wire fields (from `_node_obj`): `kind`, `apiVersion`, `metadata`, `status`, `name`, `labels`, `creationTimestamp`, `capacity`, `nodeInfo`, `addresses`, `conditions`, `cpu`, `memory`, `kubeletVersion`, `osImage`, `type`, `address`
- **pods** (pk=`name`)
  - Internal fields (from `_coerce_pods`): `…raw row…`, `restart_count`, `ready`, `node`, `pod_ip`
  - Wire fields (from `_pod_obj`): `kind`, `apiVersion`, `metadata`, `spec`, `status`, `name`, `namespace`, `creationTimestamp`, `nodeName`, `containers`, `phase`, `podIP`, `containerStatuses`, `image`, `ready`, `restartCount`, `state`
- **deployments** (pk=`name`)
  - Internal fields (from `_coerce_deployments`): `…raw row…`, `replicas`, `available_replicas`, `ready_replicas`, `updated_replicas`
  - Wire fields (from `_deployment_obj`): `kind`, `apiVersion`, `metadata`, `spec`, `status`, `name`, `namespace`, `creationTimestamp`, `replicas`, `strategy`, `template`, `availableReplicas`, `readyReplicas`, `updatedReplicas`, `type`, `containers`, `image`
- **services** (pk=`name`)
  - Internal fields (from `_coerce_services`): `…raw row…`, `port`, `target_port`, `external_ip`
  - Wire fields (from `_service_obj`): `kind`, `apiVersion`, `metadata`, `spec`, `status`, `name`, `namespace`, `creationTimestamp`, `type`, `clusterIP`, `selector`, `ports`, `loadBalancer`, `port`, `targetPort`, `protocol`, `ingress`, `ip`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v1/namespaces` → `list_namespaces()`
- `GET /api/v1/namespaces/{ns}/pods` → `list_pods(ns)`
- `GET /api/v1/namespaces/{ns}/pods/{name}` → `get_pod(ns, name)`
- `DELETE /api/v1/namespaces/{ns}/pods/{name}` → `delete_pod(ns, name)`
- `GET /apis/apps/v1/namespaces/{ns}/deployments` → `list_deployments(ns)`
- `GET /apis/apps/v1/namespaces/{ns}/deployments/{name}` → `get_deployment(ns, name)`
- `PATCH /apis/apps/v1/namespaces/{ns}/deployments/{name}/scale` → `scale_deployment(ns, name, body)`
- `GET /api/v1/namespaces/{ns}/services` → `list_services(ns)`
- `GET /api/v1/nodes` → `list_nodes()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### linear-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `linear_data.py`):

- **teams** (pk=`id`)
  - Internal fields (from `_coerce_teams`): `…raw row…`, `id`, `name`, `key`, `description`, `color`, `createdAt`, `updatedAt`
- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `id`, `name`, `displayName`, `email`, `avatarUrl`, `active`, `admin`, `teamId`, `createdAt`, `updatedAt`
- **workflow_states** (pk=`id`)
  - Internal fields (from `_coerce_workflow_states`): `…raw row…`, `id`, `name`, `type`, `color`, `position`, `teamId`, `description`
- **labels** (pk=`id`)
  - Internal fields (from `_coerce_labels`): `…raw row…`, `id`, `name`, `color`, `description`, `teamId`, `createdAt`, `updatedAt`
- **projects** (pk=`id`)
  - Internal fields (from `_coerce_projects`): `…raw row…`, `id`, `name`, `description`, `state`, `leadId`, `teamIds`, `startDate`, `targetDate`, `createdAt`, `updatedAt`
- **cycles** (pk=`id`)
  - Internal fields (from `_coerce_cycles`): `…raw row…`, `id`, `name`, `number`, `teamId`, `startsAt`, `endsAt`, `completedAt`, `createdAt`, `updatedAt`
- **issues** (pk=`id`)
  - Internal fields (from `_coerce_issues`): `…raw row…`, `id`, `identifier`, `number`, `title`, `description`, `priority`, `estimate`, `stateId`, `assigneeId`, `teamId`, `projectId`, `cycleId`, `labelIds`, `dueDate`, `sortOrder`, `branchName`, `createdAt`, `updatedAt`, `startedAt`, `completedAt`, `canceledAt`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `…raw row…`, `id`, `body`, `issueId`, `userId`, `createdAt`, `updatedAt`
- **workspace** (singleton, via `_store.register_document`)
  - Wire fields (from `_workspace_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/teams` → `list_teams(limit, offset)`
- `GET /v1/teams/{team_id}` → `get_team(team_id)`
- `GET /v1/teams/{team_id}/members` → `get_team_members(team_id)`
- `GET /v1/teams/{team_id}/issues` → `get_team_issues(team_id, limit, offset)`
- `GET /v1/teams/{team_id}/projects` → `get_team_projects(team_id)`
- `GET /v1/teams/{team_id}/cycles` → `get_team_cycles(team_id)`
- `GET /v1/teams/{team_id}/workflow-states` → `get_team_workflow_states(team_id)`
- `GET /v1/teams/{team_id}/labels` → `get_team_labels(team_id)`
- `GET /v1/users` → `list_users(limit, offset)`
- `GET /v1/users/{user_id}` → `get_user(user_id)`
- `GET /v1/users/{user_id}/issues` → `get_user_assigned_issues(user_id, limit, offset)`
- `GET /v1/workflow-states` → `list_workflow_states(teamId, limit, offset)`
- `GET /v1/workflow-states/{state_id}` → `get_workflow_state(state_id)`
- `GET /v1/labels` → `list_labels(teamId, limit, offset)`
- `GET /v1/labels/{label_id}` → `get_label(label_id)`
- `POST /v1/labels` → `create_label(body)`
- `GET /v1/projects` → `list_projects(limit, offset)`
- `GET /v1/projects/{project_id}` → `get_project(project_id)`
- `POST /v1/projects` → `create_project(body)`
- `PUT /v1/projects/{project_id}` → `update_project(project_id, body)`
- `GET /v1/projects/{project_id}/issues` → `get_project_issues(project_id, limit, offset)`
- `GET /v1/cycles` → `list_cycles(teamId, status, limit, offset)`
- `GET /v1/cycles/{cycle_id}` → `get_cycle(cycle_id)`
- `POST /v1/cycles` → `create_cycle(body)`
- `GET /v1/cycles/{cycle_id}/issues` → `get_cycle_issues(cycle_id, limit, offset)`
- `GET /v1/issues` → `list_issues(stateId, assigneeId, projectId, cycleId, teamId, priority, labelId, limit, offset)`
- `GET /v1/issues/search` → `search_issues(q, limit, offset)`
- `GET /v1/issues/{issue_id}` → `get_issue(issue_id)`
- `POST /v1/issues` → `create_issue(body)`
- `PUT /v1/issues/{issue_id}` → `update_issue(issue_id, body)`
- `DELETE /v1/issues/{issue_id}` → `delete_issue(issue_id)`
- `GET /v1/issues/{issue_id}/comments` → `list_comments(issue_id, limit, offset)`
- `GET /v1/comments/{comment_id}` → `get_comment(comment_id)`
- `POST /v1/comments` → `create_comment(body)`
- `PUT /v1/comments/{comment_id}` → `update_comment(comment_id, body)`
- `DELETE /v1/comments/{comment_id}` → `delete_comment(comment_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 8 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### linkedin-api

**Base path**: `/v2`

**Entities** (from `_store.register(...)` in `linkedin_data.py`):

- **posts** (pk=`id`)
  - Internal fields (from `_coerce_posts`): `…raw row…`, `socialDetail`, `likeCount`, `commentCount`, `shareCount`
- **organizations** (pk=`id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **jobs** (pk=`id`)
  - Internal fields (from `_coerce_jobs`): `…raw row…`, `applicants`, `keywords`
- **connections** (pk=`id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **profile** (singleton, via `_store.register_document`)
  - Wire fields (from `_profile_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v2/me` → `get_me()`
- `GET /v2/connections` → `list_connections(start, count)`
- `GET /v2/posts` → `list_posts(author_id, start, count)`
- `POST /v2/posts` → `create_post(body)`
- `GET /v2/posts/{post_id}` → `get_post(post_id)`
- `GET /v2/organizations/{org_id}` → `get_organization(org_id)`
- `GET /v2/jobs` → `search_jobs(keywords, location, start, count)`
- `GET /v2/jobs/{job_id}` → `get_job(job_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### mailchimp-api

**Base path**: `/3.0`

**Entities** (from `_store.register(...)` in `mailchimp_data.py`):

- **lists** (pk=`id`)
  - Internal fields (from `_coerce_lists`): `id`, `name`, `company`, `from_name`, `from_email`, `subject`, `member_count`, `unsubscribe_count`, `date_created`
- **members** (pk=`_pk`)
  - Internal fields (from `_coerce_members`): `id`, `list_id`, `email_address`, `full_name`, `status`, `timestamp_signup`, `member_rating`, `_pk`
- **campaigns** (pk=`id`)
  - Internal fields (from `_coerce_campaigns`): `id`, `list_id`, `type`, `status`, `emails_sent`, `send_time`, `create_time`, `recipients`, `settings`, `subject_line`, `from_name`, `reply_to`, `title`
- **reports** (pk=`id`)
  - Internal fields (from `_coerce_reports`): `id`, `emails_sent`, `opens`, `clicks`, `unsubscribed`, `bounces`, `opens_total`, `unique_opens`, `open_rate`, `clicks_total`, `unique_clicks`, `click_rate`, `hard_bounces`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /3.0/lists` → `list_lists()`
- `GET /3.0/lists/{list_id}` → `get_list(list_id)`
- `GET /3.0/lists/{list_id}/members` → `list_members(list_id, status)`
- `POST /3.0/lists/{list_id}/members` → `create_member(list_id, body)`
- `GET /3.0/lists/{list_id}/members/{subscriber_hash}` → `get_member(list_id, subscriber_hash)`
- `PATCH /3.0/lists/{list_id}/members/{subscriber_hash}` → `update_member(list_id, subscriber_hash, body)`
- `GET /3.0/campaigns` → `list_campaigns(status)`
- `POST /3.0/campaigns` → `create_campaign(body)`
- `GET /3.0/campaigns/{campaign_id}` → `get_campaign(campaign_id)`
- `POST /3.0/campaigns/{campaign_id}/actions/send` → `send_campaign(campaign_id)`
- `GET /3.0/reports/{campaign_id}` → `get_report(campaign_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### mailgun-api

**Base path**: `/v3`

**Entities** (from `_store.register(...)` in `mailgun_data.py`):

- **messages** (pk=`id`)
  - Internal fields (from `_coerce_messages`): `id`, `domain`, `sender`, `recipient`, `subject`, `body`, `timestamp`
- **events** (pk=`id`)
  - Internal fields (from `_coerce_events`): `id`, `domain`, `message_id`, `event`, `recipient`, `timestamp`, `reason`
- **members** (pk=`list_address`)
  - Internal fields (from `_coerce_members`): `list_address`, `address`, `name`, `subscribed`, `vars`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /v3/{domain}/messages` → `send_message(domain, sender, to, subject, text)`
- `GET /v3/{domain}/events` → `get_events(domain, event, recipient, limit)`
- `GET /v3/{domain}/stats/total` → `get_stats_total(domain, event)`
- `GET /v3/lists/{address}/members` → `list_members(address, subscribed)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### microsoft-teams-api

**Base path**: `/v1.0`

**Entities** (from `_store.register(...)` in `microsoft_teams_data.py`):

- **teams** (pk=`id`)
  - Internal fields (from `_coerce_teams`): `id`, `displayName`, `description`, `visibility`, `isArchived`, `webUrl`, `member_ids`
  - Wire fields (from `_serialize_team`): `id`, `displayName`, `description`, `visibility`, `isArchived`, `webUrl`
- **channels** (pk=`id`)
  - Internal fields (from `_coerce_channels`): `id`, `team_id`, `displayName`, `description`, `membershipType`, `webUrl`, `createdDateTime`
  - Wire fields (from `_serialize_channel`): `id`, `displayName`, `description`, `membershipType`, `webUrl`, `createdDateTime`
- **messages** (pk=`id`)
  - Internal fields (from `_coerce_messages`): `id`, `channel_id`, `team_id`, `from_user_id`, `from_display_name`, `content`, `contentType`, `importance`, `createdDateTime`
  - Wire fields (from `_serialize_message`): `id`, `messageType`, `createdDateTime`, `importance`, `channelIdentity`, `from`, `body`, `teamId`, `channelId`, `user`, `contentType`, `content`, `displayName`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1.0/me/joinedTeams` → `joined_teams()`
- `GET /v1.0/teams/{team_id}` → `get_team(team_id)`
- `GET /v1.0/teams/{team_id}/channels` → `list_channels(team_id)`
- `GET /v1.0/teams/{team_id}/channels/{channel_id}/messages` → `list_messages(team_id, channel_id)`
- `POST /v1.0/teams/{team_id}/channels/{channel_id}/messages` → `send_message(team_id, channel_id, body)`

**Additional data-layer functions** (referenced indirectly): `list_joined_teams()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### mixpanel-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `mixpanel_data.py`):

- **events** (pk=`event_id`)
  - Internal fields (from `_coerce_events`): `event_id`, `event`, `distinct_id`, `time`, `properties`
- **funnels** (singleton, via `_store.register_document`)
  - Internal fields (from `_coerce_funnels`): `funnel_id`, `name`, `steps`, `order`, `event`, `count`
  - Wire fields (from `_funnels_doc`): passthrough — same as internal.
- **profiles** (pk=`distinct_id`)
  - Internal fields (from `_coerce_profiles`): `distinct_id`, `properties`, `$name`, `$email`, `country`, `plan`, `total_events`, `$last_seen`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /track` → `track(body)`
- `GET /api/2.0/events` → `events(event, from_date, to_date)`
- `GET /api/2.0/funnels/list` → `funnels_list()`
- `GET /api/2.0/funnels` → `funnel(funnel_id)`
- `GET /api/2.0/segmentation` → `segmentation(event, from_date, to_date, on)`
- `GET /api/2.0/engage` → `engage(distinct_id, where, page_size)`

**Additional data-layer functions** (referenced indirectly): `events_counts(event, from_date, to_date)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 2 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### monday-api

**Base path**: `/v2`

**Entities** (from `_store.register(...)` in `monday_data.py`):

- **workspaces** (pk=`workspace_id`)
  - Internal fields (from `_coerce_workspaces`): passthrough — no field rewrites (returns rows unchanged).
- **boards** (pk=`board_id`)
  - Internal fields (from `_coerce_boards`): passthrough — no field rewrites (returns rows unchanged).
- **groups** (pk=`_pk`)
  - Internal fields (from `_coerce_groups`): `…raw row…`, `position`
- **columns** (pk=`_pk`)
  - Internal fields (from `_coerce_columns`): `…raw row…`, `position`
- **items** (pk=`item_id`)
  - Internal fields (from `_coerce_items`): passthrough — no field rewrites (returns rows unchanged).
  - Wire fields (from `_item_view`): `id`, `name`, `board_id`, `group`, `created_at`, `column_values`
- **column_values** (pk=`_pk`)
  - Internal fields (from `_coerce_column_values`): `item_id`, `column_id`, `text`, `value`, `_pk`
- **users** (pk=`user_id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `is_admin`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v2/workspaces` → `workspaces()`
- `GET /v2/boards` → `boards(workspace_id)`
- `GET /v2/boards/{board_id}` → `get_board(board_id)`
- `GET /v2/boards/{board_id}/items` → `board_items(board_id)`
- `GET /v2/items` → `list_items(board_id, group_id)`
- `POST /v2/items` → `create_item(body)`
- `GET /v2/items/{item_id}` → `get_item(item_id)`
- `PUT /v2/items/{item_id}` → `update_item(item_id, body)`
- `DELETE /v2/items/{item_id}` → `delete_item(item_id)`
- `GET /v2/users` → `users()`

**Additional data-layer functions** (referenced indirectly): `list_workspaces()`, `list_boards(workspace_id)`, `get_board_items(board_id)`, `list_users()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 7 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### myfitnesspal-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `myfitnesspal_data.py`):

- **foods** (pk=`food_id`)
  - Internal fields (from `_coerce_foods`): `…raw row…`, `food_id`, `calories`, `total_fat_g`, `saturated_fat_g`, `cholesterol_mg`, `sodium_mg`, `total_carbs_g`, `dietary_fiber_g`, `sugars_g`, `protein_g`, `potassium_mg`, `is_verified`
- **diary_entries** (pk=`entry_id`)
  - Internal fields (from `_coerce_diary_entries`): `…raw row…`, `entry_id`, `food_id`, `servings`, `calories`, `total_fat_g`, `saturated_fat_g`, `cholesterol_mg`, `sodium_mg`, `total_carbs_g`, `dietary_fiber_g`, `sugars_g`, `protein_g`
- **exercise_types** (pk=`exercise_type_id`)
  - Internal fields (from `_coerce_exercise_types`): `…raw row…`, `exercise_type_id`, `calories_per_minute_low`, `calories_per_minute_high`, `met_value`
- **exercise_log** (pk=`exercise_id`)
  - Internal fields (from `_coerce_exercise_log`): `…raw row…`, `exercise_id`, `exercise_type_id`, `duration_minutes`, `calories_burned`
- **weight_log** (pk=`weight_id`)
  - Internal fields (from `_coerce_weight_log`): `…raw row…`, `weight_id`, `weight_lbs`
- **water_log** (pk=`water_id`)
  - Internal fields (from `_coerce_water_log`): `…raw row…`, `water_id`, `cups`
- **user_profile** (singleton, via `_store.register_document`)
  - Wire fields (from `_user_profile_doc`): passthrough — same as internal.
- **scenario_user_profile** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/user/profile` → `get_user_profile()`
- `GET /v1/user/scenario-profile` → `get_scenario_user_profile()`
- `PUT /v1/user/profile` → `update_user_profile(body)`
- `GET /v1/user/goals` → `get_goals()`
- `PUT /v1/user/goals` → `update_goals(body)`
- `GET /v1/foods/search` → `search_foods(q, limit, offset)`
- `GET /v1/foods/{food_id}` → `get_food(food_id)`
- `GET /v1/user/diary/{date}` → `get_diary(date, meal)`
- `GET /v1/user/diary` → `get_diary_range(start_date, end_date)`
- `POST /v1/user/diary` → `create_diary_entry(body)`
- `PUT /v1/user/diary/{entry_id}` → `update_diary_entry(entry_id, body)`
- `DELETE /v1/user/diary/{entry_id}` → `delete_diary_entry(entry_id)`
- `GET /v1/user/nutrition/{date}` → `get_daily_totals(date)`
- `GET /v1/user/nutrition/weekly/{end_date}` → `get_weekly_summary(end_date)`
- `GET /v1/user/progress` → `get_progress(days)`
- `GET /v1/exercises/types` → `list_exercise_types(category, limit, offset)`
- `GET /v1/exercises/types/{exercise_type_id}` → `get_exercise_type(exercise_type_id)`
- `GET /v1/user/exercises` → `list_exercises(start_date, end_date, limit, offset)`
- `GET /v1/user/exercises/{exercise_id}` → `get_exercise(exercise_id)`
- `POST /v1/user/exercises` → `create_exercise(body)`
- `GET /v1/user/weight` → `list_weight_entries(limit, offset)`
- `GET /v1/user/weight/{weight_id}` → `get_weight_entry(weight_id)`
- `POST /v1/user/weight` → `create_weight_entry(body)`
- `GET /v1/user/water/{date}` → `get_water(date)`
- `POST /v1/user/water` → `create_water(body)`
- `PUT /v1/user/water/{date}` → `update_water(date, body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 6 keyed table(s) + 2 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### nasa-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `nasa_data.py`):

- **apod** (pk=`date`)
  - Internal fields (from `_coerce_apod`): `date`, `title`, `explanation`, `url`, `media_type`, `service_version`, `hdurl`, `copyright`
- **rover_photos** (pk=`id`)
  - Internal fields (from `_coerce_rover_photos`): `id`, `rover`, `sol`, `camera`, `camera_full_name`, `img_src`, `earth_date`
- **rovers** (pk=`name`)
  - Internal fields (from `_coerce_rovers`): `name`, `status`, `landing_date`, `launch_date`, `max_sol`, `max_date`, `total_photos`
- **neos** (pk=`id`)
  - Internal fields (from `_coerce_neos`): `id`, `name`, `close_approach_date`, `absolute_magnitude_h`, `est_diameter_min_km`, `est_diameter_max_km`, `is_potentially_hazardous`, `miss_distance_km`, `relative_velocity_kph`, `orbiting_body`
  - Wire fields (from `_neo_view`): `id`, `neo_reference_id`, `name`, `absolute_magnitude_h`, `estimated_diameter`, `is_potentially_hazardous_asteroid`, `close_approach_data`, `kilometers`, `estimated_diameter_min`, `estimated_diameter_max`, `close_approach_date`, `relative_velocity`, `miss_distance`, `orbiting_body`, `kilometers_per_hour`
- **epic** (pk=`identifier`)
  - Internal fields (from `_coerce_epic`): `identifier`, `image`, `caption`, `date`, `centroid_coordinates`, `lat`, `lon`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /planetary/apod` → `apod(date, start_date, end_date)`
- `GET /mars-photos/api/v1/rovers/{rover}/photos` → `rover_photos(rover, sol, camera, earth_date)`
- `GET /mars-photos/api/v1/rovers/{rover}` → `rover_manifest(rover)`
- `GET /neo/rest/v1/feed` → `neo_feed(start_date, end_date)`
- `GET /neo/rest/v1/neo/{neo_id}` → `neo(neo_id)`
- `GET /EPIC/api/natural` → `epic_natural()`

**Additional data-layer functions** (referenced indirectly): `get_apod(date, start_date, end_date)`, `get_rover_manifest(rover)`, `get_rover_photos(rover, sol, camera, earth_date)`, `get_neo_feed(start_date, end_date)`, `get_neo(neo_id)`, `get_epic_natural()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### notion-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `notion_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `bot`, `avatar_url`, `email`
- **databases** (pk=`id`)
  - Internal fields (from `_coerce_databases`): `…raw row…`, `archived`
- **pages** (pk=`id`)
  - Internal fields (from `_coerce_pages`): `…raw row…`, `archived`, `cover_url`
- **blocks** (pk=`id`)
  - Internal fields (from `_coerce_blocks`): `…raw row…`, `order`, `has_children`, `checked`, `parent_block_id`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `…raw row…`, `resolved`, `parent_block_id`
- **properties** (singleton, via `_store.register_document`)
  - Internal fields (from `_coerce_properties`): `type`, `value`
  - Wire fields (from `_properties_doc`): passthrough — same as internal.
- **workspace** (singleton, via `_store.register_document`)
  - Wire fields (from `_workspace_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/users` → `list_users(start_cursor, page_size)`
- `GET /v1/users/me` → `get_me()`
- `GET /v1/users/{user_id}` → `get_user(user_id)`
- `GET /v1/workspace` → `get_workspace()`
- `POST /v1/search` → `search(body)`
- `GET /v1/databases/{database_id}` → `get_database(database_id)`
- `POST /v1/databases/{database_id}/query` → `query_database(database_id, body)`
- `GET /v1/pages/{page_id}` → `get_page(page_id)`
- `POST /v1/pages` → `create_page(body)`
- `PATCH /v1/pages/{page_id}` → `update_page(page_id, body)`
- `DELETE /v1/pages/{page_id}` → `delete_page(page_id)`
- `GET /v1/blocks/{block_id}/children` → `list_block_children(block_id, start_cursor, page_size)`
- `PATCH /v1/blocks/{block_id}/children` → `append_block_children(block_id, body)`
- `PATCH /v1/blocks/{block_id}` → `update_block(block_id, body)`
- `DELETE /v1/blocks/{block_id}` → `delete_block(block_id)`
- `GET /v1/comments` → `list_comments(block_id, page_id)`
- `POST /v1/comments` → `create_comment(body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 2 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### obsidian-api

**Base path**: `/vault`

**Entities** (from `_store.register(...)` in `obsidian_data.py`):

- **notes** (pk=`path`)
  - Internal fields (from `_coerce_notes`): `…raw row…`, `size_bytes`, `tags`
- **contents** (singleton, via `_store.register_document`)
  - Internal fields (from `_coerce_contents`): passthrough — no field rewrites (returns rows unchanged).
  - Wire fields (from `_contents_doc`): passthrough — same as internal.
- **vault** (singleton, via `_store.register_document`)
  - Wire fields (from `_vault_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /vault` → `get_vault()`
- `GET /vault/notes` → `list_notes(folder, tag)`
- `GET /vault/notes/{path:path}` → `get_note(path)`
- `POST /vault/notes` → `create_note(body)`
- `PUT /vault/notes/{path:path}` → `update_note(path, body)`
- `DELETE /vault/notes/{path:path}` → `delete_note(path)`
- `GET /vault/search` → `search(query, content)`
- `GET /vault/backlinks/{path:path}` → `list_backlinks(path)`
- `GET /vault/daily/{date_str}` → `get_daily(date_str)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 1 keyed table(s) + 2 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### okta-api

**Base path**: `/api/v1`

**Entities** (from `_store.register(...)` in `okta_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `activated`, `last_login`
  - Wire fields (from `_serialize_user`): `id`, `status`, `created`, `activated`, `lastLogin`, `profile`, `firstName`, `lastName`, `email`, `login`
- **groups** (pk=`id`)
  - Wire fields (from `_serialize_group`): `id`, `type`, `created`, `profile`, `name`, `description`
- **memberships** (pk=`group_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **apps** (pk=`id`)
  - Wire fields (from `_serialize_app`): `id`, `name`, `label`, `status`, `signOnMode`, `created`
- **app_assignments** (pk=`app_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v1/users` → `list_users(status, q)`
- `GET /api/v1/users/{user_id}` → `get_user(user_id)`
- `POST /api/v1/users` → `create_user(body, activate)`
- `POST /api/v1/users/{user_id}/lifecycle/activate` → `activate_user(user_id)`
- `POST /api/v1/users/{user_id}/lifecycle/suspend` → `suspend_user(user_id)`
- `POST /api/v1/users/{user_id}/lifecycle/deactivate` → `deactivate_user(user_id)`
- `GET /api/v1/groups` → `list_groups(q)`
- `GET /api/v1/groups/{group_id}` → `get_group(group_id)`
- `GET /api/v1/groups/{group_id}/users` → `list_group_users(group_id)`
- `GET /api/v1/apps` → `list_apps(status)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### openlibrary-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `openlibrary_data.py`):

- **authors** (pk=`author_id`)
  - Internal fields (from `_coerce_authors`): `author_id`, `name`, `birth_date`, `death_date`, `bio`, `top_work`, `work_count`
- **works** (pk=`work_id`)
  - Internal fields (from `_coerce_works`): `work_id`, `title`, `author_id`, `first_publish_year`, `subjects`, `description`, `edition_count`
  - Wire fields (from `_work_doc`): `key`, `type`, `title`, `first_publish_year`, `author_key`, `author_name`, `subject`, `edition_count`
- **editions** (pk=`edition_id`)
  - Internal fields (from `_coerce_editions`): `edition_id`, `work_id`, `title`, `isbn_13`, `isbn_10`, `publisher`, `publish_date`, `number_of_pages`, `language`
  - Wire fields (from `_edition_doc`): `key`, `title`, `works`, `isbn_13`, `isbn_10`, `publishers`, `publish_date`, `number_of_pages`, `languages`, `type`
- **subjects** (pk=`subject`)
  - Internal fields (from `_coerce_subjects`): passthrough — no field rewrites (returns rows unchanged).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /search.json` → `search(q, author, title, page, limit)`
- `GET /works/{work_id}.json` → `get_work(work_id)`
- `GET /works/{work_id}/editions.json` → `get_work_editions(work_id)`
- `GET /authors/{author_id}.json` → `get_author(author_id)`
- `GET /authors/{author_id}/works.json` → `get_author_works(author_id)`
- `GET /subjects/{subject}.json` → `get_subject(subject)`
- `GET /isbn/{isbn}.json` → `get_isbn(isbn)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### openweather-api

**Base path**: varies

**Entities**: (no `_store.register(...)` calls detected — inspect data module manually)

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /data/2.5/weather` → `current_weather(q, lat, lon)`
- `GET /data/2.5/forecast` → `forecast(q, lat, lon)`
- `GET /geo/1.0/direct` → `geocode_direct(q, limit)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### outlook-api

**Base path**: `/v1.0/me`

**Entities** (from `_store.register(...)` in `outlook_data.py`):

- **messages** (pk=`id`)
  - Internal fields (from `_coerce_messages`): `id`, `subject`, `from_name`, `from_address`, `to_name`, `to_address`, `bodyPreview`, `contentType`, `isRead`, `importance`, `receivedDateTime`
  - Wire fields (from `_serialize_message`): `id`, `subject`, `bodyPreview`, `importance`, `isRead`, `receivedDateTime`, `from`, `toRecipients`, `body`, `emailAddress`, `contentType`, `content`, `name`, `address`
- **events** (pk=`id`)
  - Internal fields (from `_coerce_events`): `id`, `subject`, `organizer_name`, `organizer_address`, `location`, `start`, `end`, `isAllDay`, `isOnlineMeeting`, `attendees`
  - Wire fields (from `_serialize_event`): `id`, `subject`, `isAllDay`, `isOnlineMeeting`, `start`, `end`, `location`, `organizer`, `attendees`, `dateTime`, `timeZone`, `displayName`, `emailAddress`, `name`, `address`, `type`
- **contacts** (pk=`id`)
  - Internal fields (from `_coerce_contacts`): `id`, `displayName`, `givenName`, `surname`, `email`, `jobTitle`, `companyName`, `mobilePhone`
  - Wire fields (from `_serialize_contact`): `id`, `displayName`, `givenName`, `surname`, `emailAddresses`, `jobTitle`, `companyName`, `mobilePhone`, `address`, `name`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1.0/me/messages` → `list_messages(isRead)`
- `GET /v1.0/me/messages/{message_id}` → `get_message(message_id)`
- `POST /v1.0/me/sendMail` → `send_mail(body)`
- `GET /v1.0/me/events` → `list_events()`
- `GET /v1.0/me/contacts` → `list_contacts()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### pagerduty-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `pagerduty_data.py`):

- **users** (pk=`user_id`)
  - Internal fields (from `_coerce_users`): passthrough — no field rewrites (returns rows unchanged).
- **services** (pk=`service_id`)
  - Internal fields (from `_coerce_services`): `…raw row…`, `auto_resolve_timeout`
- **incidents** (pk=`incident_id`)
  - Internal fields (from `_coerce_incidents`): `…raw row…`, `incident_number`, `assigned_to`, `resolved_at`
- **policies** (pk=`escalation_policy_id`)
  - Internal fields (from `_coerce_policies`): `…raw row…`, `num_loops`
- **schedules** (pk=`schedule_id`)
  - Internal fields (from `_coerce_schedules`): passthrough — no field rewrites (returns rows unchanged).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /services` → `list_services()`
- `GET /services/{service_id}` → `get_service(service_id)`
- `GET /incidents` → `list_incidents(statuses, service_id, urgency)`
- `GET /incidents/{incident_id}` → `get_incident(incident_id)`
- `POST /incidents` → `create_incident(body)`
- `PUT /incidents/{incident_id}` → `update_incident(incident_id, body)`
- `GET /incidents/{incident_id}/notes` → `list_notes(incident_id)`
- `POST /incidents/{incident_id}/notes` → `create_note(incident_id, body)`
- `GET /oncalls` → `list_oncalls(escalation_policy_id)`
- `GET /schedules` → `list_schedules()`
- `GET /escalation_policies` → `list_escalation_policies()`
- `GET /users` → `list_users()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### paypal-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `paypal_data.py`):

- **orders** (pk=`id`)
  - Internal fields (from `_coerce_orders`): `id`, `intent`, `status`, `purchase_units`, `create_time`, `amount`, `payee`, `description`, `email_address`
- **captures** (pk=`id`)
  - Internal fields (from `_coerce_captures`): `id`, `order_id`, `status`, `amount`, `final_capture`, `create_time`
- **invoices** (pk=`id`)
  - Internal fields (from `_coerce_invoices`): `id`, `detail`, `status`, `primary_recipients`, `amount`, `due_date`, `invoice_number`, `currency_code`, `note`, `billing_info`, `email_address`
- **payouts** (pk=`payout_batch_id`)
  - Internal fields (from `_coerce_payouts`): `batch_header`, `recipient_email`, `create_time`, `payout_batch_id`, `batch_status`, `sender_batch_header`, `amount`, `sender_batch_id`
- **refunds** (pk=`id`)
  - Internal fields (from `_coerce_refunds`): `id`, `capture_id`, `status`, `amount`, `note_to_payer`, `create_time`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /v2/checkout/orders` → `create_order(body)`
- `GET /v2/checkout/orders/{order_id}` → `get_order(order_id)`
- `POST /v2/checkout/orders/{order_id}/capture` → `capture_order(order_id)`
- `POST /v2/payments/refunds` → `create_refund(body)`
- `GET /v2/payments/refunds/{refund_id}` → `get_refund(refund_id)`
- `GET /v2/invoicing/invoices` → `list_invoices(status, page_size)`
- `POST /v2/invoicing/invoices` → `create_invoice(body)`
- `POST /v1/payments/payouts` → `create_payout(body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### pinterest-api

**Base path**: `/v5`

**Entities** (from `_store.register(...)` in `pinterest_data.py`):

- **boards** (pk=`board_id`)
  - Internal fields (from `_coerce_boards`): `…raw row…`, `board_id`, `pin_count`, `follower_count`, `collaborator_count`
- **board_sections** (pk=`section_id`)
  - Internal fields (from `_coerce_board_sections`): `…raw row…`, `section_id`, `board_id`, `pin_count`
- **pins** (pk=`pin_id`)
  - Internal fields (from `_coerce_pins`): `…raw row…`, `pin_id`, `board_id`, `board_section_id`, `link`, `alt_text`, `is_promoted`, `pin_metrics_impressions`, `pin_metrics_saves`, `pin_metrics_clicks`
- **pin_analytics** (pk=`pin_id`)
  - Internal fields (from `_coerce_pin_analytics`): `pin_id`, `date`, `impressions`, `saves`, `pin_clicks`, `outbound_clicks`
- **user_analytics** (pk=`date`)
  - Internal fields (from `_coerce_user_analytics`): `date`, `impressions`, `saves`, `pin_clicks`, `outbound_clicks`, `profile_visits`, `follows`
- **ad_accounts** (pk=`ad_account_id`)
  - Internal fields (from `_coerce_ad_accounts`): `…raw row…`, `ad_account_id`
- **campaigns** (pk=`campaign_id`)
  - Internal fields (from `_coerce_campaigns`): `…raw row…`, `campaign_id`, `ad_account_id`, `daily_spend_cap_micro`, `lifetime_spend_cap_micro`, `end_time`
- **user_account_raw** (singleton, via `_store.register_document`)
  - Wire fields (from `_user_account_raw_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v5/user_account` → `get_user_account()`
- `GET /v5/user_account/analytics` → `get_user_analytics(start_date, end_date)`
- `GET /v5/boards` → `list_boards(privacy, limit, offset)`
- `GET /v5/boards/{board_id}` → `get_board(board_id)`
- `POST /v5/boards` → `create_board(body)`
- `PATCH /v5/boards/{board_id}` → `update_board(board_id, body)`
- `DELETE /v5/boards/{board_id}` → `delete_board(board_id)`
- `GET /v5/boards/{board_id}/pins` → `list_board_pins(board_id, limit, offset)`
- `GET /v5/boards/{board_id}/sections` → `list_board_sections(board_id)`
- `POST /v5/boards/{board_id}/sections` → `create_board_section(board_id, body)`
- `GET /v5/boards/{board_id}/sections/{section_id}/pins` → `list_section_pins(board_id, section_id, limit, offset)`
- `GET /v5/pins` → `list_pins(limit, offset)`
- `GET /v5/pins/{pin_id}` → `get_pin(pin_id)`
- `POST /v5/pins` → `create_pin(body)`
- `PATCH /v5/pins/{pin_id}` → `update_pin(pin_id, body)`
- `DELETE /v5/pins/{pin_id}` → `delete_pin(pin_id)`
- `GET /v5/pins/{pin_id}/analytics` → `get_pin_analytics(pin_id, start_date, end_date)`
- `GET /v5/search/pins` → `search_pins(query, limit, offset)`
- `GET /v5/media/{media_id}` → `get_media_upload_status(media_id)`
- `GET /v5/ad_accounts` → `list_ad_accounts(limit, offset)`
- `GET /v5/ad_accounts/{ad_account_id}` → `get_ad_account(ad_account_id)`
- `GET /v5/ad_accounts/{ad_account_id}/campaigns` → `list_campaigns(ad_account_id, status, limit, offset)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 7 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### plaid-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `plaid_data.py`):

- **accounts** (pk=`account_id`)
  - Internal fields (from `_coerce_accounts`): `account_id`, `name`, `official_name`, `mask`, `type`, `subtype`, `balances`, `available`, `current`, `limit`, `iso_currency_code`, `unofficial_currency_code`
- **transactions** (pk=`transaction_id`)
  - Internal fields (from `_coerce_transactions`): `transaction_id`, `account_id`, `amount`, `iso_currency_code`, `date`, `name`, `merchant_name`, `category`, `pending`, `payment_channel`
- **item** (singleton, via `_store.register_document`)
  - Wire fields (from `_item_doc`): passthrough — same as internal.
- **identity** (singleton, via `_store.register_document`)
  - Wire fields (from `_identity_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /accounts/get` → `accounts_get(body)`
- `POST /accounts/balance/get` → `accounts_balance_get(body)`
- `POST /transactions/get` → `transactions_get(body)`
- `POST /institutions/get_by_id` → `institutions_get_by_id(body)`
- `POST /identity/get` → `identity_get(body)`

**Additional data-layer functions** (referenced indirectly): `get_accounts(account_ids)`, `get_balances(account_ids)`, `get_transactions(start_date, end_date, account_ids, count, offset)`, `get_institution_by_id(institution_id)`, `get_identity(account_ids)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 2 keyed table(s) + 2 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### posthog-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `posthog_data.py`):

- **events** (pk=`id`)
  - Internal fields (from `_coerce_events`): `id`, `project_id`, `distinct_id`, `event`, `timestamp`, `properties`
- **flags** (pk=`id`)
  - Internal fields (from `_coerce_flags`): `id`, `project_id`, `key`, `name`, `active`, `rollout_percentage`
  - Wire fields (from `_serialize_flag`): `id`, `key`, `name`, `active`, `rollout_percentage`
- **persons** (pk=`id`)
  - Internal fields (from `_coerce_persons`): `id`, `project_id`, `distinct_id`, `name`, `email`, `created_at`
  - Wire fields (from `_serialize_person`): `id`, `distinct_ids`, `name`, `properties`, `created_at`, `email`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /capture` → `capture(body)`
- `POST /decide` → `decide(body)`
- `GET /api/projects/{project_id}/events` → `list_events(project_id, event, distinct_id)`
- `GET /api/projects/{project_id}/feature_flags` → `list_feature_flags(project_id)`
- `GET /api/projects/{project_id}/persons` → `list_persons(project_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### quickbooks-api

**Base path**: `/v3/company`

**Entities** (from `_store.register(...)` in `quickbooks_data.py`):

- **customers** (pk=`Id`)
  - Internal fields (from `_coerce_customers`): `Id`, `DisplayName`, `GivenName`, `FamilyName`, `CompanyName`, `PrimaryEmailAddr`, `PrimaryPhone`, `BillAddr`, `Balance`, `Active`, `Job`, `Notes`, `MetaData`, `SyncToken`, `Line1`, `City`, `CountrySubDivisionCode`, `PostalCode`, `CreateTime`, `LastUpdatedTime`, `Address`, `FreeFormNumber`
- **vendors** (pk=`Id`)
  - Internal fields (from `_coerce_vendors`): `Id`, `DisplayName`, `CompanyName`, `PrimaryEmailAddr`, `PrimaryPhone`, `BillAddr`, `Balance`, `Active`, `AcctNum`, `Vendor1099`, `MetaData`, `SyncToken`, `Line1`, `City`, `CountrySubDivisionCode`, `PostalCode`, `CreateTime`, `LastUpdatedTime`, `Address`, `FreeFormNumber`
- **items** (pk=`Id`)
  - Internal fields (from `_coerce_items`): `Id`, `Name`, `Description`, `Type`, `UnitPrice`, `IncomeAccountRef`, `Active`, `Taxable`, `MetaData`, `SyncToken`, `value`, `name`, `CreateTime`, `LastUpdatedTime`
- **accounts** (pk=`Id`)
  - Internal fields (from `_coerce_accounts`): `Id`, `Name`, `AccountType`, `AccountSubType`, `CurrentBalance`, `Active`, `Classification`, `Description`, `MetaData`, `SyncToken`, `CreateTime`, `LastUpdatedTime`
- **invoices** (pk=`Id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **bills** (pk=`Id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **payments** (pk=`Id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **estimates** (pk=`Id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **expenses** (pk=`Id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **company_info** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **company_raw** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **bill_payments** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **corporate_expense_ledger** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **reimbursement_policy** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **break_even_analysis** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v3/company/{realm_id}/companyinfo/{company_id}` → `get_company_info(realm_id, company_id)`
- `GET /v3/company/{realm_id}/customer/{customer_id}` → `get_customer(realm_id, customer_id)`
- `POST /v3/company/{realm_id}/customer` → `create_or_update_customer(realm_id, body)`
- `GET /v3/company/{realm_id}/vendor/{vendor_id}` → `get_vendor(realm_id, vendor_id)`
- `POST /v3/company/{realm_id}/vendor` → `create_or_update_vendor(realm_id, body)`
- `GET /v3/company/{realm_id}/item/{item_id}` → `get_item(realm_id, item_id)`
- `POST /v3/company/{realm_id}/item` → `create_or_update_item(realm_id, body)`
- `GET /v3/company/{realm_id}/account/{account_id}` → `get_account(realm_id, account_id)`
- `GET /v3/company/{realm_id}/invoice/{invoice_id}` → `get_invoice(realm_id, invoice_id)`
- `GET /v3/company/{realm_id}/invoice/{invoice_id}/pdf` → `get_invoice_pdf(realm_id, invoice_id)`
- `POST /v3/company/{realm_id}/invoice` → `create_or_update_invoice(realm_id, body)`
- `POST /v3/company/{realm_id}/invoice/{invoice_id}` → `void_or_send_invoice(realm_id, invoice_id, operation, include)`
- `GET /v3/company/{realm_id}/bill/{bill_id}` → `get_bill(realm_id, bill_id)`
- `POST /v3/company/{realm_id}/bill` → `create_bill(realm_id, body)`
- `POST /v3/company/{realm_id}/bill/{bill_id}` → `pay_bill(realm_id, bill_id, operation)`
- `GET /v3/company/{realm_id}/payment/{payment_id}` → `get_payment(realm_id, payment_id)`
- `POST /v3/company/{realm_id}/payment` → `create_payment(realm_id, body)`
- `GET /v3/company/{realm_id}/estimate/{estimate_id}` → `get_estimate(realm_id, estimate_id)`
- `POST /v3/company/{realm_id}/estimate` → `create_estimate(realm_id, body)`
- `POST /v3/company/{realm_id}/estimate/{estimate_id}` → `convert_estimate(realm_id, estimate_id, operation)`
- `GET /v3/company/{realm_id}/purchase/{expense_id}` → `get_expense(realm_id, expense_id)`
- `POST /v3/company/{realm_id}/purchase` → `create_expense(realm_id, body)`
- `GET /v3/company/{realm_id}/query` → `query_entities(realm_id, query)`
- `GET /v3/company/{realm_id}/reports/ProfitAndLoss` → `report_profit_and_loss(realm_id, start_date, end_date)`
- `GET /v3/company/{realm_id}/reports/BalanceSheet` → `report_balance_sheet(realm_id, start_date, end_date)`
- `GET /v3/company/{realm_id}/reports/AgedReceivableDetail` → `report_ar_aging(realm_id)`
- `GET /v3/company/{realm_id}/reports/AgedPayableDetail` → `report_ap_aging(realm_id)`
- `GET /v3/company/{realm_id}/company` → `get_company_raw(realm_id)`
- `GET /v3/company/{realm_id}/billpayments` → `get_bill_payments(realm_id)`
- `GET /v3/company/{realm_id}/reports/BreakEvenAnalysis` → `report_break_even(realm_id)`
- `GET /v3/company/{realm_id}/documents/CorporateExpenseLedger` → `get_corporate_expense_ledger(realm_id)`
- `GET /v3/company/{realm_id}/documents/ReimbursementPolicy` → `get_reimbursement_policy(realm_id)`

**Additional data-layer functions** (referenced indirectly): `get_break_even_analysis()`, `list_customers()`, `create_customer(data)`, `update_customer(customer_id, data)`, `list_vendors()`, `create_vendor(data)`, `update_vendor(vendor_id, data)`, `list_items()`, `create_item(data)`, `update_item(item_id, data)`, `list_accounts()`, `list_invoices()`, `create_invoice(data)`, `update_invoice(invoice_id, data)`, `void_invoice(invoice_id)`, `send_invoice(invoice_id)`, `list_bills()`, `list_payments()`, `list_estimates()`, `convert_estimate_to_invoice(estimate_id)`, `list_expenses()`, `execute_query(query_str)`, `profit_and_loss(start_date, end_date)`, `balance_sheet(start_date, end_date)`, `accounts_receivable_aging()`, `accounts_payable_aging()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 9 keyed table(s) + 6 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### reddit-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `reddit_data.py`):

- **subreddits** (pk=`id`)
  - Internal fields (from `_coerce_subreddits`): `id`, `display_name`, `title`, `public_description`, `subscribers`, `created_utc`, `over18`
- **posts** (pk=`id`)
  - Internal fields (from `_coerce_posts`): `id`, `subreddit`, `title`, `author`, `url`, `selftext`, `score`, `ups`, `num_comments`, `created_utc`, `is_self`, `_likes`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `id`, `post_id`, `parent_id`, `author`, `body`, `score`, `ups`, `created_utc`
- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `name`, `id`, `link_karma`, `comment_karma`, `created_utc`, `is_gold`, `is_mod`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /r/{subreddit}/about` → `subreddit_about(subreddit)`
- `GET /r/{subreddit}/hot` → `subreddit_hot(subreddit, limit)`
- `GET /r/{subreddit}/new` → `subreddit_new(subreddit, limit)`
- `GET /comments/{post_id}` → `post_comments(post_id)`
- `POST /api/submit` → `submit(body)`
- `POST /api/vote` → `vote(body)`
- `GET /user/{username}/about` → `user_about(username)`

**Additional data-layer functions** (referenced indirectly): `subreddit_listing(name, sort, limit)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### ring-api

**Base path**: `/clients_api`

**Entities** (from `_store.register(...)` in `ring_data.py`):

- **devices** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **location** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **active_dings** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **events** (pk=`id`)
  - Internal fields (from `_coerce_events`): `id`, `doorbot_id`, `device_id`, `kind`, `created_at`, `answered`, `favorite`, `recording`, `snapshot_url`, `duration_seconds`, `cv_properties`, `status`
- **shared_users** (pk=`user_id`)
  - Internal fields (from `_coerce_shared_users`): `user_id`, `first_name`, `last_name`, `email`, `role`, `device_access`, `shared_at`
- **motion_zones** (pk=`_pk`)
  - Internal fields (from `_coerce_motion_zones`): `device_id`, `zone_id`, `zone_name`, `sensitivity`, `enabled`, `coordinates`
- **notification_prefs** (pk=`device_id`)
  - Internal fields (from `_coerce_notification_prefs`): `device_id`, `motion_alerts`, `ding_alerts`, `person_alerts`, `package_alerts`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /clients_api/ring_devices` → `list_devices()`
- `GET /clients_api/doorbots/{device_id}` → `get_device(device_id)`
- `GET /clients_api/doorbots/{device_id}/health` → `get_device_health(device_id)`
- `PUT /clients_api/doorbots/{device_id}/settings` → `update_device_settings(device_id, body)`
- `GET /clients_api/locations/{location_id}` → `get_location(location_id)`
- `GET /clients_api/locations/{location_id}/devices` → `list_location_devices(location_id)`
- `GET /clients_api/locations/{location_id}/mode` → `get_location_mode(location_id)`
- `PUT /clients_api/locations/{location_id}/mode` → `set_location_mode(location_id, body)`
- `GET /clients_api/dings/active` → `list_active_dings()`
- `GET /clients_api/doorbots/{device_id}/history` → `list_device_events(device_id, kind, date_from, date_to, limit, offset)`
- `GET /clients_api/dings/{event_id}` → `get_event(event_id)`
- `GET /clients_api/dings/{event_id}/recording` → `get_event_recording(event_id)`
- `GET /clients_api/doorbots/{device_id}/recordings` → `list_recordings(device_id, date_from, date_to)`
- `GET /clients_api/locations/{location_id}/users` → `list_shared_users(location_id)`
- `GET /clients_api/locations/{location_id}/users/{user_id}` → `get_shared_user(location_id, user_id)`
- `GET /clients_api/chimes/{device_id}/settings` → `get_chime_settings(device_id)`
- `PUT /clients_api/chimes/{device_id}/link` → `link_chime_to_doorbell(device_id, body)`
- `PUT /clients_api/chimes/{device_id}/unlink` → `unlink_chime_from_doorbell(device_id, body)`
- `GET /clients_api/doorbots/{device_id}/motion_zones` → `list_motion_zones(device_id)`
- `GET /clients_api/notifications` → `list_notification_prefs()`
- `GET /clients_api/notifications/{device_id}` → `get_notification_pref(device_id)`
- `PUT /clients_api/notifications/{device_id}` → `update_notification_pref(device_id, body)`
- `POST /clients_api/doorbots/{device_id}/siren_on` → `activate_siren(device_id, body)`
- `POST /clients_api/doorbots/{device_id}/siren_off` → `deactivate_siren(device_id)`
- `PUT /clients_api/doorbots/{device_id}/floodlight_light_on` → `toggle_floodlight(device_id, body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 3 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### salesforce-api

**Base path**: `/services/data/v59.0`

**Entities** (from `_store.register(...)` in `salesforce_data.py`):

- **<dynamic>** (pk=`Id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /services/data/v59.0/sobjects/{sobject}` → `list_records(sobject, limit)`
- `GET /services/data/v59.0/sobjects/{sobject}/{record_id}` → `get_record(sobject, record_id)`
- `POST /services/data/v59.0/sobjects/{sobject}` → `create_record(sobject, body)`
- `PATCH /services/data/v59.0/sobjects/{sobject}/{record_id}` → `update_record(sobject, record_id, body)`
- `GET /services/data/v59.0/query` → `soql_query(q)`

**Additional data-layer functions** (referenced indirectly): `query(soql)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 1 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### segment-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `segment_data.py`):

- **events** (pk=`messageId`)
  - Internal fields (from `_coerce_events`): `messageId`, `type`, `userId`, `event`, `timestamp`, `properties`
- **sources** (pk=`id`)
  - Internal fields (from `_coerce_sources`): `id`, `name`, `slug`, `enabled`, `type`, `createdAt`
- **destinations** (pk=`id`)
  - Internal fields (from `_coerce_destinations`): `id`, `name`, `slug`, `enabled`, `sourceId`, `createdAt`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /v1/track` → `track(body)`
- `POST /v1/identify` → `identify(body)`
- `POST /v1/page` → `page(body)`
- `POST /v1/batch` → `batch(body)`
- `GET /v1/events` → `list_events(type, userId)`
- `GET /v1/sources` → `list_sources()`
- `GET /v1/destinations` → `list_destinations()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### sendgrid-api

**Base path**: `/v3`

**Entities** (from `_store.register(...)` in `sendgrid_data.py`):

- **templates** (pk=`id`)
  - Internal fields (from `_coerce_templates`): `…raw row…`, `active`
  - Wire fields (from `_serialize_template`): `id`, `name`, `generation`, `updated_at`, `versions`, `subject`, `html_content`, `active`
- **lists** (pk=`id`)
  - Internal fields (from `_coerce_lists`): `…raw row…`, `contact_count`
- **contacts** (pk=`id`)
  - Internal fields (from `_coerce_contacts`): `…raw row…`, `list_ids`
  - Wire fields (from `_serialize_contact`): `id`, `email`, `first_name`, `last_name`, `country`, `list_ids`, `created_at`, `updated_at`
- **sent_log** (pk=`message_id`)
  - Internal fields (from `_coerce_sent_log`): `…raw row…`, `opens`, `clicks`
- **stats** (pk=`date`)
  - Internal fields (from `_coerce_stats`): `date`, `requests`, `delivered`, `opens`, `unique_opens`, `clicks`, `unique_clicks`, `bounces`, `spam_reports`, `unsubscribes`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /v3/mail/send` → `send_mail(body)`
- `GET /v3/templates` → `list_templates(generations)`
- `GET /v3/templates/{template_id}` → `get_template(template_id)`
- `POST /v3/templates` → `create_template(body)`
- `GET /v3/marketing/contacts` → `list_contacts(email)`
- `POST /v3/marketing/contacts` → `upsert_contacts(body)`
- `GET /v3/marketing/lists` → `list_lists()`
- `GET /v3/stats` → `get_stats(start_date, end_date)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### sentry-api

**Base path**: `/api/0`

**Entities** (from `_store.register(...)` in `sentry_data.py`):

- **organizations** (pk=`id`)
  - Internal fields (from `_coerce_organizations`): `…raw row…`, `id`
- **projects** (pk=`id`)
  - Internal fields (from `_coerce_projects`): `…raw row…`, `id`
- **issues** (pk=`id`)
  - Internal fields (from `_coerce_issues`): `…raw row…`, `id`, `count`, `user_count`
  - Wire fields (from `_serialize_issue`): `id`, `shortId`, `title`, `culprit`, `level`, `status`, `count`, `userCount`, `project`, `firstSeen`, `lastSeen`, `slug`
- **events** (pk=`event_id`)
  - Internal fields (from `_coerce_events`): `…raw row…`, `id`, `issue_id`
  - Wire fields (from `_serialize_event`): `id`, `eventID`, `message`, `platform`, `environment`, `release`, `user`, `dateCreated`, `email`
- **releases** (pk=`version`)
  - Internal fields (from `_coerce_releases`): `…raw row…`, `new_groups`, `date_released`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/0/organizations/{org_slug}/projects/` → `list_org_projects(org_slug)`
- `GET /api/0/projects/{org_slug}/{project_slug}/issues/` → `list_project_issues(org_slug, project_slug, status, level)`
- `GET /api/0/organizations/{org_slug}/issues/{issue_id}/` → `get_issue(org_slug, issue_id)`
- `PUT /api/0/organizations/{org_slug}/issues/{issue_id}/` → `update_issue(org_slug, issue_id, body)`
- `GET /api/0/organizations/{org_slug}/issues/{issue_id}/events/` → `list_issue_events(org_slug, issue_id)`
- `GET /api/0/organizations/{org_slug}/releases/` → `list_releases(org_slug, project)`

**Additional data-layer functions** (referenced indirectly): `update_issue_status(org_slug, issue_id, status)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### servicenow-api

**Base path**: `/api/now/table`

**Entities** (from `_store.register(...)` in `servicenow_data.py`):

- **incidents** (pk=`sys_id`)
  - Internal fields (from `_coerce_incidents`): passthrough — no field rewrites (returns rows unchanged).
- **changes** (pk=`sys_id`)
  - Internal fields (from `_coerce_changes`): passthrough — no field rewrites (returns rows unchanged).
- **problems** (pk=`sys_id`)
  - Internal fields (from `_coerce_problems`): passthrough — no field rewrites (returns rows unchanged).
- **users** (pk=`sys_id`)
  - Internal fields (from `_coerce_users`): `active`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/now/table/incident` → `list_incidents(sysparm_query, sysparm_limit)`
- `GET /api/now/table/incident/{sys_id}` → `get_incident(sys_id)`
- `POST /api/now/table/incident` → `create_incident(body)`
- `PATCH /api/now/table/incident/{sys_id}` → `update_incident(sys_id, body)`
- `GET /api/now/table/change_request` → `list_change_requests(sysparm_query, sysparm_limit)`
- `GET /api/now/table/change_request/{sys_id}` → `get_change_request(sys_id)`
- `GET /api/now/table/problem` → `list_problems(sysparm_query, sysparm_limit)`
- `GET /api/now/table/problem/{sys_id}` → `get_problem(sys_id)`
- `GET /api/now/table/sys_user` → `list_users(sysparm_query, sysparm_limit)`
- `GET /api/now/table/sys_user/{sys_id}` → `get_user(sys_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### shippo-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `shippo_data.py`):

- **addresses** (pk=`object_id`)
  - Internal fields (from `_coerce_addresses`): `…raw row…`, `is_residential`, `validated`
  - Wire fields (from `_address_obj`): passthrough — same as internal.
- **parcels** (pk=`object_id`)
  - Internal fields (from `_coerce_parcels`): `…raw row…`, `length`, `width`, `height`, `weight`, `template`
  - Wire fields (from `_parcel_obj`): passthrough — same as internal.
- **shipments** (pk=`object_id`)
  - Internal fields (from `_coerce_shipments`): `…raw row pass-through…`
  - Wire fields (from `_shipment_obj`): `object_id`, `status`, `object_created`, `address_from`, `address_to`, `parcels`, `rates`
- **rates** (pk=`object_id`)
  - Internal fields (from `_coerce_rates`): `…raw row…`, `amount`, `estimated_days`
  - Wire fields (from `_rate_obj`): `object_id`, `shipment`, `provider`, `servicelevel`, `amount`, `currency`, `estimated_days`, `token`, `name`
- **transactions** (pk=`object_id`)
  - Internal fields (from `_coerce_transactions`): `…raw row pass-through…`
- **tracking** (pk=`carrier`)
  - Internal fields (from `_coerce_tracking`): `…raw row pass-through…`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /addresses` → `create_address(body)`
- `GET /addresses/{object_id}` → `get_address(object_id)`
- `POST /shipments` → `create_shipment(body)`
- `GET /shipments/{object_id}` → `get_shipment(object_id)`
- `GET /shipments/{object_id}/rates` → `list_shipment_rates(object_id)`
- `POST /transactions` → `create_transaction(body)`
- `GET /transactions/{object_id}` → `get_transaction(object_id)`
- `GET /tracks/{carrier}/{tracking_number}` → `get_tracking(carrier, tracking_number)`

**Additional data-layer functions** (referenced indirectly): `create_parcel(payload)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 6 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### slack-api

**Base path**: `/api`

**Entities** (from `_store.register(...)` in `slack_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `is_admin`, `is_bot`
- **channels** (pk=`id`)
  - Internal fields (from `_coerce_channels`): `…raw row…`, `is_private`, `is_archived`, `created`, `num_members`
- **messages** (pk=`ts`)
  - Internal fields (from `_coerce_messages`): `…raw row…`, `thread_ts`, `reply_count`, `reactions`
- **channel_members** (pk=`channel_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **team** (singleton, via `_store.register_document`)
  - Wire fields (from `_team_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/auth.test` → `auth_test()`
- `POST /api/auth.test` → `auth_test()`
- `GET /api/team.info` → `team_info()`
- `GET /api/users.list` → `users_list()`
- `GET /api/users.info` → `users_info(user)`
- `POST /api/users.setPresence` → `users_set_presence(body)`
- `GET /api/conversations.list` → `conversations_list(types, exclude_archived)`
- `GET /api/conversations.info` → `conversations_info(channel)`
- `POST /api/conversations.create` → `conversations_create(body)`
- `POST /api/conversations.archive` → `conversations_archive(body)`
- `GET /api/conversations.members` → `conversations_members(channel)`
- `POST /api/conversations.invite` → `conversations_invite(body)`
- `GET /api/conversations.history` → `conversations_history(channel, limit, oldest, latest)`
- `GET /api/conversations.replies` → `conversations_replies(channel, ts)`
- `POST /api/chat.postMessage` → `chat_post_message(body)`
- `POST /api/chat.update` → `chat_update(body)`
- `POST /api/chat.delete` → `chat_delete(body)`
- `POST /api/reactions.add` → `reactions_add(body)`
- `GET /api/search.messages` → `search_messages(query)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### spotify-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `spotify_data.py`):

- **artists** (pk=`artist_id`)
  - Internal fields (from `_coerce_artists`): `…raw row…`, `genres`, `followers`, `popularity`
- **albums** (pk=`album_id`)
  - Internal fields (from `_coerce_albums`): `…raw row…`, `total_tracks`
- **tracks** (pk=`track_id`)
  - Internal fields (from `_coerce_tracks`): `…raw row…`, `duration_ms`, `popularity`, `explicit`, `track_number`
  - Wire fields (from `_track_obj`): `id`, `name`, `duration_ms`, `popularity`, `explicit`, `track_number`, `artist`, `album`, `uri`
- **playlists** (pk=`playlist_id`)
  - Internal fields (from `_coerce_playlists`): `…raw row…`, `public`, `collaborative`
  - Wire fields (from `_playlist_obj`): `tracks`, `id`, `name`, `description`, `owner`, `public`, `collaborative`, `uri`, `total`, `items`, `added_at`, `track`
- **playlist_tracks** (pk=`playlist_id`)
  - Internal fields (from `_coerce_playlist_tracks`): `…raw row…`, `position`
  - Wire fields (from `_track_obj`): `id`, `name`, `duration_ms`, `popularity`, `explicit`, `track_number`, `artist`, `album`, `uri`
- **user** (singleton, via `_store.register_document`)
  - Wire fields (from `_user_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/me` → `get_me()`
- `GET /v1/me/playlists` → `list_my_playlists()`
- `GET /v1/playlists/{playlist_id}` → `get_playlist(playlist_id)`
- `GET /v1/playlists/{playlist_id}/tracks` → `get_playlist_tracks(playlist_id)`
- `POST /v1/users/{user_id}/playlists` → `create_playlist(user_id, body)`
- `POST /v1/playlists/{playlist_id}/tracks` → `add_tracks(playlist_id, body)`
- `GET /v1/search` → `search(q, type)`
- `GET /v1/me/player` → `get_player()`
- `PUT /v1/me/player/play` → `start_playback(body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### square-api

**Base path**: `/v2`

**Entities** (from `_store.register(...)` in `square_data.py`):

- **customers** (pk=`id`)
  - Internal fields (from `_coerce_customers`): `id`, `given_name`, `family_name`, `email_address`, `phone_number`, `company_name`, `created_at`
- **catalog** (pk=`id`)
  - Internal fields (from `_coerce_catalog`): `type`, `id`, `item_data`, `name`, `description`, `category`, `variations`, `item_variation_data`, `price_money`
- **inventory** (pk=`catalog_object_id`)
  - Internal fields (from `_coerce_inventory`): `catalog_object_id`, `location_id`, `quantity`, `state`
- **payments** (pk=`id`)
  - Internal fields (from `_coerce_payments`): `id`, `order_id`, `customer_id`, `amount_money`, `status`, `source_type`, `location_id`, `receipt_number`, `created_at`
- **orders** (pk=`id`)
  - Internal fields (from `_coerce_orders`): `id`, `customer_id`, `location_id`, `line_items`, `total_money`, `state`, `created_at`, `catalog_object_id`, `quantity`
- **merchant** (singleton, via `_store.register_document`)
  - Wire fields (from `_merchant_doc`): passthrough — same as internal.
- **refunds** (pk=`refund_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v2/merchants/me` → `get_merchant()`
- `GET /v2/payments` → `list_payments(location_id, limit)`
- `GET /v2/payments/{payment_id}` → `get_payment(payment_id)`
- `POST /v2/payments` → `create_payment(body)`
- `POST /v2/refunds` → `create_refund(body)`
- `GET /v2/customers` → `list_customers(limit)`
- `GET /v2/customers/{customer_id}` → `get_customer(customer_id)`
- `POST /v2/customers` → `create_customer(body)`
- `GET /v2/catalog/list` → `list_catalog(types)`
- `POST /v2/orders` → `create_order(body)`
- `GET /v2/orders/{order_id}` → `get_order(order_id)`
- `GET /v2/inventory/{catalog_object_id}` → `get_inventory(catalog_object_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 6 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### strava-api

**Base path**: `/api/v3`

**Entities** (from `_store.register(...)` in `strava_data.py`):

- **activities** (pk=`id`)
  - Internal fields (from `_coerce_activities`): `id`, `name`, `type`, `sport_type`, `distance`, `moving_time`, `elapsed_time`, `total_elevation_gain`, `average_speed`, `start_date`, `kudos_count`, `segment_id`
- **segments** (pk=`id`)
  - Internal fields (from `_coerce_segments`): `id`, `name`, `activity_type`, `distance`, `average_grade`, `maximum_grade`, `elevation_high`, `elevation_low`, `climb_category`, `city`, `state`
- **kudoers** (pk=`activity_id`)
  - Internal fields (from `_coerce_kudoers`): `activity_id`, `athlete_id`, `firstname`, `lastname`
- **athlete** (singleton, via `_store.register_document`)
  - Wire fields (from `_athlete_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v3/athlete` → `get_athlete()`
- `GET /api/v3/athlete/activities` → `list_activities(before, after, page, per_page)`
- `GET /api/v3/athletes/{athlete_id}/stats` → `athlete_stats(athlete_id)`
- `GET /api/v3/activities/{activity_id}` → `get_activity(activity_id)`
- `PUT /api/v3/activities/{activity_id}` → `update_activity(activity_id, body)`
- `GET /api/v3/activities/{activity_id}/kudos` → `activity_kudos(activity_id)`
- `GET /api/v3/segments/{segment_id}` → `get_segment(segment_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### stripe-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `stripe_data.py`):

- **customers** (pk=`id`)
  - Internal fields (from `_coerce_customers`): `…raw row…`, `object`, `delinquent`, `balance`, `created`
- **products** (pk=`id`)
  - Internal fields (from `_coerce_products`): `…raw row…`, `object`, `active`, `created`
- **prices** (pk=`id`)
  - Internal fields (from `_coerce_prices`): `…raw row…`, `interval`, `object`, `unit_amount`, `active`, `recurring`, `type`
- **charges** (pk=`id`)
  - Internal fields (from `_coerce_charges`): `…raw row…`, `object`, `amount`, `paid`, `refunded`, `amount_refunded`, `created`
- **invoices** (pk=`id`)
  - Internal fields (from `_coerce_invoices`): `…raw row…`, `object`, `subscription`, `charge`, `amount_due`, `amount_paid`, `created`, `due_date`
- **subscriptions** (pk=`id`)
  - Internal fields (from `_coerce_subscriptions`): `…raw row…`, `object`, `quantity`, `current_period_start`, `current_period_end`, `cancel_at_period_end`, `created`
- **balance** (singleton, via `_store.register_document`)
  - Wire fields (from `_balance_doc`): passthrough — same as internal.
- **payment_intents** (pk=`payment_intent_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **refunds** (pk=`refund_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/customers` → `list_customers(limit, email)`
- `GET /v1/customers/{customer_id}` → `get_customer(customer_id)`
- `POST /v1/customers` → `create_customer(body)`
- `GET /v1/products` → `list_products(limit)`
- `GET /v1/prices` → `list_prices(limit, product)`
- `POST /v1/payment_intents` → `create_payment_intent(body)`
- `GET /v1/payment_intents/{pi_id}` → `get_payment_intent(pi_id)`
- `GET /v1/charges` → `list_charges(limit, customer)`
- `GET /v1/charges/{charge_id}` → `get_charge(charge_id)`
- `POST /v1/charges` → `create_charge(body)`
- `POST /v1/refunds` → `create_refund(body)`
- `GET /v1/invoices` → `list_invoices(limit, customer, status)`
- `GET /v1/invoices/{invoice_id}` → `get_invoice(invoice_id)`
- `POST /v1/invoices` → `create_invoice(body)`
- `GET /v1/subscriptions` → `list_subscriptions(limit, customer, status)`
- `GET /v1/subscriptions/{sub_id}` → `get_subscription(sub_id)`
- `POST /v1/subscriptions` → `create_subscription(body)`
- `GET /v1/balance` → `get_balance()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 8 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### telegram-api

**Base path**: `/bot`

**Entities** (from `_store.register(...)` in `telegram_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `id`, `is_bot`, `first_name`, `last_name`, `username`, `language_code`
- **chats** (pk=`id`)
  - Internal fields (from `_coerce_chats`): `member_count`, `id`, `type`, `title`, `username`, `first_name`, `last_name`, `description`
- **messages** (pk=`message_id`)
  - Internal fields (from `_coerce_messages`): `message_id`, `chat_id`, `from_id`, `text`, `date`, `reply_to_message_id`
  - Wire fields (from `_format_message`): `message_id`, `from`, `chat`, `date`, `text`, `reply_to_message_id`, `id`, `type`
- **members** (pk=`chat_id`)
  - Internal fields (from `_coerce_members`): `chat_id`, `user_id`, `status`
- **bot** (singleton, via `_store.register_document`)
  - Wire fields (from `_bot_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /bot/getMe` → `get_me()`
- `POST /bot/sendMessage` → `send_message(body)`
- `POST /bot/sendPhoto` → `send_photo(body)`
- `POST /bot/editMessageText` → `edit_message_text(body)`
- `POST /bot/deleteMessage` → `delete_message(body)`
- `GET /bot/getUpdates` → `get_updates(offset, limit)`
- `GET /bot/getChat` → `get_chat(chat_id)`
- `GET /bot/getChatMember` → `get_chat_member(chat_id, user_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### ticketmaster-api

**Base path**: `/discovery/v2`

**Entities** (from `_store.register(...)` in `ticketmaster_data.py`):

- **classifications** (pk=`id`)
  - Internal fields (from `_coerce_classifications`): passthrough — no field rewrites (returns rows unchanged).
  - Wire fields (from `_classification_obj`): `segment`, `genre`, `subGenre`, `name`
- **venues** (pk=`id`)
  - Internal fields (from `_coerce_venues`): `latitude`, `longitude`
  - Wire fields (from `_venue_obj`): `id`, `name`, `city`, `state`, `country`, `postalCode`, `address`, `location`, `stateCode`, `countryCode`, `line1`, `latitude`, `longitude`
- **attractions** (pk=`id`)
  - Internal fields (from `_coerce_attractions`): `upcoming_events`
  - Wire fields (from `_attraction_obj`): `id`, `name`, `type`, `upcomingEvents`, `classifications`, `_total`, `segment`, `genre`
- **events** (pk=`id`)
  - Internal fields (from `_coerce_events`): `price_min`, `price_max`
  - Wire fields (from `_event_obj`): `venues`, `attractions`, `id`, `name`, `dates`, `classifications`, `priceRanges`, `_embedded`, `start`, `status`, `dateTime`, `code`, `type`, `currency`, `min`, `max`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /discovery/v2/events` → `search_events(keyword, city, classificationName, startDateTime)`
- `GET /discovery/v2/events/{event_id}` → `get_event(event_id)`
- `GET /discovery/v2/venues` → `search_venues(keyword)`
- `GET /discovery/v2/venues/{venue_id}` → `get_venue(venue_id)`
- `GET /discovery/v2/attractions` → `search_attractions(keyword)`
- `GET /discovery/v2/attractions/{attraction_id}` → `get_attraction(attraction_id)`
- `GET /discovery/v2/classifications` → `list_classifications()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### tmdb-api

**Base path**: `/3`

**Entities** (from `_store.register(...)` in `tmdb_data.py`):

- **genres** (pk=`id`)
  - Internal fields (from `_coerce_genres`): `id`, `name`
- **movies** (pk=`id`)
  - Internal fields (from `_coerce_movies`): `id`, `title`, `original_title`, `overview`, `release_date`, `vote_average`, `vote_count`, `genre_ids`, `popularity`, `original_language`, `media_type`, `adult`
- **people** (pk=`id`)
  - Internal fields (from `_coerce_people`): `id`, `name`, `known_for_department`, `gender`, `popularity`
- **credits** (pk=`movie_id`)
  - Internal fields (from `_coerce_credits`): `movie_id`, `person_id`, `credit_type`, `character`, `job`, `order`
- **tv** (pk=`id`)
  - Internal fields (from `_coerce_tv`): `id`, `name`, `original_name`, `overview`, `first_air_date`, `vote_average`, `vote_count`, `genre_ids`, `popularity`, `number_of_seasons`, `number_of_episodes`, `media_type`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /3/search/movie` → `search_movie(query, page)`
- `GET /3/movie/popular` → `movie_popular(page)`
- `GET /3/movie/{movie_id}` → `get_movie(movie_id)`
- `GET /3/movie/{movie_id}/credits` → `movie_credits(movie_id)`
- `GET /3/tv/{tv_id}` → `get_tv(tv_id)`
- `GET /3/genre/movie/list` → `genre_movie_list()`
- `GET /3/trending/all/week` → `trending_all_week(page)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### trello-api

**Base path**: `/1`

**Entities** (from `_store.register(...)` in `trello_data.py`):

- **members** (pk=`id`)
  - Internal fields (from `_coerce_members`): passthrough — no field rewrites (returns rows unchanged).
- **boards** (pk=`id`)
  - Internal fields (from `_coerce_boards`): `…raw row…`, `closed`, `member_ids`
  - Wire fields (from `_serialize_board`): `id`, `name`, `desc`, `closed`, `idOrganization`, `url`, `idMembers`
- **lists** (pk=`id`)
  - Internal fields (from `_coerce_lists`): `…raw row…`, `pos`, `closed`
  - Wire fields (from `_serialize_list`): `id`, `name`, `idBoard`, `pos`, `closed`
- **cards** (pk=`id`)
  - Internal fields (from `_coerce_cards`): `…raw row…`, `pos`, `closed`, `due`, `member_ids`, `labels`
  - Wire fields (from `_serialize_card`): `id`, `name`, `desc`, `idBoard`, `idList`, `pos`, `due`, `closed`, `idMembers`, `labels`
- **checklists** (pk=`id`)
  - Internal fields (from `_coerce_checklists`): `id`, `name`, `id_card`, `id_board`, `check_items`, `state`, `pos`
  - Wire fields (from `_serialize_checklist`): `id`, `name`, `idCard`, `idBoard`, `checkItems`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /1/members/me` → `get_me()`
- `GET /1/members/me/boards` → `list_my_boards()`
- `GET /1/boards/{board_id}` → `get_board(board_id)`
- `GET /1/boards/{board_id}/lists` → `list_board_lists(board_id)`
- `GET /1/lists/{list_id}/cards` → `list_cards(list_id)`
- `GET /1/cards/{card_id}` → `get_card(card_id)`
- `POST /1/cards` → `create_card(idList, name, desc, due, idMembers)`
- `PUT /1/cards/{card_id}` → `update_card(card_id, name, desc, idList, due, closed, pos)`
- `DELETE /1/cards/{card_id}` → `delete_card(card_id)`
- `GET /1/cards/{card_id}/checklists` → `list_card_checklists(card_id)`
- `POST /1/checklists` → `create_checklist(idCard, name)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### twilio-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `twilio_data.py`):

- **phone_numbers** (pk=`sid`)
  - Internal fields (from `_coerce_phone_numbers`): `…raw row…`, `sms_enabled`, `voice_enabled`, `mms_enabled`, `capabilities_fax`
  - Wire fields (from `_serialize_phone_number`): `sid`, `account_sid`, `phone_number`, `friendly_name`, `iso_country`, `capabilities`, `date_created`, `sms`, `voice`, `mms`, `fax`
- **messages** (pk=`sid`)
  - Internal fields (from `_coerce_messages`): `…raw row…`, `num_segments`, `price`, `error_code`, `date_sent`
  - Wire fields (from `_serialize_message`): `sid`, `account_sid`, `from`, `to`, `body`, `status`, `direction`, `num_segments`, `price`, `price_unit`, `error_code`, `date_sent`, `date_created`, `uri`
- **calls** (pk=`sid`)
  - Internal fields (from `_coerce_calls`): `…raw row…`, `duration`, `price`, `answered_by`, `start_time`, `end_time`
  - Wire fields (from `_serialize_call`): `sid`, `account_sid`, `from`, `to`, `status`, `direction`, `duration`, `price`, `price_unit`, `answered_by`, `start_time`, `end_time`, `date_created`, `uri`
- **account** (singleton, via `_store.register_document`)
  - Wire fields (from `_account_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /2010-04-01/Accounts/{account_sid}/Messages.json` → `list_messages(account_sid, To, From, Status, PageSize)`
- `GET /2010-04-01/Accounts/{account_sid}/Messages/{sid}.json` → `get_message(account_sid, sid)`
- `POST /2010-04-01/Accounts/{account_sid}/Messages.json` → `create_message(account_sid, To, From, Body)`
- `GET /2010-04-01/Accounts/{account_sid}/Calls.json` → `list_calls(account_sid, To, From, Status, PageSize)`
- `POST /2010-04-01/Accounts/{account_sid}/Calls.json` → `create_call(account_sid, To, From)`
- `GET /2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json` → `list_phone_numbers(account_sid, PhoneNumber, PageSize)`
- `GET /v1/PhoneNumbers/{phone_number}` → `lookup(phone_number)`

**Additional data-layer functions** (referenced indirectly): `get_call(sid)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### twitch-api

**Base path**: `/helix`

**Entities** (from `_store.register(...)` in `twitch_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `view_count`
- **games** (pk=`id`)
  - Internal fields (from `_coerce_games`): `id`, `name`, `box_art_url`, `rank`, `viewer_count`
- **channels** (pk=`broadcaster_id`)
  - Internal fields (from `_coerce_channels`): `…raw row…`, `tags`, `follower_count`
- **streams** (pk=`id`)
  - Internal fields (from `_coerce_streams`): `…raw row…`, `viewer_count`, `is_live`, `started_at`
- **clips** (pk=`id`)
  - Internal fields (from `_coerce_clips`): `…raw row…`, `view_count`, `duration`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /helix/users` → `get_users(login, id)`
- `GET /helix/streams` → `get_streams(user_login, user_id, game_id)`
- `GET /helix/channels` → `get_channels(broadcaster_id)`
- `GET /helix/channels/followers` → `get_channel_followers(broadcaster_id)`
- `GET /helix/games/top` → `get_top_games(first)`
- `GET /helix/games` → `get_games(name, id)`
- `GET /helix/clips` → `get_clips(broadcaster_id, game_id, first)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### twitter-api

**Base path**: `/2`

**Entities** (from `_store.register(...)` in `twitter_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `…raw row…`, `verified`, `protected`, `public_metrics`, `followers_count`, `following_count`, `tweet_count`
  - Wire fields (from `_public_user`): passthrough — same as internal.
- **tweets** (pk=`id`)
  - Internal fields (from `_coerce_tweets`): `id`, `author_id`, `text`, `created_at`, `lang`, `reply_to_tweet_id`, `public_metrics`, `like_count`, `retweet_count`, `reply_count`, `quote_count`
- **follows** (pk=`follower_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **likes** (pk=`user_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **retweets** (pk=`user_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /2/users/me` → `get_me()`
- `GET /2/users/by/username/{username}` → `get_user_by_username(username)`
- `GET /2/users/{user_id}` → `get_user(user_id)`
- `GET /2/users/{user_id}/tweets` → `get_user_tweets(user_id, max_results)`
- `GET /2/users/{user_id}/followers` → `get_followers(user_id, max_results)`
- `GET /2/users/{user_id}/following` → `get_following(user_id, max_results)`
- `GET /2/tweets` → `list_tweets(ids, max_results)`
- `GET /2/tweets/search/recent` → `search_recent(query, max_results)`
- `GET /2/tweets/{tweet_id}` → `get_tweet(tweet_id)`
- `POST /2/tweets` → `create_tweet(body)`
- `DELETE /2/tweets/{tweet_id}` → `delete_tweet(tweet_id)`
- `POST /2/users/{user_id}/likes` → `like_tweet(user_id, body)`
- `POST /2/users/{user_id}/retweets` → `retweet(user_id, body)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### typeform-api

**Base path**: `/forms`

**Entities** (from `_store.register(...)` in `typeform_data.py`):

- **forms** (pk=`form_id`)
  - Internal fields (from `_coerce_forms`): `…raw row…`, `is_public`, `response_count`
  - Wire fields (from `_form_obj`): `id`, `title`, `language`, `workspace`, `settings`, `fields`, `_links`, `created_at`, `last_updated_at`, `href`, `is_public`, `display`
- **fields** (pk=`field_id`)
  - Internal fields (from `_coerce_fields`): `…raw row…`, `required`, `choices`, `order`
  - Wire fields (from `_field_obj`): `id`, `title`, `ref`, `type`, `required`, `properties`, `choices`, `label`
- **responses** (pk=`response_id`)
  - Internal fields (from `_coerce_responses`): `…raw row…`, `completed`
- **answers** (pk=`response_id`)
  - Internal fields (from `_coerce_answers`): `…raw row pass-through…`
  - Wire fields (from `_answer_obj`): `field`, `type`, `choice`, `id`, `ref`, `label`, `number`, `email`, `text`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /forms` → `list_forms()`
- `POST /forms` → `create_form(body)`
- `GET /forms/{form_id}` → `get_form(form_id)`
- `PUT /forms/{form_id}` → `update_form(form_id, body)`
- `DELETE /forms/{form_id}` → `delete_form(form_id)`
- `GET /forms/{form_id}/responses` → `list_responses(form_id, completed)`
- `GET /forms/{form_id}/insights/summary` → `insights_summary(form_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### uber-api

**Base path**: `/v1.2`

**Entities** (from `_store.register(...)` in `uber_data.py`):

- **products** (pk=`product_id`)
  - Internal fields (from `_coerce_products`): `…raw row…`, `capacity`, `base_fare`, `cost_per_mile`, `cost_per_minute`, `booking_fee`, `minimum_fare`, `shared`
- **trips** (pk=`request_id`)
  - Internal fields (from `_coerce_trips`): `…raw row…`, `start_latitude`, `start_longitude`, `end_latitude`, `end_longitude`, `distance_miles`, `duration_minutes`, `fare`, `surge_multiplier`, `driver_name`, `vehicle`, `license_plate`, `completed_at`
- **rider** (singleton, via `_store.register_document`)
  - Wire fields (from `_rider_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1.2/products` → `list_products(latitude, longitude)`
- `GET /v1.2/products/{product_id}` → `get_product(product_id)`
- `GET /v1.2/estimates/price` → `price_estimates(start_latitude, start_longitude, end_latitude, end_longitude)`
- `GET /v1.2/estimates/time` → `time_estimates(start_latitude, start_longitude, product_id)`
- `POST /v1.2/requests` → `create_request(body)`
- `GET /v1.2/requests/{request_id}` → `get_request(request_id)`
- `DELETE /v1.2/requests/{request_id}` → `cancel_request(request_id)`
- `GET /v1.2/history` → `get_history(rider_id, limit, offset)`
- `GET /v1.2/me` → `get_me()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 2 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### ups-api

**Base path**: `/api`

**Entities** (from `_store.register(...)` in `ups_data.py`):

- **rates** (pk=`service_code`)
  - Internal fields (from `_coerce_rates`): `service_code`, `service_name`, `origin_zip`, `dest_zip`, `weight_lb`, `currency`, `total_charge`, `transit_days`, `delivery_date`
- **shipments** (pk=`tracking_number`)
  - Internal fields (from `_coerce_shipments`): `tracking_number`, `service_code`, `service_name`, `ship_date`, `origin_zip`, `dest_zip`, `weight_lb`, `currency`, `total_charge`, `label_url`
- **tracking** (pk=`tracking_number`)
  - Internal fields (from `_coerce_tracking`): `tracking_number`, `status_type`, `status_code`, `status_description`, `service_name`, `ship_date`, `scheduled_delivery`, `latest_activity`, `latest_activity_location`, `latest_activity_time`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `POST /api/rating/v1/Rate` → `rate(origin_zip, dest_zip, weight_lb, service_code)`
- `POST /api/shipments/v1/ship` → `create_shipment(origin_zip, dest_zip, weight_lb, service_code)`
- `GET /api/track/v1/details/{tracking_number}` → `track(tracking_number)`

**Additional data-layer functions** (referenced indirectly): `get_rate(origin_zip, dest_zip, weight_lb, service_code)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### vimeo-api

**Base path**: varies

**Entities** (from `_store.register(...)` in `vimeo_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `id`, `name`, `link`, `location`, `bio`, `account`, `created_time`, `websites`
  - Wire fields (from `_serialize_user`): `uri`, `name`, `link`, `location`, `bio`, `account`, `created_time`, `websites`, `metadata`, `connections`, `videos`, `total`
- **videos** (pk=`id`)
  - Internal fields (from `_coerce_videos`): `id`, `user_id`, `name`, `description`, `duration`, `width`, `height`, `privacy`, `status`, `plays`, `likes`, `created_time`, `modified_time`, `link`
  - Wire fields (from `_serialize_video`): `uri`, `name`, `description`, `link`, `duration`, `width`, `height`, `created_time`, `modified_time`, `privacy`, `status`, `stats`, `metadata`, `user`, `view`, `plays`, `connections`, `likes`, `total`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /me` → `get_me()`
- `GET /me/videos` → `get_my_videos(page, per_page)`
- `GET /videos/{video_id}` → `get_video(video_id)`
- `GET /users/{user_id}` → `get_user(user_id)`
- `GET /users/{user_id}/videos` → `get_user_videos(user_id, page, per_page)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 2 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### webflow-api

**Base path**: `/v2`

**Entities** (from `_store.register(...)` in `webflow_data.py`):

- **sites** (pk=`id`)
  - Internal fields (from `_coerce_sites`): `id`, `workspace_id`, `display_name`, `short_name`, `preview_url`, `time_zone`, `created_on`, `last_published`, `custom_domains`
  - Wire fields (from `_serialize_site`): `id`, `workspaceId`, `displayName`, `shortName`, `previewUrl`, `timeZone`, `createdOn`, `lastPublished`, `customDomains`, `url`
- **collections** (pk=`id`)
  - Internal fields (from `_coerce_collections`): `id`, `site_id`, `display_name`, `singular_name`, `slug`, `created_on`, `last_updated`
  - Wire fields (from `_serialize_collection`): `id`, `siteId`, `displayName`, `singularName`, `slug`, `createdOn`, `lastUpdated`
- **items** (pk=`id`)
  - Internal fields (from `_coerce_items`): `id`, `collection_id`, `name`, `slug`, `is_draft`, `is_archived`, `summary`, `created_on`, `last_updated`
  - Wire fields (from `_serialize_item`): `id`, `cmsLocaleId`, `lastPublished`, `lastUpdated`, `createdOn`, `isArchived`, `isDraft`, `fieldData`, `name`, `slug`, `summary`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v2/sites` → `list_sites()`
- `GET /v2/sites/{site_id}` → `get_site(site_id)`
- `GET /v2/sites/{site_id}/collections` → `list_collections(site_id)`
- `GET /v2/collections/{collection_id}/items` → `list_items(collection_id, limit, offset)`
- `POST /v2/collections/{collection_id}/items` → `create_item(collection_id, payload)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### whatsapp-api

**Base path**: `/v17.0`

**Entities** (from `_store.register(...)` in `whatsapp_data.py`):

- **contacts** (pk=`wa_id`)
  - Internal fields (from `_coerce_contacts`): `…raw row…`, `opted_in`
- **conversations** (pk=`conversation_id`)
  - Internal fields (from `_coerce_conversations`): `…raw row…`, `within_24h_window`
- **templates** (pk=`name`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **messages** (pk=`message_id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **business** (singleton, via `_store.register_document`)
  - Wire fields (from `_business_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v17.0/business` → `get_business()`
- `GET /v17.0/contacts` → `list_contacts(opted_in_only)`
- `GET /v17.0/contacts/{wa_id}` → `get_contact(wa_id)`
- `GET /v17.0/message_templates` → `list_templates(status)`
- `GET /v17.0/message_templates/{name}` → `get_template(name)`
- `GET /v17.0/conversations` → `list_conversations(wa_id)`
- `GET /v17.0/messages` → `list_messages(conversation_id, wa_id, limit)`
- `POST /v17.0/messages` → `send_message(body)`
- `POST /v17.0/messages/status` → `mark_read(body)`

**Additional data-layer functions** (referenced indirectly): `send_text(to_wa_id, body)`, `send_template(to_wa_id, template_name, components)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### woocommerce-api

**Base path**: `/wp-json/wc/v3`

**Entities** (from `_store.register(...)` in `woocommerce_data.py`):

- **products** (pk=`id`)
  - Internal fields (from `_coerce_products`): `id`, `name`, `slug`, `sku`, `type`, `status`, `price`, `regular_price`, `sale_price`, `on_sale`, `stock_quantity`, `stock_status`, `manage_stock`, `categories`, `description`, `date_created`
  - Wire fields (from `_serialize_product`): same as internal
- **customers** (pk=`id`)
  - Internal fields (from `_coerce_customers`): `id`, `first_name`, `last_name`, `email`, `username`, `role`, `billing_city`, `billing_country`, `is_paying_customer`, `date_created`
  - Wire fields (from `_serialize_customer`): `id`, `first_name`, `last_name`, `email`, `username`, `role`, `billing`, `is_paying_customer`, `date_created`, `city`, `country`
- **orders** (pk=`id`)
  - Internal fields (from `_coerce_orders`): `id`, `number`, `customer_id`, `status`, `currency`, `total`, `subtotal`, `total_tax`, `payment_method`, `payment_method_title`, `billing_first_name`, `billing_last_name`, `billing_email`, `date_created`
  - Wire fields (from `_serialize_order`): `id`, `number`, `customer_id`, `status`, `currency`, `total`, `subtotal`, `total_tax`, `payment_method`, `payment_method_title`, `billing`, `date_created`, `first_name`, `last_name`, `email`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /wp-json/wc/v3/products` → `list_products(search, sku, status, page, per_page)`
- `GET /wp-json/wc/v3/products/{product_id}` → `get_product(product_id)`
- `GET /wp-json/wc/v3/orders` → `list_orders(customer, status, page, per_page)`
- `GET /wp-json/wc/v3/orders/{order_id}` → `get_order(order_id)`
- `POST /wp-json/wc/v3/orders` → `create_order(body)`
- `GET /wp-json/wc/v3/customers` → `list_customers(search, email, page, per_page)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### wordpress-api

**Base path**: `/wp-json/wp/v2`

**Entities** (from `_store.register(...)` in `wordpress_data.py`):

- **posts** (pk=`id`)
  - Internal fields (from `_coerce_posts`): `id`, `title`, `slug`, `status`, `author`, `content`, `excerpt`, `categories`, `tags`, `comment_status`, `date`, `modified`, `type`
- **pages** (pk=`id`)
  - Internal fields (from `_coerce_pages`): `id`, `title`, `slug`, `status`, `author`, `content`, `date`, `modified`, `parent`, `type`
- **categories** (pk=`id`)
  - Internal fields (from `_coerce_categories`): `id`, `name`, `slug`, `description`, `parent`, `count`, `taxonomy`
- **tags** (pk=`id`)
  - Internal fields (from `_coerce_tags`): `id`, `name`, `slug`, `description`, `count`, `taxonomy`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `id`, `post`, `author_name`, `author_email`, `content`, `status`, `date`, `parent`
- **media** (pk=`id`)
  - Internal fields (from `_coerce_media`): `id`, `title`, `slug`, `media_type`, `mime_type`, `source_url`, `alt_text`, `author`, `post`, `date`, `type`
- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `id`, `name`, `slug`, `description`, `url`, `roles`, `avatar_urls`, `96`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /wp-json/wp/v2/posts` → `list_posts(status, author, search, categories, per_page)`
- `POST /wp-json/wp/v2/posts` → `create_post(body)`
- `GET /wp-json/wp/v2/posts/{post_id}` → `get_post(post_id)`
- `PUT /wp-json/wp/v2/posts/{post_id}` → `update_post(post_id, body)`
- `DELETE /wp-json/wp/v2/posts/{post_id}` → `delete_post(post_id)`
- `GET /wp-json/wp/v2/pages` → `list_pages(status, per_page)`
- `GET /wp-json/wp/v2/categories` → `list_categories()`
- `GET /wp-json/wp/v2/tags` → `list_tags()`
- `GET /wp-json/wp/v2/comments` → `list_comments(post, status)`
- `POST /wp-json/wp/v2/comments` → `create_comment(body)`
- `GET /wp-json/wp/v2/media` → `list_media()`
- `GET /wp-json/wp/v2/users` → `list_users()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 7 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### xero-api

**Base path**: `/api.xro/2.0`

**Entities** (from `_store.register(...)` in `xero_data.py`):

- **contacts** (pk=`ContactID`)
  - Internal fields (from `_coerce_contacts`): `ContactID`, `Name`, `FirstName`, `LastName`, `EmailAddress`, `IsCustomer`, `IsSupplier`, `ContactStatus`, `AccountNumber`
  - Wire fields (from `_serialize_contact`): same as internal
- **accounts** (pk=`AccountID`)
  - Internal fields (from `_coerce_accounts`): `AccountID`, `Code`, `Name`, `Type`, `TaxType`, `Status`, `Description`, `EnablePaymentsToAccount`
  - Wire fields (from `_serialize_account`): passthrough — same as internal.
- **invoices** (pk=`InvoiceID`)
  - Internal fields (from `_coerce_invoices`): `InvoiceID`, `InvoiceNumber`, `Type`, `contact_id`, `contact_name`, `Date`, `DueDate`, `Status`, `LineAmountTypes`, `SubTotal`, `TotalTax`, `Total`, `AmountDue`, `AmountPaid`, `CurrencyCode`, `Reference`
  - Wire fields (from `_serialize_invoice`): `InvoiceID`, `InvoiceNumber`, `Type`, `Contact`, `Date`, `DueDate`, `Status`, `LineAmountTypes`, `SubTotal`, `TotalTax`, `Total`, `AmountDue`, `AmountPaid`, `CurrencyCode`, `Reference`, `ContactID`, `Name`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api.xro/2.0/Invoices` → `list_invoices(Status, Type)`
- `GET /api.xro/2.0/Invoices/{invoice_id}` → `get_invoice(invoice_id)`
- `POST /api.xro/2.0/Invoices` → `create_invoice(body)`
- `GET /api.xro/2.0/Contacts` → `list_contacts()`
- `GET /api.xro/2.0/Accounts` → `list_accounts()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### yelp-api

**Base path**: `/v3`

**Entities** (from `_store.register(...)` in `yelp_data.py`):

- **businesses** (pk=`id`)
  - Internal fields (from `_coerce_businesses`): `id`, `alias`, `name`, `rating`, `price`, `review_count`, `is_closed`, `phone`, `image_url`, `categories`, `coordinates`, `location`, `latitude`, `longitude`, `address1`, `city`, `state`, `display_address`, `title`
- **reviews** (pk=`id`)
  - Internal fields (from `_coerce_reviews`): `id`, `business_id`, `rating`, `text`, `time_created`, `user`, `name`
- **categories** (pk=`alias`)
  - Internal fields (from `_coerce_categories`): `alias`, `title`, `parent_aliases`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v3/businesses/search` → `search_businesses(term, location, categories, price, sort_by, limit, offset)`
- `GET /v3/businesses/{business_id}` → `get_business(business_id)`
- `GET /v3/businesses/{business_id}/reviews` → `get_business_reviews(business_id)`
- `GET /v3/categories` → `list_categories()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### youtube-api

**Base path**: `/youtube`

**Entities** (from `_store.register(...)` in `youtube_data.py`):

- **videos** (pk=`id`)
  - Internal fields (from `_coerce_videos`): `id`, `snippet`, `contentDetails`, `statistics`, `status`, `publishedAt`, `channelId`, `title`, `description`, `thumbnails`, `channelTitle`, `tags`, `categoryId`, `liveBroadcastContent`, `defaultLanguage`, `defaultAudioLanguage`, `duration`, `dimension`, `definition`, `caption`, `licensedContent`, `projection`, `viewCount`, `likeCount`, `dislikeCount`, `commentCount`, `uploadStatus`, `privacyStatus`, `publishAt`, `license`, `embeddable`, `publicStatsViewable`, `madeForKids`, `default`, `medium`, `high`, `maxres`, `url`, `width`, `height`
- **playlists** (pk=`id`)
  - Internal fields (from `_coerce_playlists`): `id`, `snippet`, `status`, `contentDetails`, `publishedAt`, `channelId`, `title`, `description`, `thumbnails`, `channelTitle`, `privacyStatus`, `itemCount`, `default`, `medium`, `high`, `url`, `width`, `height`
- **playlist_items** (pk=`id`)
  - Internal fields (from `_coerce_playlist_items`): `id`, `snippet`, `contentDetails`, `publishedAt`, `channelId`, `title`, `playlistId`, `position`, `resourceId`, `thumbnails`, `channelTitle`, `videoId`, `videoPublishedAt`, `kind`, `default`, `medium`, `high`, `url`, `width`, `height`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `id`, `videoId`, `channelId`, `parentId`, `snippet`, `moderationStatus`, `authorDisplayName`, `authorChannelId`, `textDisplay`, `textOriginal`, `likeCount`, `publishedAt`, `updatedAt`, `value`
- **captions** (pk=`id`)
  - Internal fields (from `_coerce_captions`): `id`, `snippet`, `videoId`, `lastUpdated`, `trackKind`, `language`, `name`, `isDraft`
- **channel** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **video_categories** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **channel_sections** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **analytics** (singleton, via `_store.register_document`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /youtube/v3/channels` → `list_channels(id, part)`
- `GET /youtube/v3/videos` → `list_videos(id, channelId, part, maxResults, pageToken)`
- `PUT /youtube/v3/videos` → `update_video(body, part)`
- `DELETE /youtube/v3/videos` → `delete_video(id)`
- `GET /youtube/v3/playlists` → `list_playlists(id, channelId, part, maxResults, pageToken)`
- `POST /youtube/v3/playlists` → `create_playlist(body, part)`
- `PUT /youtube/v3/playlists` → `update_playlist(body, part)`
- `DELETE /youtube/v3/playlists` → `delete_playlist(id)`
- `GET /youtube/v3/playlistItems` → `list_playlist_items(playlistId, part, maxResults, pageToken)`
- `POST /youtube/v3/playlistItems` → `insert_playlist_item(body, part)`
- `PUT /youtube/v3/playlistItems` → `update_playlist_item(body, part)`
- `DELETE /youtube/v3/playlistItems` → `delete_playlist_item(id)`
- `GET /youtube/v3/commentThreads` → `list_comment_threads(videoId, channelId, part, maxResults, moderationStatus, pageToken)`
- `POST /youtube/v3/commentThreads` → `insert_comment_thread(body, part)`
- `GET /youtube/v3/comments` → `list_comments(parentId, part, maxResults, pageToken)`
- `POST /youtube/v3/comments` → `insert_comment(body, part)`
- `PUT /youtube/v3/comments` → `update_comment(body, part)`
- `DELETE /youtube/v3/comments` → `delete_comment(id)`
- `POST /youtube/v3/comments/setModerationStatus` → `set_moderation_status(id, moderationStatus)`
- `GET /youtube/v3/search` → `search(q, channelId, part, order, maxResults, pageToken, type)`
- `GET /youtube/v3/videoCategories` → `list_video_categories(regionCode, part)`
- `GET /youtube/v3/captions` → `list_captions(videoId, part)`
- `GET /youtube/v3/channelSections` → `list_channel_sections(channelId, part)`
- `GET /youtube/analytics/v2/reports` → `get_analytics(ids, metrics, dimensions, filters, startDate, endDate)`

**Additional data-layer functions** (referenced indirectly): `get_channel(channel_id)`, `get_video(video_id)`, `get_playlist(playlist_id)`, `get_comment_thread(comment_id)`, `search_videos(channel_id, q, order, max_results, offset)`, `get_channel_analytics()`, `get_video_analytics(video_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 5 keyed table(s) + 4 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### zendesk-api

**Base path**: `/api/v2`

**Entities** (from `_store.register(...)` in `zendesk_data.py`):

- **users** (pk=`id`)
  - Internal fields (from `_coerce_users`): `id`, `name`, `email`, `role`, `organization_id`, `active`, `created_at`
- **organizations** (pk=`id`)
  - Fields: no dedicated coercer/serializer detected; rows stored as raw dicts (inspect handler for wire shape).
- **tickets** (pk=`id`)
  - Internal fields (from `_coerce_tickets`): `id`, `subject`, `description`, `status`, `priority`, `type`, `requester_id`, `assignee_id`, `organization_id`, `tags`, `created_at`, `updated_at`
- **comments** (pk=`id`)
  - Internal fields (from `_coerce_comments`): `id`, `ticket_id`, `author_id`, `body`, `public`, `created_at`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /api/v2/tickets` → `list_tickets(status, priority, assignee_id)`
- `GET /api/v2/tickets/{ticket_id}` → `get_ticket(ticket_id)`
- `POST /api/v2/tickets` → `create_ticket(body)`
- `PUT /api/v2/tickets/{ticket_id}` → `update_ticket(ticket_id, body)`
- `GET /api/v2/tickets/{ticket_id}/comments` → `list_comments(ticket_id)`
- `POST /api/v2/tickets/{ticket_id}/comments` → `create_comment(ticket_id, body)`
- `GET /api/v2/users` → `list_users(role)`
- `GET /api/v2/users/{user_id}` → `get_user(user_id)`
- `GET /api/v2/organizations` → `list_organizations()`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### zillow-api

**Base path**: `/v1`

**Entities** (from `_store.register(...)` in `zillow_data.py`):

- **properties** (pk=`zpid`)
  - Internal fields (from `_coerce_properties`): `…raw row…`, `zpid`, `latitude`, `longitude`, `bedrooms`, `bathrooms`, `living_area_sqft`, `lot_size_sqft`, `year_built`, `list_price`, `zestimate`, `rent_zestimate`, `days_on_zillow`
- **price_history** (pk=`zpid`)
  - Internal fields (from `_coerce_price_history`): `…raw row…`, `zpid`, `price`, `price_per_sqft`
- **agents** (pk=`agent_id`)
  - Internal fields (from `_coerce_agents`): `…raw row…`, `active_listings`, `sold_last_12mo`, `rating`, `reviews`
- **saved_searches** (pk=`search_id`)
  - Internal fields (from `_coerce_saved_searches`): `…raw row…`, `min_price`, `max_price`, `min_beds`, `min_baths`, `city`

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v1/properties/search` → `search_properties(city, state, zipcode, min_price, max_price, min_beds, min_baths, home_type, status, limit, offset, sort_by, sort_order)`
- `GET /v1/properties/{zpid}` → `get_property(zpid)`
- `GET /v1/properties/{zpid}/zestimate` → `get_zestimate(zpid)`
- `GET /v1/properties/{zpid}/price-history` → `get_price_history(zpid)`
- `GET /v1/agents` → `list_agents(city, state)`
- `GET /v1/agents/{agent_id}` → `get_agent(agent_id)`
- `GET /v1/users/{user_id}/saved-searches` → `list_saved_searches(user_id)`
- `POST /v1/users/{user_id}/saved-searches` → `create_saved_search(user_id, body)`
- `DELETE /v1/saved-searches/{search_id}` → `delete_saved_search(search_id)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 4 keyed table(s) + 0 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---

### zoom-api

**Base path**: `/v2`

**Entities** (from `_store.register(...)` in `zoom_data.py`):

- **meetings** (pk=`id`)
  - Internal fields (from `_coerce_meetings`): `…raw row…`, `id`, `type`, `duration`, `agenda`
  - Wire fields (from `_serialize_meeting`): `id`, `host_id`, `topic`, `type`, `status`, `start_time`, `duration`, `timezone`, `agenda`, `join_url`, `created_at`
- **recordings** (pk=`id`)
  - Internal fields (from `_coerce_recordings`): `…raw row…`, `meeting_id`, `file_size`
- **registrants** (pk=`id`)
  - Internal fields (from `_coerce_registrants`): `…raw row…`, `meeting_id`, `join_time`
- **user** (singleton, via `_store.register_document`)
  - Wire fields (from `_user_doc`): passthrough — same as internal.

**Endpoints** (from `server.py`):

- `GET /health` → `health()`
- `GET /v2/users/me` → `get_me()`
- `GET /v2/users/{user_id}/meetings` → `list_meetings(user_id, type, page_size)`
- `POST /v2/users/{user_id}/meetings` → `create_meeting(user_id, body)`
- `GET /v2/meetings/{meeting_id}` → `get_meeting(meeting_id)`
- `PATCH /v2/meetings/{meeting_id}` → `update_meeting(meeting_id, body)`
- `DELETE /v2/meetings/{meeting_id}` → `delete_meeting(meeting_id)`
- `GET /v2/meetings/{meeting_id}/recordings` → `get_recordings(meeting_id)`
- `GET /v2/meetings/{meeting_id}/registrants` → `list_registrants(meeting_id, status)`

**Relationships**: see entity primary keys; cross-references are by matching key names across tables (e.g., `*_id` foreign keys).

**Notes**:
- 3 keyed table(s) + 1 singleton document(s).
- Standard fleet conventions apply: in-memory `_store`, request-tracking middleware, admin-plane endpoints (`/__admin/*`), error envelopes `{error: "<message>"}` unless overridden.

---
