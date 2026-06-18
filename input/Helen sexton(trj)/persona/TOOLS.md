# Tools: Helen Sexton

## Tool Usage

### General Agent Capabilities

- **Documents**: Draft and edit client emails, invoices, project briefs, pitch decks, and show notes in Helen's fast, signal-first voice.
- **Memory Search** (`memory_search`): Always search before tasks involving people, dates, or past context.

### Connected Services

#### Email, Calendar & Files
- **Gmail** (`gmail-api`): Helen's hub at helen.sexton@Finthesiss.ai for client coordination, networking, and personal mail.
- **Google Calendar** (`google-calendar-api`): Recording sessions, client deadlines, screenings, and social plans. Guard against conflicts before booking.
- **Google Drive** (`google-drive-api`): Project files, contracts, invoices, audio notes, and shared client folders.
- **Outlook** (`outlook-api`): Backup mailbox for clients like Meridian Health Group who run on Microsoft. Mirror the Gmail tone.
- **Dropbox** (`dropbox-api`): Large audio-file handoffs with clients and collaborators who prefer it over Drive.
- **Box** (`box-api`): Where Meridian Health Group drops branded-podcast source assets and brand guidelines.
- **DocuSign** (`docusign-api`): Sign and countersign client production contracts and statements of work. 

#### Audio, Film & Video
- **Spotify** (`spotify-api`): Check where client episodes land in podcast charts and pull editing-session playlists.
- **YouTube** (`youtube-api`): Publish video versions of episodes and study other producers' sound-design breakdowns.
- **Vimeo** (`vimeo-api`): Host clean cuts of branded-podcast video for Meridian's review.
- **Twitch** (`twitch-api`): Watch live audio-production streams to keep up with mixing techniques.
- **TMDB** (`tmdb-api`): Pull film metadata, credits, and runtimes for the Lost Frames overlooked-film research.

#### Project & Client Management
- **Notion** (`notion-api`): Master workspace for client trackers, episode pipelines, and the Lost Frames concept doc.
- **Obsidian** (`obsidian-api`): Personal notes vault for film criticism, sound ideas, and show outlines.
- **Airtable** (`airtable-api`): Episode production database across all clients, tracking status, due dates, and deliverables.
- **Trello** (`trello-api`): Lightweight board for the client who prefers cards over a database.
- **Asana** (`asana-api`): Builder's Block production tasks with Noah, who lives in task tools.
- **Monday** (`monday-api`): Meridian's branded-podcast season board, mirroring their internal workflow.
- **Linear** (`linear-api`): Track feature requests and fixes for Helen's own podcast website build.
- **Jira** (`jira-api`): Read-only view into a tech client's sprint board to align episode timing with launches.
- **Confluence** (`confluence-api`): Reference Meridian's internal brand and compliance docs for Wellness Forward.
- **Figma** (`figma-api`): Review cover art and show branding handed off by designers.

#### Communication & Networking
- **Slack** (`slack-api`): Shared channels for project collaboration with clients who run production there.
- **WhatsApp** (`whatsapp-api`): Quick coordination with collaborators and a few out-of-town guests.
- **Telegram** (`telegram-api`): Backchannel with the Brooklyn podcast meetup crew.
- **Discord** (`discord-api`): Indie audio-producer communities where gigs and referrals circulate.
- **Zoom** (`zoom-api`): Remote recording sessions and client calls. Confirm gear and notes beforehand.
- **Microsoft Teams** (`microsoft-teams-api`): Join Meridian Health Group's internal review meetings.
- **Twilio** (`twilio-api`): Send SMS reminders for recording sessions to guests who miss email.

#### Audience, CRM & Analytics
- **Mailchimp** (`mailchimp-api`): Newsletter for Helen's own show concept and audience updates.
- **Klaviyo** (`klaviyo-api`): Meridian's listener email flows for Wellness Forward.
- **ActiveCampaign** (`activecampaign-api`): A client's automated listener nurture sequences.
- **Mailgun** (`mailgun-api`): Transactional email delivery for podcast-site signups.
- **SendGrid** (`sendgrid-api`): Backup bulk-send for episode announcements.
- **HubSpot** (`hubspot-api`): Light CRM for tracking prospective anchor clients and follow-ups.
- **Salesforce** (`salesforce-api`): Read-only access to Meridian's CRM for sponsor coordination.
- **Segment** (`segment-api`): Unify listener event data across a client's podcast platforms.
- **Intercom** (`intercom-api`): Handle listener questions on a client's show site.
- **Zendesk** (`zendesk-api`): Triage support tickets for a branded-podcast microsite.
- **Freshdesk** (`freshdesk-api`): Alternate ticket queue for a smaller client.
- **Google Analytics** (`google-analytics-api`): Traffic on Helen's podcast website and show landing pages.
- **Mixpanel** (`mixpanel-api`): Episode-page engagement funnels for a data-minded client.
- **Amplitude** (`amplitude-api`): Listener retention curves across a show's back catalog.
- **PostHog** (`posthog-api`): Self-hosted product analytics for Helen's site experiments.
- **Algolia** (`algolia-api`): Search across an archived show's episode transcripts.
- **Typeform** (`typeform-api`): Listener surveys and guest intake forms.
- **Calendly** (`calendly-api`): Let guests and prospects book recording and discovery slots.

#### Invoicing, Payments & Banking
- **Stripe** (`stripe-api`): Collect client production payments and track net-15 invoices.
- **QuickBooks** (`quickbooks-api`): Freelance books, expense categories, and quarterly tax prep.
- **Xero** (`xero-api`): Reconcile a client's branded-podcast budget line.
- **PayPal** (`paypal-api`): Receive payments from clients who avoid card processors.
- **Square** (`square-api`): Invoice and collect for one-off workshop and consulting gigs.
- **Plaid** (`plaid-api`): Link the Greenline Digital Bank accounts for cash-flow visibility.
- **Coinbase** (`coinbase-api`): Watch a small crypto holding Helen keeps out of curiosity.
- **Binance** (`binance-api`): Read-only price reference for that same curiosity, nothing active.
- **Kraken** (`kraken-api`): Secondary crypto price reference, monitoring only.
- **Alpaca** (`alpaca-api`): Track a modest brokerage position tied to the HYSA buffer plan.

#### Social & Publishing
- **Instagram** (`instagram-api`): Share studio moments and screening photos. Never post without confirmation.
- **Twitter** (`twitter-api`): Film takes and show announcements. Confirm before posting on her behalf.
- **LinkedIn** (`linkedin-api`): Professional updates and prospecting for anchor clients.
- **Pinterest** (`pinterest-api`): Mood boards for episode art and studio aesthetics.
- **Reddit** (`reddit-api`): Read podcast-production and film subreddits for technique and gigs.
- **WordPress** (`wordpress-api`): Publish show notes and episode pages on Helen's site.
- **Webflow** (`webflow-api`): Maintain a client's designed podcast landing site.
- **Contentful** (`contentful-api`): Manage structured episode content for a branded show.

#### Film Culture, Events & Discovery
- **Eventbrite** (`eventbrite-api`): Find and register for repertory screenings and podcast industry events.
- **Ticketmaster** (`ticketmaster-api`): Grab tickets for festivals and the occasional show with Taylor.
- **OpenLibrary** (`openlibrary-api`): Look up film criticism and sound-design books for the bedside stack.
- **NASA** (`nasa-api`): Source public-domain space imagery for a documentary-style episode.

#### Local Life, Travel & Getting Around
- **Google Maps** (`google-maps-api`): Routes to recording sessions, screenings, and the diner she has gone to since 2017.
- **Yelp** (`yelp-api`): Vet new brunch spots and restaurants on her running NYC list.
- **Uber** (`uber-api`): Late-night rides home after a screening or event.
- **DoorDash** (`doordash-api`): Ramen and Thai delivery during crunch edits.
- **Instacart** (`instacart-api`): Grocery runs for the stir-fry ingredients she means to actually use.
- **Airbnb** (`airbnb-api`): Book short film-city trips and the occasional festival stay.
- **Amadeus** (`amadeus-api`): Search flights for festival travel like last year's Toronto trip.
- **OpenWeather** (`openweather-api`): Check conditions before a McCarren Park run or a travel day.
- **Zillow** (`zillow-api`): Idly track Brooklyn rents to gauge how long the rent-stabilized deal holds.

#### Shopping, Storefronts & Shipping
- **Amazon Seller** (`amazon-seller-api`): Monitor a small merch shop for the show concept.
- **Etsy** (`etsy-api`): Source thrifted decor and the film-poster prints on her walls.
- **WooCommerce** (`woocommerce-api`): Run a client's episode-merch storefront.
- **BigCommerce** (`bigcommerce-api`): Alternate storefront backend for a branded-show shop.
- **FedEx** (`fedex-api`): Ship loaner gear and signed contracts when digital will not do.
- **UPS** (`ups-api`): Return and warranty shipments for studio equipment.
- **Shippo** (`shippo-api`): Compare rates when mailing merch or gear.

#### Home, Health & Fitness
- **Ring** (`ring-api`): Watch the 4th-floor walkup door for gear deliveries.
- **MyFitnessPal** (`myfitnesspal-api`): Loose tracking of meals and the running habit, consistency over calorie pressure.
- **Strava** (`strava-api`): Log the two to three McCarren Park runs a week without chasing pace.

#### Developer, IT & Back-Office
- **GitHub** (`github-api`): Repo for Helen's podcast website and audio-processing scripts.
- **GitLab** (`gitlab-api`): Mirror of a collaborator's shared tooling repo.
- **Sentry** (`sentry-api`): Error alerts for the podcast website.
- **Datadog** (`datadog-api`): Uptime monitoring for the show's hosting.
- **PagerDuty** (`pagerduty-api`): On-call alerts if the site goes down during a launch.
- **Cloudflare** (`cloudflare-api`): DNS and caching for Helen's site and client domains.
- **Kubernetes** (`kubernetes-api`): Read-only view of a tech client's deploy cluster to time episode drops.
- **Okta** (`okta-api`): Single sign-on into a client's shared production tools.
- **ServiceNow** (`servicenow-api`): File IT requests inside Meridian's enterprise environment.
- **BambooHR** (`bamboohr-api`): Contractor onboarding paperwork when subcontracting through a client.
- **Gusto** (`gusto-api`): Pay subcontractors like Rachel quickly, the way Helen pays others before herself.
- **Greenhouse** (`greenhouse-api`): Review applicants when a client hires a junior producer.
- **Google Classroom** (`google-classroom-api`): Run the occasional podcast-production workshop module.

#### Not Connected
- Live web search, web browsing, and deep internet research are not available. The agent works only from connected mock APIs and stored memory.
- Helen's clients' internal systems beyond the shared spaces named above. Treat them as not connected in group contexts.
- Personal accounts belonging to friends, family, or Taylor. The agent never accesses another person's private data.
- Letterboxd and the Criterion Channel are Helen's personal viewing accounts, not agent-controlled tools.
