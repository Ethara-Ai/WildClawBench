# Tools — Layla McBride

## Tool Usage

### General Agent Capabilities
- **Browser**: Web research — agricultural science, grants, policy, conferences, recipes, gifts.
- **Wide Research**: Deep-dive info gathering — agricultural science, funding bodies, policy, traditional farming methods.
- **Documents**: Create/edit research papers, grant proposals, lecture notes, reports, personal letters.

---

### Connected Services

#### Google Ecosystem
All Google services: layla.mcbride@Finthesiss.ai
- **Gmail** (`gmail-api`): Professional + personal email. Research, grants (WAADA/WAITA), university admin, journals, personal. Never send to government officials or funding bodies without explicit confirmation.
- **Google Calendar** (`google-calendar-api`): Primary scheduling. Cross-reference before suggesting availability. Colour: blue=lectures, green=research, orange=family, red=deadlines. Shared with Marcus.
- **Google Drive** (`google-drive-api`): Key folders: `WAADA-Cassava/`, `WAITA-EACRI/`, `Teaching/`, `Farmer-Training/`, `Personal/`. Shared with Derek, Amina, ESFES. Never share grant proposals or unpublished data outside existing access without confirmation.
- **Google Classroom** (`google-classroom-api`): Undergrad Crop Science at NNU. Mon/Wed/Fri. Do not auto-grade or modify marks.
- **Google Maps** (`google-maps-api`): NNU commute (45 min), Udi LGA field sites, Nsukka LGA coops. Check traffic — roads bad after rain.
- **Google Analytics** (`google-analytics-api`): Read-only. NNU Crop Sci website — farmer downloads, workshop traffic. Monthly review.

#### Communication & Collaboration
- **WhatsApp** (`whatsapp-api`): 555-8801. Primary channel — research team, family groups (McBride, Mitchell), cooperative updates, ESFES. Read/draft. Never send to new contacts without confirmation.
- **Slack** (`slack-api`): Workspaces: `nnu-crop-science`, `waita-eacri`. Channels: `#cassava-data`, `#yam-improvement`, `#field-updates`, `#publications`. DMs with Derek daily.
- **Zoom** (`zoom-api`): Bi-weekly Amina calls (Wed 3 PM WAT). Virtual conferences. Confirm before scheduling over teaching/family time.
- **Microsoft Teams** (`microsoft-teams-api`): NNU institutional. Monitor Dean's office, promotion committee. Do not initiate meetings without explicit request.
- **Telegram** (`telegram-api`): Read-only. `NigeriaAgPolicy`, `WAADA-Updates`, `EnuguFarmersNetwork`. Flag cassava policy, biofort funding, Enugu ag budget.
- **Discord** (`discord-api`): Read-only. `PlantSci-Africa` — `#field-methods`, `#grant-writing`, `#r-stats-help`.
- **Twilio** (`twilio-api`): SMS to ~340 farmers in Nsukka LGA. English/Pidgin. Drafts require review. ESFES budget.
- **Intercom** (`intercom-api`): ESFES portal queries. Friday review. Crop advice/pricing escalated to Layla.

#### Email Infrastructure
- **SendGrid** (`sendgrid-api`): Bulk email for cooperative comms — newsletters, workshop announcements, planting calendars. ESFES mailing list (~580 contacts). All bulk sends require approval.
- **Mailgun** (`mailgun-api`): Transactional email backend — student notifications, server job alerts, form confirmations. Runs automatically behind the scenes.

#### Research, Science & Knowledge
- **Notion** (`notion-api`): Research knowledge base. DBs: `Literature Review`, `Grant Tracker`, `Publication Pipeline`, `Student Progress`. Shared with Derek (protocols), Amina (publications). Promotion portfolio tool.
- **Obsidian** (`obsidian-api`): Offline-first vault — critical during power outages. Vault: `Layla-Research/`. Field observations, farmer interviews, reading notes, grant ideas. Syncs to Drive when online. Do not reorganise folders without asking.
- **Airtable** (`airtable-api`): Structured research data. Bases: `Field-Trial-Udi` (plots, yields, soil), `Farmer-Cooperative-Registry` (340 members), `Equipment-Inventory`. Derek edits `Field-Trial-Udi`. Never delete data rows.
- **GitHub** (`github-api`): `layla-mcbride/cassava-biofort-analysis` (R scripts, shared with Derek). `nnu-crop-sci/farmer-training-materials`. Keep commits clean, plain English.
- **GitLab** (`gitlab-api`): NNU-hosted (`gitlab.nnu.edu.ng`). Mirrors GitHub for compliance. Group: `l.mcbride`.
- **Confluence** (`confluence-api`): WAITA-EACRI wiki. Spaces: `Yam Improvement Programme`, `Joint Publications`. Amina co-admins. Update notes after Wednesday calls.
- **NASA** (`nasa-api`): POWER API — solar radiation, temp, precipitation at Udi LGA coordinates. NDVI satellite imagery for crop health. Seasonal: rain onset, drought indicators.
- **OpenLibrary** (`openlibrary-api`): Book discovery/reading list. Fav authors: Adichie, Hosseini, Aboulela. Also ag science texts.
- **OpenWeather** (`openweather-api`): Daily weather for Enugu, Udi LGA, Nsukka LGA. Monitor rainfall, temp, humidity, harmattan. Flag rain on field days or dry spells >10 days in growing season.

#### Project Management & Workflow
- **Trello** (`trello-api`): Personal research boards. `Cassava Paper` (Data Cleaning→Analysis→Draft→Review→Submit), `WAITA Grant Extension` (Pre-proposal→Submission), `Teaching Prep` (weekly lectures). Create, move, archive cards. Check board state before suggesting tasks.
- **Asana** (`asana-api`): ESFES Farmer Training workflow. Shared with ESFES coordinator and field officers. Workshop logistics, materials, registration, surveys. Layla owns "Content & Facilitation." Monitor overdue tasks near quarterly workshop dates.
- **Linear** (`linear-api`): NNU IT ticket tracking. Equipment requests, Wi-Fi issues, server access. Submit and check status only.
- **Monday.com** (`monday-api`): WAITA-EACRI cross-institutional board. Shared with Amina (Nairobi). Joint publications, bi-weekly agendas, data sharing deadlines. Update after Wednesday calls.

#### Documents, Design & Storage
- **Dropbox** (`dropbox-api`): International collaborators (UK, Ghana). Folder: `International-Collabs/`. Backup during outages. Read-only Amina EACRI datasets.
- **Box** (`box-api`): NNU institutional. Folder: `CropSci/McBride-L/`. Contracts, outputs, grants, ethics. Do not delete.
- **DocuSign** (`docusign-api`): Research agreements. Active: WAADA amendment, ESFES MOU, WAITA-EACRI data sharing. Never initiate without explicit approval.
- **Figma** (`figma-api`): Posters and visuals using NNU templates. Current: `Cassava-Biofort-Poster-Oct2026`, `Farmer-Training-Infographics`. Do not overhaul without asking.
- **Contentful** (`contentful-api`): ESFES resource library CMS. Bilingual (English + Pidgin). Quarterly. Publish, update, track downloads.

#### Calendar, Events & Scheduling
- **Calendly** (`calendly-api`): Student office hours (Tue/Thu). 2 PhD + 1 MSc self-schedule supervision. Also international collaborator calls. Auto-creates Google Calendar events. Block field days and family commitments proactively.
- **Eventbrite** (`eventbrite-api`): ESFES workshops and NNU public seminars registration. Track counts, send reminders, download attendee lists. Also monitors West Africa ag conference listings.
- **Ticketmaster** (`ticketmaster-api`): Personal — Houston events during Dec–Jan visit. Family-friendly events, concerts. Purchases above ₦15,000 require confirmation.

#### Travel, Transport & Shipping
- **Amadeus** (`amadeus-api`): Flights. Annual Houston (Dec–Jan), Ibadan Oct 12, Nairobi. Window seat, morning departures. All bookings require confirmation.
- **Airbnb** (`airbnb-api`): Conference stays. Entire place, Wi-Fi, quiet, mid-range. All bookings require confirmation.
- **Uber** (`uber-api`): Ride-hailing — Enugu, Abuja, Lagos, Ibadan. Not daily commute.
- **FedEx** (`fedex-api`): International — research samples (cold-chain), documents to WAADA/Accra, Houston packages.
- **UPS** (`ups-api`): Houston parcels, lab supply imports, books. Track and customs.
- **Shippo** (`shippo-api`): Multi-carrier rate comparison for research shipments to EACRI Nairobi.

#### Finance & Payments
- **Stripe** (`stripe-api`): International payments — conference fees, journal APCs, software. Track/download receipts for NNU reimbursement.
- **PayPal** (`paypal-api`): Transfers — monthly Karen support (₦30K USD), conference fees, purchases. All outgoing >₦15,000 require confirmation.
- **Plaid** (`plaid-api`): Read-only. First Bank Nigeria — categories, patterns, alerts. No transactions.
- **Square** (`square-api`): POS at ESFES workshops. Booklets ₦500, seed kits ₦1,200, calendars ₦300. Dashboard review.

#### Shopping, Food & Marketplace
- **Instacart** (`instacart-api`): Houston only (Dec–Jan). Items unavailable in Enugu. Also deliveries to Karen.
- **Amazon** (`amazon-seller-api`): Read-only. ESFES pilot — cassava flour/yam chips for diaspora. Write requires Layla + ESFES coordinator.
- **Etsy** (`etsy-api`): Read-only browsing for handmade gifts. Does not sell.
- **BigCommerce** (`bigcommerce-api`): ESFES input store. Read-only admin. Write requires ESFES coordinator.
- **WooCommerce** (`woocommerce-api`): NNU shop — manuals ₦2,500, proceedings ₦4,000, notebooks ₦1,500. Flag low stock.
- **DoorDash** (`doordash-api`): Houston only (Dec–Jan). Food delivery on international card.

#### Social Media & Content
- **Read-only platforms** (does NOT post, comment, or engage on any of these):
  - **Instagram** (`instagram-api`): Follows food bloggers, gardening, ag extension. Search feed on request.
  - **Twitter/X** (`twitter-api`): Monitors #AgriTwitter, #CropScience, #FoodSecurity, WAADA/WAITA. Draft tweets only if explicitly requested.
  - **Reddit** (`reddit-api`): r/agriculture, r/plantscience, r/AcademicPhilosophy, r/Gardening, r/Nigeria.
  - **Pinterest** (`pinterest-api`): Private boards — gardening, décor, recipes. Read on request.
  - **Twitch** (`twitch-api`): Marcus's account, observer access. Search ag conference streams.
- **LinkedIn** (`linkedin-api`): Professional profile. Updates 1–2x/year. All posts require explicit review before publishing.
- **YouTube** (`youtube-api`): Subscribed to IITA, FAO, CGIAR, cooking, R tutorials. Search/summarise. No own channel.
- **Vimeo** (`vimeo-api`): Hosts ESFES workshop recordings (private). Upload quarterly, manage access links via WhatsApp/SMS. Also NNU lectures.
- **Spotify** (`spotify-api`): Playlists: `Focus`, `Drive to Campus`, `Evening Wind-Down`. Podcasts: The Food Programme, Gastropod, How I Built This.
- **TMDB** (`tmdb-api`): Movie/show lookup for Saturday family nights. No account — lookup only.

#### Farmer Cooperative Outreach
- **Mailchimp** (`mailchimp-api`): Quarterly newsletter to ~580 ESFES contacts. Segment by crop type/location. All sends require review.
- **HubSpot** (`hubspot-api`): ESFES CRM — member profiles, attendance, engagement. Also WAADA/WAITA/ministry contacts. Do not delete member profiles.
- **ActiveCampaign** (`activecampaign-api`): NNU student email automation — confirmations, reminders, "Research Spotlight" series.
- **Klaviyo** (`klaviyo-api`): Granular cooperative campaigns (e.g., pest advisories to Udi corridor only). All sends require approval.
- **Freshdesk** (`freshdesk-api`): ESFES support desk. Weekly escalated ticket review. Auto-FAQs don't reach Layla.
- **Typeform** (`typeform-api`): Surveys — post-workshop, annual satisfaction, course evaluation. Never alter a live survey collecting responses.

#### Analytics & Data Intelligence
- **Algolia** (`algolia-api`): Search engine for ESFES and NNU sites. Surface "top failed searches" monthly.
- **Segment** (`segment-api`): Integrates HubSpot+Mailchimp+Typeform+Contentful. Unified farmer engagement view for research.
- **Amplitude** (`amplitude-api`): ESFES mobile PWA analytics — active users, feature usage, retention. Monthly review.
- **PostHog** (`posthog-api`): NNU website behavior — traffic, navigation, registration drop-offs. Grant report data.
- **Mixpanel** (`mixpanel-api`): Cooperative app event analytics. Cross-refs Airtable for digital literacy research.

#### University & Institutional (NNU)
- **BambooHR** (`bamboohr-api`): HR portal — leave, salary, benefits, pay slips. Promotion timeline tracking.
- **Greenhouse** (`greenhouse-api`): Hiring — reviewing field research assistant apps for Cassava Trial Year 3.
- **ServiceNow** (`servicenow-api`): Facility requests, IT tickets, procurement. Escalate politely on deadlines.
- **Okta** (`okta-api`): SSO. If locked out, auto-trigger ServiceNow ticket.
- **Zendesk** (`zendesk-api`): IT support tickets. Check existing tickets before creating new ones.
- **Outlook** (`outlook-api`): l.mcbride@nnu.edu.ng. Official admin — promotion committee, faculty minutes, HR. Summarise weekly.
- **WordPress** (`wordpress-api`): Dept blog + WooCommerce shop. Quarterly field updates, research highlights.
- **Webflow** (`webflow-api`): ESFES public site. Content editor only (not design). Workshops, banners, success stories.

#### Research Computing & Infrastructure
- **Sentry** (`sentry-api`): Error tracking for R/Python on NNU server. Alerts to Layla+Derek. Check first on failures, summarise in plain English.
- **Datadog** (`datadog-api`): Cluster monitoring — uptime, CPU/memory, storage (73%). Alert if >85% or unreachable >30 min.
- **PagerDuty** (`pagerduty-api`): Critical: irrigation sensor failures, server downtime, cold-storage excursions. Layla+Derek daytime, Derek-only overnight. Triage before interrupting family time.
- **Cloudflare** (`cloudflare-api`): CDN/security for NNU+ESFES sites. Route config to NNU IT.
- **Kubernetes** (`kubernetes-api`): Namespace: `crop-sci-mcbride`. R/RStudio, Jupyter, batch jobs. Monitor pods, retrieve outputs.

#### Marcus's Business (McBride & Associates)
All access below is **read-only** for household financial planning. Layla does NOT modify records, contact clients, or execute trades.
- **QuickBooks** (`quickbooks-api`): Monthly revenue, invoices, expenses. Sunday reviews. Write actions require Marcus's confirmation.
- **Xero** (`xero-api`): Secondary accounting for tax prep (Mar–Apr). Cross-refs QuickBooks.
- **Salesforce** (`salesforce-api`): Marcus's client CRM — contracts, prospects, timelines.
- **Jira** (`jira-api`): Marcus's project tracking. Checks deadline-heavy weeks for household logistics.
- **Gusto** (`gusto-api`): Payroll for 4 employees. Co-reviews for budget planning.
- **Crypto** — read-only portfolio monitoring across three platforms, no trading authority:
  - **Coinbase** (`coinbase-api`): BTC/ETH (~₦400K).
  - **Binance** (`binance-api`): Altcoins/stablecoins. Monthly check.
  - **Kraken** (`kraken-api`): USDT-to-Naira conversions. Flag transactions >$500.
- **Alpaca** (`alpaca-api`): Household investments — ARM mutual fund (₦1.5M), US equities managed with Robert. Quarterly review.

#### Health, Fitness & Home
- **MyFitnessPal** (`myfitnesspal-api`): Yoga 3x/week, daily walks. Consistency patterns only — no calorie pressure.
- **Strava** (`strava-api`): 5:30 AM walks, 30 min daily. Streak competition with Brianna. Don't make fitness stressful.
- **Ring** (`ring-api`): Home security, Independence Layout. Filter cats/deliveries/gardener before escalating.
- **Zillow** (`zillow-api`): Houston property browsing near Karen. Search/save only — no offers without family discussion.

#### Lifestyle & Discovery
- **Yelp** (`yelp-api`): Houston restaurant research during Dec–Jan visits only. Middle Eastern, Southern comfort, kid-friendly. Not useful in Enugu.

---

### Not Connected
- **Field data collection sensors** — Udi LGA irrigation/soil/weather loggers. Manual transfer to Airtable. Full API integration is Year 3 goal.
- **NNU internal grading system** — legacy portal, no API. Manual grade entry each semester.
- **Hospital/medical records** — Enugu Teaching Hospital, paper-based. Strictly private, handled in person.
- **First Bank Nigeria app** — direct transactions. Plaid provides read-only monitoring only.
- **ARM Investments portal** — direct fund management. Alpaca provides read-only monitoring only.
