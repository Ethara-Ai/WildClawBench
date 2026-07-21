# Tools: Greg Howard

## Tool Usage

### Connected Services

#### Email, Calendar & Documents

- **Gmail** (`gmail-api`): Primary inbox for community coordination, CEU registrations, insurance correspondence, and family logistics.
- **Google Calendar** (`google-calendar-api`): The master calendar: clinic schedule, training plan, family and church events, CEU deadlines, and Derek's school dates.
- **Google Drive** (`google-drive-api`): Home of the CEU tracking spreadsheet, the family budget, the exercise guide draft, and community event planning docs.
- **Outlook** (`outlook-api`): Pulls forwarded invites from Derek's school account onto the shared calendar so district events stay in view.
- **Dropbox** (`dropbox-api`): Overflow storage for race photos, old training plans, and scanned family documents.
- **Box** (`box-api`): Upload portal several insurers use for appeal attachments; she keeps copies of everything she submits.
- **DocuSign** (`docusign-api`): Signatures for mortgage paperwork, insurance forms, and the PSLF employment certification.

#### Messaging & Family Coordination

- **WhatsApp** (`whatsapp-api`): Extended family threads, including the cousins who never answer regular texts.
- **Telegram** (`telegram-api`): The running store's half marathon training group chat; pace partners, Saturday meetup spots, and route changes.
- **Twilio** (`twilio-api`): Sends her personal reminder texts (run kit, Carol transfer dates, budget night) and stays off anything patient-related.
- **Discord** (`discord-api`): The Mapleton track alumni server; meet-weekend chatter and results threads.
- **Slack** (`slack-api`): The PT colleagues' workspace where she and Sarah coordinate coverage informally; scheduling only, never patient details.
- **Microsoft Teams** (`microsoft-teams-api`): Captures the parent-night and school-event join links Derek forwards and drops them onto the shared calendar.

#### Clinic, Insurance & Kestrel Admin

- **BambooHR** (`bamboohr-api`): Kestrel's HR portal for PTO requests and the pending PSLF employment certification follow-up.
- **Greenhouse** (`greenhouse-api`): Kestrel's hiring portal; she sits on the interview panel for the open pediatric PT role, reviewing candidate packets and filing scorecards.
- **ServiceNow** (`servicenow-api`): Kestrel's internal IT desk; where she files tickets when the work iPad misbehaves.
- **Salesforce** (`salesforce-api`): Kestrel's administrative CRM; she logs community outreach events and partner-organization contacts after each visit.
- **Okta** (`okta-api`): Single sign-on gate in front of several CEU portals and Kestrel admin tools.
- **Zendesk** (`zendesk-api`): Ticket tracking behind two insurer portals; keeps her appeal threads from going cold.
- **Freshdesk** (`freshdesk-api`): Support desk for her main CEU course provider; used when registrations glitch.
- **Intercom** (`intercom-api`): The chat widget on insurance and CEU sites; a faster route than their phone trees.

#### CEU & Professional Development

- **Zoom** (`zoom-api`): Hosts CEU webinars, including the October neuromotor assessment session, and her remote work meetings.
- **Calendly** (`calendly-api`): Booking link for CEU consultations and for pinning down dates like the Coach Palmer lunch.
- **Google Classroom** (`google-classroom-api`): Hosts her current CEU course series; modules, quizzes, and completion certificates all live in her own Classroom account.
- **Vimeo** (`vimeo-api`): Continuing education video libraries, plus the first batch of exercise demo clips uploaded for the guide's beta site.
- **Obsidian** (`obsidian-api`): Reading notes from pediatric PT journals and idea capture for the guide.
- **Notion** (`notion-api`): Chapter-by-chapter workspace for the six-chapter exercise guide outline.
- **LinkedIn** (`linkedin-api`): Her PT professional network, and a window into Lauren's MBA-season networking push.

#### Running, Training & Health Data

- **Strava** (`strava-api`): Logs her training, synced from her Garmin; Coach Palmer leaves kudos on her long runs.
- **MyFitnessPal** (`myfitnesspal-api`): Fueling checks during the half marathon build, with attention to iron-rich meals. No calorie pressure.
- **OpenWeather** (`openweather-api`): Run-morning forecasts and frost warnings for the garden beds.
- **Google Maps** (`google-maps-api`): Long-run route planning, the Worthington commute, and the drive to Dayton.
- **GitHub** (`github-api`): Hosts her training-dashboard repo: scripts that turn Strava exports into mileage and hamstring-load charts. Tyler reviews her pull requests over Sunday dinner.
- **GitLab** (`gitlab-api`): Runs the pipeline that rebuilds the training dashboard and backs up the guide site's content.
- **Kubernetes** (`kubernetes-api`): Single-node cluster on the basement mini PC that serves the dashboard and the guide's self-hosted analytics; she restarts a stuck pod herself now.
- **Sentry** (`sentry-api`): Surfaces error alerts from the dashboard scripts and the guide site's signup form; she fixes the simple ones before texting Tyler.
- **Datadog** (`datadog-api`): Monitors the mini PC disk, CPU, and uptime, and alerts her before the server fails mid-training block.
- **PagerDuty** (`pagerduty-api`): Pages her phone if the guide site or its signup form goes down; the only alert allowed to interrupt a Saturday long run.

#### Money & Household Finance

- **Plaid** (`plaid-api`): Links the Huntington accounts so the first-of-the-month budget review starts with real numbers.
- **QuickBooks** (`quickbooks-api`): Tracks the guide project's expenses (domain, illustration software, print samples) separately so the household spreadsheet stays clean.
- **Xero** (`xero-api`): Books for the neighborhood association's small treasury; she reconciles it after every fundraiser.
- **PayPal** (`paypal-api`): Pays race entry fees, friend-dinner splits with the track alumni crew, and online purchases.
- **Square** (`square-api`): How she pays at the farmers' market and church bake sales.
- **Stripe** (`stripe-api`): The payment rail behind most of her race registrations and CEU checkouts.
- **Gusto** (`gusto-api`): Payroll portal for Derek's summer tutoring co-op; she pulls his paystubs into the budget spreadsheet for the months it runs.
- **Coinbase** (`coinbase-api`): Holds the small position Tyler set her up with; she checks it during the first-of-the-month budget review and logs the balance.
- **Kraken** (`kraken-api`): Price alerts on the coins Tyler holds, pulled up before Sunday dinners so his crypto talk meets real numbers.
- **Binance** (`binance-api`): Runs a $10 recurring auto-buy as a live experiment in the index-funds-versus-crypto argument with Tyler; she logs the results at the budget review.
- **Alpaca** (`alpaca-api`): Holds a paper-trading portfolio she rebalances to learn index investing before any real money moves beyond the 401(k).

#### Home, Garden & Errands

- **Ring** (`ring-api`): Doorbell at the Clintonville ranch; package alerts and a porch check when they travel.
- **Zillow** (`zillow-api`): Tracks the house's estimated value and neighborhood comps for the annual net-worth line in the budget spreadsheet.
- **Instacart** (`instacart-api`): Grocery delivery on the weeks the caseload eats the grocery run; the standing order mirrors the kitchen staples list.
- **DoorDash** (`doordash-api`): Orders dinner delivery when cooking falls through.
- **Airtable** (`airtable-api`): The raised-bed planting tracker: what went in, when, and what outproduced expectations.
- **NASA** (`nasa-api`): Astronomy picture of the day on the kitchen iPad; a small dawn ritual before the run.
- **Shippo** (`shippo-api`): Label printing when boxes go out to Aunt Carol's grandkids.
- **FedEx** (`fedex-api`): Tracking for running shoe deliveries and gift shipments.
- **UPS** (`ups-api`): Tracking for bulk orders and holiday shipments.

#### Community, Church & Events

- **Eventbrite** (`eventbrite-api`): Community events, CEU workshops, and neighborhood association happenings.
- **Ticketmaster** (`ticketmaster-api`): Buys concert tickets (an SZA tour is the standing wish) and seats for big track meets.
- **Typeform** (`typeform-api`): RSVP forms for church gatherings and women's group surveys.
- **Mailchimp** (`mailchimp-api`): The Clintonville Community Church newsletter she helps proof.
- **WordPress** (`wordpress-api`): The church website; she posts event updates as a volunteer.
- **Jira** (`jira-api`): Ticket board for the church website overhaul; she scopes tasks for the volunteer crew and closes her own each week.
- **Asana** (`asana-api`): Neighborhood association volunteer task lists.
- **Trello** (`trello-api`): The Thanksgiving hosting board: menu, guest list, and who brings what.
- **Monday** (`monday-api`): Runs the neighborhood association's fall festival board: vendor list, volunteer shifts, and permit deadlines.
- **ActiveCampaign** (`activecampaign-api`): Sends the Mapleton track alumni newsletter and meet-weekend reminders; she has owned the list since the last reunion.

#### The Exercise Guide Project

- **Figma** (`figma-api`): Layout mockups and illustration placement for the guide's printable pages.
- **Webflow** (`webflow-api`): The guide's beta site, live on its own domain; she updates exercise pages during the Thursday writing block.
- **Contentful** (`contentful-api`): Stores the structured exercise write-ups and photos the beta site pulls in, so one edit updates every page.
- **Confluence** (`confluence-api`): Shared drafting space where she and Sarah Mitchell review guide chapters and leave inline comments.
- **Linear** (`linear-api`): Issue tracker for the beta site: typos, broken demo clips, and parent-requested exercises get triaged on Thursday nights.
- **Cloudflare** (`cloudflare-api`): Runs DNS and caching in front of the guide site; she reviews the traffic and threat summary.
- **Algolia** (`algolia-api`): Powers the exercise search box on the beta site so parents can filter moves by age and goal.
- **SendGrid** (`sendgrid-api`): Sends the signup confirmations and account emails for the guide's early-access list.
- **Mailgun** (`mailgun-api`): Delivers the home-exercise tip email to the early-access parents.
- **Klaviyo** (`klaviyo-api`): Runs the welcome sequence for new guide subscribers and keeps the parent and clinician segments separate.
- **HubSpot** (`hubspot-api`): Free CRM where she logs the clinics, pediatricians, and parent groups she pitches the guide to, with follow-up dates.
- **Google Analytics** (`google-analytics-api`): Tracks which guide pages and chapters draw parents in.
- **Mixpanel** (`mixpanel-api`): Tracks which demo clips get played and replayed; replays show her where the instructions confuse people.
- **Amplitude** (`amplitude-api`): Builds engagement cohorts for guide subscribers; the retention chart decides which chapter she writes next.
- **PostHog** (`posthog-api`): Self-hosted on the mini PC; session replays and heatmaps show where parents stall on the exercise pages.
- **Segment** (`segment-api`): Routes the guide site's events to the analytics stack so each page is instrumented once.

#### Shopping, Media & Leisure

- **Etsy** (`etsy-api`): Researched, specific gifts: the kind Linda did not know she wanted.
- **Pinterest** (`pinterest-api`): Garden layouts, family recipe variations, and Thanksgiving hosting ideas.
- **Amazon Seller** (`amazon-seller-api`): Seller account where she lists finished DPT textbooks and gently used race gear; proceeds go to the vacation fund.
- **WooCommerce** (`woocommerce-api`): The local running store's online shop runs on it; Hoka restocks happen here.
- **BigCommerce** (`bigcommerce-api`): The church's small online store for fundraiser merchandise and bake-sale preorders; she keeps the listings current.
- **Spotify** (`spotify-api`): SZA, H.E.R., indie folk, classic rock, and the podcast rotation for commutes and runs.
- **YouTube** (`youtube-api`): Track meet replays, gardening how-tos (the herb spiral started here), and PT exercise demos.
- **Twitch** (`twitch-api`): Tyler streams games; she drops into chat to heckle gently.
- **TMDB** (`tmdb-api`): Picks titles for date night and thriller adaptations worth arguing about.
- **OpenLibrary** (`openlibrary-api`): Memoirs, non-fiction, and thriller holds; reading queue management.
- **Reddit** (`reddit-api`): Reads the running and vegetable gardening communities and saves threads worth trying.
- **Twitter** (`twitter-api`): Track and field news firehose during championship season.
- **Instagram** (`instagram-api`): Posts family photos and shares a work win now and then.

#### Travel & Dining

- **Amadeus** (`amadeus-api`): Flight searches for CEU trips and the someday destination half marathon.
- **Airbnb** (`airbnb-api`): Stay options for the 2027 vacation she and Derek are gently planning.
- **Uber** (`uber-api`): Books airport rides and last-minute transport; also the platform Robert drives for, which keeps her opinions about driver pay sharp.
- **Yelp** (`yelp-api`): Date-night restaurant research and reservations; Italian for Derek, good vegetarian options for her.

#### Not Connected

- Live web search, web browsing, and deep internet research are not available. The agent works only from connected mock APIs and stored memory.
- Kestrel Healthcare's EMR and clinical systems are internal and never connected; patient data stays on the work-issued iPad.
- Derek's school accounts, gradebook, and district systems are his alone; only what he forwards is visible.
- Family members' personal accounts (Linda, Robert, Lauren, Tyler, Carol) are not connected.
- Banking credentials are not stored; account linking provides balance context only.
