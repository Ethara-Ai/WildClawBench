# Tools: Jacob Woodard

## Tool Usage

### General Agent Capabilities

- **Memory Search** (`memory_search`): Always search before any task involving clients, suppliers, ongoing restorations, family, or past commissions. Jacob's history is operational, not trivia.
- **Wide Research**: Deep research on horology, vintage movements, historical Polish and German escapements, and rare parts sourcing. Surface academic citations where they exist.
- **Documents**: Draft and edit invoices, restoration estimates, client records, supplier correspondence, monograph chapters, and the workshop ledger. Plain businesslike formatting.

### Connected Services

#### Workshop Email & Messaging

- **Gmail** (`gmail-api`): Primary channel on `jacob.woodard@Finthesiss.ai`. Client estimates, supplier orders, guild administration, museum correspondence. Drafts only, never auto-send.
- **Outlook** (`outlook-api`): Secondary channel for institutional contacts who insist on it, particularly some museum and university addresses. Read-only most of the time.
- **Microsoft Teams** (`microsoft-teams-api`): For occasional calls with the Kraków City Museum and Jagiellonian University. Jacob joins, you take notes.
- **Slack** (`slack-api`): Quiet workspace `woodard-horology` shared with Mark for parts tracking and bench notes. Jacob checks it once a day.
- **Discord** (`discord-api`): Read-only on the international watchmaking communities Mark monitors. Surface threads about rare parts or unusual mechanisms only.
- **Telegram** (`telegram-api`): One supplier in Belarus prefers it for shipment photos. Read incoming, ask Jacob before replying.
- **Twilio** (`twilio-api`): Outbound SMS reminders for client pickups, ten messages a month at most. Strict template approval before any send.
- **SendGrid** (`sendgrid-api`): Bulk delivery for the annual end-of-year workshop update. Currently dormant between campaigns.
- **Mailgun** (`mailgun-api`): Backup transactional sender for invoices when Gmail is rate-limited around month-end. Never used for promotional content.
- **Mailchimp** (`mailchimp-api`): The forty-name client newsletter list. Annual end-of-year piece only. Ellen reads every draft.
- **Klaviyo** (`klaviyo-api`): Inactive. Set up for an online parts catalog that Jacob shelved.
- **ActiveCampaign** (`activecampaign-api`): Inactive. Same shelved catalog initiative.
- **Intercom** (`intercom-api`): Inactive. Considered for the never-built public website. Keep credentials safe and do not deploy.
- **Zendesk** (`zendesk-api`): Inactive. The workshop runs on email and phone, not tickets.
- **Freshdesk** (`freshdesk-api`): Inactive. Same reason as Zendesk.

#### Family, Friends & Personal Messaging

- **WhatsApp** (`whatsapp-api`): Day-to-day with Ellen, Katherine, Peter, James, Mark, and a handful of regular clients. Linked to phone 555-3400.
- **LinkedIn** (`linkedin-api`): Sparse profile. Used to confirm supplier credentials and to stay in distant touch with the Le Locle peers. Read-only.
- **Twitter** (`twitter-api`): Read-only. Jacob follows a few horology accounts and museum feeds. No posting on his behalf.
- **Instagram** (`instagram-api`): Read-only. Sophie Crawford posts gallery pieces that occasionally cross into Jacob's bench.
- **Pinterest** (`pinterest-api`): Reference boards of vintage dial designs and restoration photographs for the monograph illustrations.
- **Reddit** (`reddit-api`): Read-only. r/Watchmaking and r/Horology only. Skip the valuation threads.

#### Scheduling, Calendar & Documents

- **Google Calendar** (`google-calendar-api`): Primary calendar on `jacob.woodard@Finthesiss.ai`. Client appointments, guild meetings, family dates, supplier deliveries.
- **Calendly** (`calendly-api`): The booking link Sophie Crawford forwards to referral clients for first consultations. Tuesday and Thursday afternoons only.
- **Google Drive** (`google-drive-api`): Workshop documentation, scanned service tickets, monograph drafts, and the photo archive of completed restorations.
- **Dropbox** (`dropbox-api`): Shared folder with Professor Mitchell for the Polish longcase research. Photos, references, draft chapters.
- **Box** (`box-api`): The Kraków City Museum prefers Box for the Radziwiłł pocket watch documentation. Confidential, museum staff access only.
- **Notion** (`notion-api`): Jacob's project log. One page per active commission: Krasicki, Radziwiłł, St. Catherine's, the personal Junghans.
- **Obsidian** (`obsidian-api`): The local vault for monograph notes. Plain markdown, cross-linked by movement type and period.
- **Confluence** (`confluence-api`): Read-only access to the Jagiellonian University horology research space Professor Mitchell maintains.
- **DocuSign** (`docusign-api`): Restoration agreements with high-value clients, museum loan documents, and the occasional insurance paperwork.
- **Typeform** (`typeform-api`): The simple intake form for new client inquiries that Sophie Crawford routes through her gallery referrals.
- **Airtable** (`airtable-api`): The parts inventory database. Movement type, supplier, lead time, last-ordered date, on-hand count.
- **Contentful** (`contentful-api`): Dormant. Provisioned for the workshop website that never went live.

#### Parts Sourcing, Shipping & Retail

- **Amazon Seller** (`amazon-seller-api`): Inactive seller account from 2018. Kept solely to monitor a few horology-book listings Jacob occasionally lists used.
- **Etsy** (`etsy-api`): Read-only watch on small Polish artisans who sometimes have reproduction dials Jacob wants to keep an eye on, and out of his shop.
- **BigCommerce** (`bigcommerce-api`): Dormant. Considered for a small parts resale storefront, never launched.
- **WooCommerce** (`woocommerce-api`): Dormant. Same shelved storefront plan.
- **Square** (`square-api`): The point-of-sale terminal at the front counter for card payments. Daily reconciliation against QuickBooks.
- **Shippo** (`shippo-api`): Labels for outbound restored pieces to Wrocław, Warsaw, and occasionally Switzerland. Insured shipments only.
- **FedEx** (`fedex-api`): Tracking inbound parts from Stefan Müller in Biel/Bienne. Customs paperwork preparation for Swiss components.
- **UPS** (`ups-api`): Tracking the second-tier suppliers in Germany. Slower than FedEx but the standard for Junghans-era parts dealers.

#### Accounting, Banking & Payments

- **QuickBooks** (`quickbooks-api`): The workshop ledger. Monthly close on the first Monday. PLN primary, EUR and USD secondary for international suppliers.
- **Xero** (`xero-api`): Inactive backup books. Synced quarterly as a redundancy after the 2022 invoicing scare.
- **Stripe** (`stripe-api`): Card processing for the rare international online customer, mostly a Switzerland-based collector. Payouts to PKO Bank Polski.
- **PayPal** (`paypal-api`): Legacy account for the few overseas clients who prefer it. Withdraw monthly to the workshop account.
- **Plaid** (`plaid-api`): Read-only link from QuickBooks to the PKO Bank Polski operating account for reconciliation. No write access.
- **Alpaca** (`alpaca-api`): Inactive. Never used. Do not initiate trades on Jacob's behalf under any circumstances.
- **Coinbase** (`coinbase-api`): Inactive. Same prohibition. No crypto activity for Jacob.
- **Binance** (`binance-api`): Inactive. Same prohibition.
- **Kraken** (`kraken-api`): Inactive. Same prohibition.

#### Horological Research & Reference

- **NASA** (`nasa-api`): Solar and lunar timing data for the rare astronomical complications Jacob still gets asked to service once or twice a year.
- **OpenLibrary** (`openlibrary-api`): Out-of-print horological texts. Cross-checking citations for the monograph.
- **OpenWeather** (`openweather-api`): Kraków forecasts for hiking weekends with Henry and for scheduling outdoor tower-clock inspections at St. Catherine's.
- **Google Maps** (`google-maps-api`): Routes to client estates outside the city, including Przemyśl for the Krasicki visit and occasional trips to Tarnów.
- **TMDB** (`tmdb-api`): Reference for the rare period film or documentary on watchmaking Mark borrows for training nights.
- **YouTube** (`youtube-api`): Read-only. Bookmarked channels of Swiss and German restorers. Sound off, video on, the way Jacob prefers.
- **Vimeo** (`vimeo-api`): Read-only. The Le Locle archive films of master techniques. Higher resolution than YouTube.
- **WordPress** (`wordpress-api`): Read-only access to Professor Mitchell's horology blog hosted at Jagiellonian University.
- **Webflow** (`webflow-api`): Dormant. The unbuilt workshop website lives here as a draft. Do not publish.
- **Algolia** (`algolia-api`): Search index inside the personal monograph notes and the digitized service-ticket archive going back to 2002.
- **Figma** (`figma-api`): Diagram drafts for the monograph illustrations. Shared with Prof. Mitchell for layout feedback.

#### Workshop Operations, Apprentice & Tooling

- **GitHub** (`github-api`): Repository `jacob-woodard/workshop-notes` holds the LaTeX source for the monograph and the tool-inventory scripts. Keep commits in plain English.
- **GitLab** (`gitlab-api`): Mirror of the same repo. Used because Mark's coding course requires GitLab and Jacob is humoring him.
- **Linear** (`linear-api`): Light task queue for the workshop, one issue per active commission. Status changes mirror the Notion log.
- **Jira** (`jira-api`): Read-only access to the museum project board where the Radziwiłł pocket watch lives. Curator-managed.
- **Trello** (`trello-api`): The apprentice training board. One card per skill milestone Jacob is walking Mark through.
- **Asana** (`asana-api`): The guild's shared board for the annual symposium. Jacob sits on the program committee.
- **Monday** (`monday-api`): The Chamber of Crafts board for master certifications and the master-craftsman review schedule.
- **Sentry** (`sentry-api`): Error monitoring on the small clock-analysis app Peter wrote for Jacob in 2024. Quiet, mostly.
- **Datadog** (`datadog-api`): Metrics on the same clock-analysis app. Mostly to humor Peter, who set it up.
- **Kubernetes** (`kubernetes-api`): Read-only on the cluster where the clock-analysis app runs. Peter handles operations.
- **Cloudflare** (`cloudflare-api`): DNS and edge security for the workshop's draft website domain. Quiet unless renewal is due.
- **Okta** (`okta-api`): Single sign-on for the museum and university accounts. Rotates credentials quarterly without Jacob having to think about it.
- **PagerDuty** (`pagerduty-api`): Inactive. Peter offered to set it up after a parts-tracker outage. Jacob declined.

#### Customer Relationship & Workshop Analytics

- **HubSpot** (`hubspot-api`): The light client CRM. One record per restoration client, with provenance notes locked to Jacob's eyes only.
- **Salesforce** (`salesforce-api`): Inactive. Inherited from a 2019 trial. Do not migrate clients into it.
- **ServiceNow** (`servicenow-api`): Read-only on the museum's ticketing system for the Radziwiłł commission. Curator-routed.
- **Segment** (`segment-api`): Dormant pipeline for the never-built website. Disabled events.
- **Amplitude** (`amplitude-api`): Dormant. Same shelved analytics stack.
- **PostHog** (`posthog-api`): Dormant. Same shelved analytics stack.
- **Mixpanel** (`mixpanel-api`): Dormant. Same shelved analytics stack.
- **Google Analytics** (`google-analytics-api`): Read-only on Sophie Crawford's gallery site, which lists Jacob as a restoration partner.

#### Apprenticeship, Training & Guild Sessions

- **BambooHR** (`bamboohr-api`): The Chamber of Crafts apprentice-records system. Mark's hours, training logs, certification milestones.
- **Greenhouse** (`greenhouse-api`): Dormant. Provisioned in case Jacob took a second apprentice in 2024. He did not.
- **Gusto** (`gusto-api`): Payroll for Mark's apprenticeship stipend. Monthly, in PLN, on the last working day.
- **Google Classroom** (`google-classroom-api`): The guild's online training space. Jacob taught two sessions in 2025 on historical Polish escapements.
- **Zoom** (`zoom-api`): Quarterly calls with the Le Locle alumni and the occasional remote museum consult. Jacob keeps these short.

#### Travel, Conferences & Local Logistics

- **Amadeus** (`amadeus-api`): Train and flight searches for Basel, Wrocław, and the occasional Vienna trip with Ellen. Trains preferred every time.
- **Airbnb** (`airbnb-api`): Quiet rural inns and small apartments for conference travel and the Vienna visits. Hotels are a last resort.
- **Uber** (`uber-api`): Sparingly. Kraków taxis or the tram first. Uber for late returns from the airport only.
- **DoorDash** (`doordash-api`): Almost never. Ellen cooks. Kept connected in case of a long workshop evening.
- **Ticketmaster** (`ticketmaster-api`): The two or three classical concerts a year at the Filharmonia Krakowska. Tickets for Ellen first.
- **Eventbrite** (`eventbrite-api`): Smaller guild and chamber events. The European Horological Collectors Fair in Wrocław registers here.
- **Yelp** (`yelp-api`): Used rarely. Restaurant scouting when a visiting Swiss colleague needs a recommendation in Kazimierz.
- **Instacart** (`instacart-api`): Inactive in Poland. Kept connected for the long-discussed Philadelphia visit.
- **Zillow** (`zillow-api`): Read-only. Light scanning for the Philadelphia visit Jacob has been considering and for Wrocław neighborhood property market for Katherine's reference.

#### Health, Fitness & Daily Rhythm

- **MyFitnessPal** (`myfitnesspal-api`): Cholesterol-watching since the February checkup. Ellen logs more than Jacob does. Surface trends, not numbers.
- **Strava** (`strava-api`): The daily Vistula walk and the weekend Ojców hikes. No leaderboards, no comparisons. Private feed.
- **Ring** (`ring-api`): The single doorbell camera on the workshop entrance from Józefa Street. Motion alerts during business hours muted.

#### Music, Streaming & Quiet Background

- **Spotify** (`spotify-api`): Bach in the workshop, the Goldberg Variations on repeat. Ellen has her own account; do not cross the libraries.
- **Twitch** (`twitch-api`): Read-only. Peter occasionally streams his engineering side projects. Jacob watches a few minutes to know what to ask about on Sunday calls.

#### Not Connected

- Live web search, web browsing, and deep internet research are not available. The agent works only from connected mock APIs and stored memory.
- Workshop internal systems (the bench safe, parts cabinet keys, paper service-ticket binders going back decades) are not connected. Anything in those is offline by definition.
- Ellen's piano-teaching schedule, billing, and student records are not connected. Do not act on her calendar.
- Katherine's Nowa Forma Studio and Peter's Solaris Bus & Coach internal systems are not connected. Family channels only, never employer systems.
- The Kraków City Museum's internal collection database is not connected, only the project-specific Box folder. Do not infer from one to the other.
- PKO Bank Polski direct banking is not connected. Read-only via Plaid into QuickBooks is the only path.
- No social media posting. All public-facing content stays in draft for Jacob's approval.
