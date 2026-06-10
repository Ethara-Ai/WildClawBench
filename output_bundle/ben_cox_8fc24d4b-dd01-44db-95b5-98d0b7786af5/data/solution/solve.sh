#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
import os

airtable_api_url = os.environ.get('AIRTABLE_API_URL', 'http://airtable-api:8032').rstrip('/')
docusign_api_url = os.environ.get('DOCUSIGN_API_URL', 'http://docusign-api:8053').rstrip('/')
gmail_api_url = os.environ.get('GMAIL_API_URL', 'http://gmail-api:8017').rstrip('/')
outlook_api_url = os.environ.get('OUTLOOK_API_URL', 'http://outlook-api:8087').rstrip('/')
quickbooks_api_url = os.environ.get('QUICKBOOKS_API_URL', 'http://quickbooks-api:8007').rstrip('/')
xero_api_url = os.environ.get('XERO_API_URL', 'http://xero-api:8088').rstrip('/')

print('Solution not yet implemented -- populate with API calls from golden trajectory.')
PY
