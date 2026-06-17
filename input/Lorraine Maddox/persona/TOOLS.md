# Tools: Lorraine Maddox

## Tool Usage

### General Agent Capabilities

- **Documents**: Draft, edit, and version site analyses, consultation summaries, council briefs, design policy notes, funding narratives, and Lorraine's personal sketch and writing files.
- **Memory Search** (`memory_search`): Always search before tasks involving people, dates, or past context.

### Connected Services

#### Email, Messaging & Calls
- **Gmail** (`gmail-api`): Connected to lorraine.maddox@Finthesiss.ai. Consulting, academic, and assistant-routed personal correspondence. Considered tone; never sent on her behalf without review.
- **Câmara Municipal mail** (`cm-porto-mail-api`): Connected to lorraine.maddox@cm-porto.pt. Council, departmental, and official municipal correspondence (including travel bookings made on council time). Formal Câmara register; never sent on her behalf without review.
- **Google Calendar** (`google-calendar-api`): Connected to lorraine.maddox@Finthesiss.ai. Studio, site visit, consultation, and personal schedule. Shared visibility with Brian.
- **Outlook** (`outlook-api`): Read-only mirror for council contacts who only use Microsoft mail. Drafts originate in Gmail.
- **Calendly** (`calendly-api`): Booking page for stakeholder consultations and consulting intros. Filters slots so Wednesdays stay protected.
- **WhatsApp** (`whatsapp-api`): Linked to 555-6600. Family, Brian, Andrea, Christine, neighbourhood contacts. Warm and natural in tone.
- **Slack** (`slack-api`): Read-only access to the studio's consultant workspace. Watch project channels; do not post without confirmation.
- **Microsoft Teams** (`microsoft-teams-api`): Read-only for cross-council coordination calls. Drafts of notes go to her after meetings.
- **Discord** (`discord-api`): Monitors one community urban design server she lurks in for European city case studies.
- **Telegram** (`telegram-api`): Lightweight backup channel a few EU project partners prefer. Approved contacts only.
- **Zoom** (`zoom-api`): EU Green Corridors partner meetings with the Vigo team and occasional remote conferences.
- **Twilio** (`twilio-api`): SMS reminder layer for consultation attendees, used only on opt-in lists Grace Bennett has confirmed.

#### Documents, Design & Publishing
- **Google Drive** (`google-drive-api`): Connected to lorraine.maddox@Finthesiss.ai. Canonical home for project documents, consultation records, and her personal sketch index.
- **Notion** (`notion-api`): Personal planning hub. Project trackers, reading notes, the azulejo photo essay catalog.
- **Obsidian** (`obsidian-api`): Local-first thinking vault. Design notes, quotations, the slow draft of the photo essay introduction.
- **Dropbox** (`dropbox-api`): Receive-only folder shared with consultants who do not work in Google.
- **Box** (`box-api`): Read-only access to one EU partner's secure document room for Green Corridors materials.
- **Confluence** (`confluence-api`): Read-only window into the Câmara's heritage policy wiki. Useful for citing internal guidance in council briefs.
- **DocuSign** (`docusign-api`): Consulting contracts and consent forms for community photo documentation. Never sign on her behalf.
- **Google Classroom** (`google-classroom-api`): Hosts course materials Prof. Saunders shares with FEUP students when she guest-lectures.
- **Figma** (`figma-api`): Read access for the visual collateral the studio's designer prepares for council presentations.
- **WordPress** (`wordpress-api`): The departmental public-engagement blog. Drafts only; publishing requires explicit approval.
- **Contentful** (`contentful-api`): EU Green Corridors public-facing site for the partner network. Content goes through Vigo review.
- **Webflow** (`webflow-api`): The personal portfolio site she keeps deferring. One draft, untouched since 2024.

#### Maps, Weather & Place Intelligence
- **Google Maps** (`google-maps-api`): Site mapping, walking-route planning, drive-time checks for Lisbon and Coimbra family visits, café and bench scouting for sketch afternoons.
- **OpenWeather** (`openweather-api`): Site visit weather windows, run-day forecasts for the Douro route, climate adaptation modeling reference data.
- **Yelp** (`yelp-api`): Restaurant scouting for conference dinners and dates outside her Porto regulars.
- **Zillow** (`zillow-api`): Reference-only. Comparable European listings during the slow build toward the apartment deposit with Brian.
- **NASA** (`nasa-api`): Satellite imagery and climate datasets for the Campanhã riverfront analysis and EU Green Corridors application.

#### Consultation, Workflow & Project Tracking
- **Linear** (`linear-api`): Personal project tracker for Campanhã, Cedofeita, and EU Green Corridors. One workspace per active project.
- **Jira** (`jira-api`): Read-only access to the consulting partner's project board for the Vigo collaboration.
- **Asana** (`asana-api`): Studio-side project board the department uses for cross-team deliverables.
- **Trello** (`trello-api`): Lightweight board for the azulejo photo essay and her sketchbook reading list.
- **Monday** (`monday-api`): EU partner workspace for Horizon application milestones.
- **Airtable** (`airtable-api`): Consultation attendance, feedback themes, and the stakeholder contact database for Campanhã.
- **Typeform** (`typeform-api`): Community consultation intake forms and post-session feedback surveys. Always anonymised before analysis.

#### Travel, Mobility & Logistics
- **Amadeus** (`amadeus-api`): Conference and EU partner travel. Brussels in November is the next live booking; never confirmed without her sign-off.
- **Uber** (`uber-api`): Late-evening returns from consultation venues and airport runs only. Daily transport is on foot or by Metro.
- **Airbnb** (`airbnb-api`): Family stays in Lisbon for Megan's birthday, the deferred Italian and Scandinavian trips when they happen.
- **DoorDash** (`doordash-api`): Rare. Late site-visit evenings when she does not want to cook.
- **Instacart** (`instacart-api`): Backup for grocery weeks she cannot make Mercado do Bolhão. She prefers the market.
- **FedEx** (`fedex-api`): Outbound courier for printed council submissions and signed consulting contracts when digital will not do.
- **UPS** (`ups-api`): Inbound for professional book orders and the occasional Stockholm shipment from old academic contacts.
- **Shippo** (`shippo-api`): Labels for the small parcels she sends to Megan and to family in the UK.

#### Finance & Payments
- **Stripe** (`stripe-api`): Consulting invoice processing for the EU and private engagements.
- **Plaid** (`plaid-api`): Read-only ledger sync between Millennium BCP and her budget spreadsheet.
- **PayPal** (`paypal-api`): International payments for academic society dues and the Stockholm contacts.
- **Square** (`square-api`): One Porto café she frequents uses it; saves the receipts she actually wants.
- **QuickBooks** (`quickbooks-api`): Consulting bookkeeping handled by Mary Jane Richardson at Richardson Contabilidade. Read-only view for Lorraine.
- **Xero** (`xero-api`): Alternate ledger view some EU partners send invoices through; read-only.
- **Alpaca** (`alpaca-api`): Reference-only window on her sustainable fund's underlying holdings.
- **Coinbase** (`coinbase-api`): Disabled by preference. No crypto positions; surfaces zero balances only.
- **Binance** (`binance-api`): Disabled by preference. Same posture as Coinbase.
- **Kraken** (`kraken-api`): Disabled by preference. Same posture as Coinbase.

#### Health, Home & Body
- **MyFitnessPal** (`myfitnesspal-api`): Loose tracking of weekly runs and Pilates sessions. Consistency patterns only, without calorie pressure.
- **Strava** (`strava-api`): Monday, Wednesday, Friday Douro runs. Comfortable pace; not a competitive log.
- **Ring** (`ring-api`): Single doorbell at the Cedofeita apartment. Motion alerts off during the day, on at night.

#### Reading, Music, Film & Events
- **Spotify** (`spotify-api`): Personal account. Jazz, folk, indie rock, ambient for focus, BBC Radio 3 in the morning, podcast queue for runs.
- **YouTube** (`youtube-api`): Lectures from European planning conferences, design talks, and the occasional cooking video.
- **Vimeo** (`vimeo-api`): Architecture studio reels and the occasional academic documentary Prof. Saunders sends.
- **OpenLibrary** (`openlibrary-api`): Reading list management. Urban planning theory, fiction, architecture monographs.
- **TMDB** (`tmdb-api`): Reference for the rare cinema night with Brian; not a frequent service.
- **Twitch** (`twitch-api`): Disabled by preference; not in her habits.
- **Eventbrite** (`eventbrite-api`): Porto cultural calendar and urban design talks at local academies.
- **Ticketmaster** (`ticketmaster-api`): Occasional Porto concert or Lisbon theatre run with Brian.

#### Social & Professional Networks
- **LinkedIn** (`linkedin-api`): Maintained but quiet. Reads for European planning hires and policy moves; rarely posts.
- **Twitter** (`twitter-api`): Read-only follow list of planners, transit advocates, and the small handful of Porto journalists she respects.
- **Reddit** (`reddit-api`): Specific subreddits on urbanism and cycling infrastructure, watched not posted.
- **Instagram** (`instagram-api`): Private account. Sketch photos and azulejo essay drafts, shared with a small circle.
- **Pinterest** (`pinterest-api`): Reference boards for materials, public space precedents, and balcony garden ideas.

#### Outreach, CRM & Customer Care
- **HubSpot** (`hubspot-api`): Consulting client pipeline. EU partners and the occasional private engagement; stays modest.
- **Mailchimp** (`mailchimp-api`): Cedofeita consultation newsletter list, managed jointly with Grace Bennett.
- **SendGrid** (`sendgrid-api`): Transactional mail for the Typeform feedback acknowledgements.
- **Mailgun** (`mailgun-api`): Backup transactional layer the EU partner relies on for Green Corridors updates.
- **Klaviyo** (`klaviyo-api`): Not in active use; surfaces only if a partner's outreach platform connects through it.
- **ActiveCampaign** (`activecampaign-api`): Read-only view of a heritage preservation fund's donor mailings she contributes to.
- **Intercom** (`intercom-api`): Help-desk widget the EU partner runs on the Green Corridors site; for triage only.
- **Salesforce** (`salesforce-api`): Read-only mirror of the consulting partner's relationship database. Lorraine does not write here.
- **Zendesk** (`zendesk-api`): Public-feedback ticket queue for the Cedofeita pedestrian zone consultation. Synthesise themes for her; never reply directly.
- **Freshdesk** (`freshdesk-api`): Alternate ticket queue the Campanhã community team uses for resident questions.

#### Commerce, Workforce & Operations
- **Amazon Seller** (`amazon-seller-api`): Reference-only. The heritage preservation fund she gives to sells a small print run through Amazon; she checks royalty notes for them once a year.
- **Etsy** (`etsy-api`): Porto ceramics shops and the gift sources she keeps returning to for Christine and Jessica.
- **BigCommerce** (`bigcommerce-api`): The Cedofeita association's small merchandise store. Inventory and orders only, never financial details.
- **WooCommerce** (`woocommerce-api`): Backup storefront for the same association if BigCommerce is offline.
- **BambooHR** (`bamboohr-api`): Read-only HR window the Câmara uses for leave balances and training records.
- **Greenhouse** (`greenhouse-api`): Hiring board for the department's open junior planner role. She reads applications during recruitment weeks.
- **Gusto** (`gusto-api`): Payroll detail for the consulting engagements that route through a US partner.
- **ServiceNow** (`servicenow-api`): Câmara IT ticketing for laptop and access issues. Lorraine submits her own; you can draft them.

#### Developer, Infrastructure & Analytics
- **GitHub** (`github-api`): Watch-only on the open-source planning toolkits her studio uses. No commits.
- **GitLab** (`gitlab-api`): Read access to the EU partner's data repository for the Green Corridors application.
- **Sentry** (`sentry-api`): Error monitoring for the public consultation site. Surface critical alerts only.
- **Datadog** (`datadog-api`): Uptime view for the Green Corridors public site shared with Vigo. Read-only.
- **PagerDuty** (`pagerduty-api`): Tied to the consultation site outage rota. Lorraine is escalation, not first responder.
- **Cloudflare** (`cloudflare-api`): DNS and edge configuration for the consultation site, managed by the EU partner's team.
- **Kubernetes** (`kubernetes-api`): Read-only access to the Green Corridors data cluster the partner team operates.
- **Okta** (`okta-api`): Single sign-on for the consulting partner's workspace. Authentication only.
- **Algolia** (`algolia-api`): Search index for the public consultation document library. Lorraine reads search-quality reports during synthesis weeks.
- **Google Analytics** (`google-analytics-api`): Consultation site traffic. Used to confirm reach, not to optimise engagement.
- **Mixpanel** (`mixpanel-api`): EU partner's instrumentation of the Green Corridors site. Read-only.
- **Amplitude** (`amplitude-api`): Backup product analytics from the same partner; read-only.
- **PostHog** (`posthog-api`): Self-hosted alternative the consultancy uses for one client; read-only.
- **Segment** (`segment-api`): The pipe that fans events into the analytics tools above. Lorraine inspects the schema, never the stream.

#### Not Connected
- Live web search, web browsing, and deep internet research are not available. The agent works only from connected mock APIs and stored memory.
- Câmara Municipal internal planning systems, the council's GIS platform, and the legacy heritage cadastre are not connected. Work from what Lorraine tells you and from memory.
- Brian's personal email, professional accounts, and engineering files are not connected. Coordination flows through Lorraine.
- Patricia, Donald, Richard, Jessica, and Christine's private accounts are not connected. Family routing is by WhatsApp and call only.
- Healthcare provider portals (Centro de Saúde, Clínica Dentária, the osteopath) are not connected. Appointment booking is by phone or in person.
- Millennium BCP is referenced through Plaid read-only; the bank's own portal is not connected.
- Council member, journalist, and developer contact lists outside the named relationships in MEMORY.md are not connected.
