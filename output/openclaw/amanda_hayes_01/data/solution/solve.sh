#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import os

google_analytics_api_url = os.environ.get('GOOGLE_ANALYTICS_API_URL', 'http://google-analytics-api:8068').rstrip('/')
google_calendar_api_url = os.environ.get('GOOGLE_CALENDAR_API_URL', 'http://google-calendar-api:8016').rstrip('/')
google_classroom_api_url = os.environ.get('GOOGLE_CLASSROOM_API_URL', 'http://google-classroom-api:8002').rstrip('/')
google_drive_api_url = os.environ.get('GOOGLE_DRIVE_API_URL', 'http://google-drive-api:8018').rstrip('/')
google_maps_api_url = os.environ.get('GOOGLE_MAPS_API_URL', 'http://google-maps-api:8033').rstrip('/')
linear_api_url = os.environ.get('LINEAR_API_URL', 'http://linear-api:8004').rstrip('/')
notion_api_url = os.environ.get('NOTION_API_URL', 'http://notion-api:8010').rstrip('/')
plaid_api_url = os.environ.get('PLAID_API_URL', 'http://plaid-api:8022').rstrip('/')
quickbooks_api_url = os.environ.get('QUICKBOOKS_API_URL', 'http://quickbooks-api:8007').rstrip('/')
trello_api_url = os.environ.get('TRELLO_API_URL', 'http://trello-api:8030').rstrip('/')

print('Solution not yet implemented -- populate with API calls from golden trajectory.')
PY
