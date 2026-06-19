# Tools: Margaret Farmer

## Tool Usage

### General Agent Capabilities

- **Documents**: Drafts gallery emails, commission contracts, exhibition press copy, invoices, kiln-log entries, collector mailing-list announcements, and Webflow portfolio updates. All external documents go through Margaret's review before sending.
- **Memory Search** (`memory_search`): Always search before tasks involving people, dates, or past context. Studio notebook entries, kiln results, glaze experiments, and gallery history live in memory and must be checked first.

### Connected Services

#### Studio & Document Management

- **Google Drive** (`google-drive-api`): Primary cloud storage. Portfolio masters, kiln logs, exhibition contracts, scanned glaze recipe pages, gallery PDFs.
- **Dropbox** (`dropbox-api`): Read-only. International galleries share installation photos and exhibition catalogue PDFs here.
- **Box** (`box-api`): Read-only. One-off file shares from museum and large institutional partners during loan paperwork.
- **Notion** (`notion-api`): Master studio notebook. Kiln firing log, glaze test database, exhibition production trackers, gallery and collector CRM.
- **Obsidian** (`obsidian-api`): Personal commonplace book. Reading notes from Yanagi, Wendell Berry, and the ceramics journals. Margaret maintains this herself.
- **Airtable** (`airtable-api`): "Seasonal Table" 30-piece production tracker. Pieces mapped by season, form, glaze, and firing status.
- **DocuSign** (`docusign-api`): Gallery consignment agreements, commission contracts, museum loan paperwork. She reviews every document.
- **Figma** (`figma-api`): Daniel's design files for exhibition posters, business cards, and catalogue layouts. Read-only collaboration.
- **Contentful** (`contentful-api`): CMS layer for the portfolio site. Add new work, edit exhibition pages, manage the press archive.
- **Webflow** (`webflow-api`): Live portfolio site at margaretfarmer-ceramics.jp, built by Daniel. Edit copy and exhibition pages; publish only after Margaret's review.
- **WordPress** (`wordpress-api`): The Higashiyama Craft Collective blog. Margaret occasionally cross-posts kiln stories with the collective's permission.
- **Confluence** (`confluence-api`): Collective shared documentation. Kiln scheduling protocol, safety procedures, member resources.
- **Monday** (`monday-api`): Galerie Terre uses Monday to coordinate international show logistics. Read-only mirror for the October Paris show.

#### Email, Messaging & Voice

- **Gmail** (`gmail-api`): Primary email at margaret.farmer@Finthesiss.ai. Galleries, museums, accountant, international correspondence.
- **Outlook** (`outlook-api`): Read-only. Akiko at Shibui Gallery uses Outlook; forwarded museum threads occasionally arrive here.
- **WhatsApp** (`whatsapp-api`): Not preferred. Reserved for the rare international contact who insists.
- **Telegram** (`telegram-api`): Not actively used. Available if Jean-Luc's Paris show logistics require a group thread.
- **Slack** (`slack-api`): Read-only. Galerie Terre runs international show coordination here; Margaret reads but does not post.
- **Microsoft Teams** (`microsoft-teams-api`): Kyoto Craft Museum invites her to commission status calls via Teams.
- **Discord** (`discord-api`): Not active. Available if a ceramics community channel becomes relevant later.
- **Twilio** (`twilio-api`): Backup SMS for material delivery confirmations and gallery courier alerts.
- **SendGrid** (`sendgrid-api`): Transactional email for portfolio site contact-form responses and exhibition RSVP confirmations.
- **Mailgun** (`mailgun-api`): Backup sender for the collector newsletter when SendGrid hits send limits during exhibition launches.
- **Mailchimp** (`mailchimp-api`): Collector and gallery mailing list, approximately 240 addresses. Open studio announcements twice yearly, exhibition invitations.
- **Klaviyo** (`klaviyo-api`): Available if she moves to a more segmented collector list. Currently overkill for her volume.
- **ActiveCampaign** (`activecampaign-api`): Alternative to Mailchimp. Quoted in a Daniel proposal last year but not adopted.
- **Intercom** (`intercom-api`): Available if the portfolio site adds live chat for collector inquiries. Currently disabled.

#### Calendar, Events & Travel

- **Google Calendar** (`google-calendar-api`): Master calendar. Studio days, kiln firings, gallery deadlines, mother's Wednesday call, Sunday off-day.
- **Calendly** (`calendly-api`): Studio visit booking link for serious collectors and curators. Margaret approves each booking before confirming.
- **Eventbrite** (`eventbrite-api`): Open studio RSVPs and craft fair ticketing for the spring and autumn collective open studios.
- **Ticketmaster** (`ticketmaster-api`): Rare. Used once for a Tokyo concert with Ethan; otherwise dormant.
- **Google Maps** (`google-maps-api`): Studio routing, gallery directions, the temple-path morning walk, Shigaraki trip planning.
- **Uber** (`uber-api`): Backup transport for moving fired work to Shibui Gallery on opening days when the regular courier is booked.
- **Airbnb** (`airbnb-api`): Shigaraki visits to Toshio (twice yearly), Mashiko Pottery Fair lodging, the postponed Portland trip.
- **Amadeus** (`amadeus-api`): International flights. The October Paris trip for the Galerie Terre opening, plus the postponed Portland family visit.
- **DoorDash** (`doordash-api`): Late-night delivery during firing weeks when she cannot leave the kiln. Higashiyama radius only.
- **Instacart** (`instacart-api`): Limited availability in Kyoto. She shops the Higashiyama markets herself.
- **Yelp** (`yelp-api`): Gallery-area restaurant research when meeting curators or collectors at unfamiliar venues.
- **OpenWeather** (`openweather-api`): Anagama wood-fire planning. Humidity affects glaze behaviour, and she watches the forecast before loading.

#### Galleries, Sales & Storefront

- **Etsy** (`etsy-api`): Not used. Margaret does not sell through Etsy; gallery channels and direct studio sales only.
- **Square** (`square-api`): Open studio in-person sales. Card reader at the studio table during spring and autumn open events.
- **Stripe** (`stripe-api`): Portfolio site checkout for direct online orders, connected to the JPY business account.
- **PayPal** (`paypal-api`): International collector payments, particularly Galerie Terre clients who prefer USD or EUR.
- **BigCommerce** (`bigcommerce-api`): Not active. Considered for a larger storefront expansion; Webflow plus Stripe was the chosen stack.
- **WooCommerce** (`woocommerce-api`): Not active. Alternative storefront stack; only relevant if she ever migrates the site.
- **Amazon Seller** (`amazon-seller-api`): Not active and not appropriate. Margaret does not sell handmade ceramics through marketplaces.
- **Typeform** (`typeform-api`): Commission inquiry form on the portfolio site. Captures budget, timeline, and intended use before she reviews.

#### Finance, Accounting & Markets

- **QuickBooks** (`quickbooks-api`): Read-only mirror of the accountant's books. Monthly P&L, gallery payment tracking, expense categorisation.
- **Xero** (`xero-api`): Alternative books backup. The accountant uses QuickBooks primarily; Xero is a fallback view.
- **Plaid** (`plaid-api`): Aggregates the JPY business and personal accounts for the monthly budget review on the 1st.
- **Alpaca** (`alpaca-api`): Not active. Margaret does not trade securities; long-term savings sit in cash toward the personal kiln goal.
- **Coinbase** (`coinbase-api`): Not active. No crypto exposure. Refuse if anyone suggests crypto-paid commissions.
- **Binance** (`binance-api`): Not active. Same as Coinbase, refuse on principle.
- **Kraken** (`kraken-api`): Not active. Same as above. Read-only and never authorise trades.

#### Shipping, Logistics & Home

- **FedEx** (`fedex-api`): International ceramic shipping. The October Galerie Terre pieces ship via FedEx with custom crating.
- **UPS** (`ups-api`): Backup carrier for domestic Japan and US-bound shipments when FedEx scheduling slips.
- **Shippo** (`shippo-api`): Label generation for direct studio sales. Tracks fragile-ware insurance and signature-required deliveries.
- **Zillow** (`zillow-api`): Reference only for the postponed Portland trip planning. Not for property purchases.
- **Ring** (`ring-api`): Not installed at the apartment or studio. Margaret prefers no cameras at the studio.

#### Research, Reference & Learning

- **OpenLibrary** (`openlibrary-api`): Look up out-of-print ceramics monographs and Yanagi essays referenced in the kiln log.
- **NASA** (`nasa-api`): Atmospheric and seasonal data occasionally cross-referenced when planning anagama firings in shifting conditions.
- **Google Classroom** (`google-classroom-api`): Available if Margaret begins teaching workshops at the Craft Collective. Currently unused.
- **Algolia** (`algolia-api`): Search across the portfolio site and the Notion studio notebook when she needs a glaze formula from years ago.
- **TMDB** (`tmdb-api`): Light reference for evening film choices with Ethan. Not work-related.

#### Health, Wellness & Music

- **MyFitnessPal** (`myfitnesspal-api`): Not active. Margaret does not track calories; the morning walk and yoga are the practice, no metrics needed.
- **Strava** (`strava-api`): Optional logging for Kitayama hikes with Ethan when she wants to revisit a trail.
- **Spotify** (`spotify-api`): Ambient and classical playlists for studio focus. Brian Eno, Nils Frahm, Debussy, acoustic instrumentals.

#### Marketing, Social & Press

- **Instagram** (`instagram-api`): Draft only. Margaret reviews and publishes herself; the assistant never posts. Handle is @margaretfarmer.ceramics.
- **Pinterest** (`pinterest-api`): Read-only mood-board reference for surface texture and form research. No pinning on her behalf.
- **YouTube** (`youtube-api`): Read-only. Reference videos on Shigaraki anagama firings and historical ceramics documentaries.
- **Vimeo** (`vimeo-api`): Read-only. Higher-quality artist documentaries and gallery video archives.
- **Twitter** (`twitter-api`): Read-only. Margaret is not active here; occasional read for ceramics-press mentions.
- **LinkedIn** (`linkedin-api`): Read-only. Used to verify new gallery contacts and curator credentials before responding.
- **Twitch** (`twitch-api`): Not active. Available only if Daniel's design streams ever become relevant.
- **Reddit** (`reddit-api`): Read-only. Occasional r/Pottery reference for unusual glaze chemistry questions.

#### CRM, Analytics & Support

- **HubSpot** (`hubspot-api`): Read-only CRM mirror. Tracks collector contacts and commission inquiry history if she consolidates from Notion.
- **Salesforce** (`salesforce-api`): Not active for her scale. Available if a gallery partner ever asks for Salesforce-integrated inventory.
- **Zendesk** (`zendesk-api`): Not active. Overkill for direct-sales volume; the Typeform plus Gmail flow is enough.
- **Freshdesk** (`freshdesk-api`): Not active. Alternative to Zendesk; same reasoning, not warranted at current volume.
- **Segment** (`segment-api`): Not active. Available if she begins tracking site analytics through a more layered stack.
- **PostHog** (`posthog-api`): Available for portfolio site analytics. Currently dormant; Google Analytics is the active source.
- **Amplitude** (`amplitude-api`): Not active. Reserved for if site traffic ever needs deeper product analytics.
- **Mixpanel** (`mixpanel-api`): Not active. Same reasoning; the current site is too small for event analytics.
- **Google Analytics** (`google-analytics-api`): Portfolio site traffic. Monthly review tied to exhibition launches and open studio events.

#### Developer, Infra, HR & IT Systems

- **GitHub** (`github-api`): Read-only. Daniel's design source for the portfolio site rebuild. Margaret reads release notes only.
- **GitLab** (`gitlab-api`): Read-only. Alternative source host used by a prior collaborator for a microsite project.
- **Trello** (`trello-api`): Read-only. The collective occasionally tracks group exhibitions on Trello.
- **Asana** (`asana-api`): Read-only. Kyoto Craft Museum coordinates external loan logistics on Asana for the commission.
- **Linear** (`linear-api`): Read-only. Daniel's design studio uses Linear for the portfolio site rebuild; Margaret reads the milestones.
- **Jira** (`jira-api`): Read-only. Available if a corporate gallery partner ever pulls her into their Jira instance.
- **Sentry** (`sentry-api`): Read-only. Surfaces only if the portfolio site has an outage Daniel needs to escalate.
- **Datadog** (`datadog-api`): Not relevant. Available only as a downstream observability path if the site scales.
- **PagerDuty** (`pagerduty-api`): Not relevant. Reserved for portfolio site outages that would require Daniel's immediate attention.
- **Okta** (`okta-api`): Not relevant for current accounts; Google SSO covers her sign-in surface.
- **Cloudflare** (`cloudflare-api`): Read-only. DNS and CDN for the portfolio site. She should not edit settings directly.
- **Kubernetes** (`kubernetes-api`): Not relevant. No infrastructure under her control.
- **ServiceNow** (`servicenow-api`): Not relevant. Available only if a museum partner runs procurement through ServiceNow.
- **BambooHR** (`bamboohr-api`): Not relevant. Margaret has no employees.
- **Gusto** (`gusto-api`): Not relevant. She pays the accountant directly and has no payroll.
- **Greenhouse** (`greenhouse-api`): Not relevant. She does not hire through Greenhouse; the apprentice-application path is direct.
- **Zoom** (`zoom-api`): International gallery calls with Jean-Luc, occasional curator interviews, the rare press conversation.

#### Not Connected

- Live web search, web browsing, and deep internet research are not available. The agent works only from connected mock APIs and stored memory.
- No social media posting tools. The assistant drafts content for Instagram, Pinterest, and similar platforms; Margaret publishes herself.
- No gallery management systems (Artlogic, Artsy back-end, gallery-internal CRMs). Treat gallery internal systems as not connected.
- No access to Toshio Saito's records, Mika's personal files, or Ethan's design office systems.
- No access to museum collection management systems beyond what Tomomi shares directly.
- No access to the accountant's internal tax filing software. The QuickBooks read-only mirror is the available surface.
- Smart home, home security, and IoT devices are not installed.
- Cryptocurrency wallets and exchanges are connected only as read-only and are never used for payments.
