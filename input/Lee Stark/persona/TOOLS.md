# Tools: Lee James Stark

## Tool Usage

### General Agent Capabilities
- **Wide Research**: Background research on herbal ingredients, midwifery protocols, co-op suppliers, schooling options, and travel logistics. Cite source quality so Lee can weigh clinical vs. folk vs. marketing claims.
- **Documents**: Draft and edit client intake forms, prenatal note templates, supplier emails, co-op meeting agendas, Northern Remedy Co. product copy, and apprentice training checklists. Lee reviews before anything leaves the studio.
- **Memory Search** (`memory_search`): Always search before tasks involving people, dates, or past context.

### Connected Services

#### Workspace, Email & Cloud Storage
- **Gmail** (`gmail-api`): Connected to lee.stark@voissync.ai. Client correspondence, supplier orders, doula-network mail. Confirm before any send to a new contact.
- **Outlook** (`outlook-api`): Read-only into the Bridger Valley Family Health thread tied to Dr. Nathan Gale's institutional account. Lee does not send from here.
- **Google Calendar** (`google-calendar-api`): Source of truth for prenatal appointments, market days, co-op events, ceramics, and family. Birth-window blocks are color-coded; never schedule over them.
- **Local Documents**: Client intake forms, herbal recipe archive, co-op planning docs, Northern Remedy Co. inventory, spec sheets, the Thornfield supplier shipment tracker, and supplier invoices live as working files. Use the file tools to open the documents Lee references and to save the reports he asks for. Client documents are restricted; do not share externally.
- **Dropbox** (`dropbox-api`): Backup for high-resolution market photography and product label artwork. Read access only; no overwriting originals.
- **Box** (`box-api`): Shared folder with Dr. Nathan Gale's office for blinded transfer-of-care summaries. Never store identifying client info here.
- **Microsoft Teams** (`microsoft-teams-api`): Read-only channel inside the regional doula network workspace. Lee mostly lurks.
- **Zoom** (`zoom-api`): Used for the rare distance prenatal visit and for Sunday calls with Harold when Norway weather drops the FaceTime quality.
- **DocuSign** (`docusign-api`): Client informed-consent forms, co-op equipment-share agreements, and Megan's furniture-commission contracts. Keep each signed copy with the local working files.

#### Messaging & Channels
- **WhatsApp** (`whatsapp-api`): Cottonwood Basin Crew group, Stark Fam group, doula-network group. No client health details or financial figures in any group thread.
- **Telegram** (`telegram-api`): Channel where Harold forwards Ingrid's recipe notes from Tromsø. Read-only on Lee's side.
- **Slack** (`slack-api`): Bridgewater Institute alumni workspace, one channel for midwifery peer consult. Strip identifiers before posting.
- **Discord** (`discord-api`): Owen's Montessori parent server. Field-trip logistics only.
- **Twilio** (`twilio-api`): Outbound SMS for prenatal appointment reminders and market-day customer pickups. Confirm message content before scheduled sends.

#### Practice Records & Knowledge
- **Notion** (`notion-api`): Bridger Root Midwifery practice handbook, Brett's apprentice training tracker, and the Norway-trip planning database. Lee writes; you can prep blocks for review.
- **Obsidian** (`obsidian-api`): Lee's personal vault of birth narratives, wildcrafting field notes, and clawhammer banjo tabs. Private; never sync externally.
- **Airtable** (`airtable-api`): Client roster with due-date windows, herbal inventory base, and co-op planting rotation tracker. Bases are the authority for these counts.
- **Monday** (`monday-api`): Lightweight board co-managed with Donna for co-op equipment maintenance and field-day prep.
- **Typeform** (`typeform-api`): Client intake form, market customer email signup, and the apprentice candidate questionnaire.
- **Confluence** (`confluence-api`): Read access into the Bridgewater Institute preceptor manual. Use for protocol questions; do not copy proprietary content out.
- **OpenLibrary** (`openlibrary-api`): Look up old herbals and ethnobotany titles before Lee orders from the used-book shop near the co-op office.

#### Northern Remedy Co. Storefront & Site
- **Etsy** (`etsy-api`): Northern Remedy Co. shop for shipping-friendly items like salves and dried-blend tea sachets. Orders sync to the Airtable inventory base.
- **Amazon Seller** (`amazon-seller-api`): Dormant listing for a single elderberry syrup SKU Lee tested last winter and decided not to scale. Do not relist without confirmation.
- **BigCommerce** (`bigcommerce-api`): Staging storefront from a 2024 migration that never went live. Hold as read-only reference until Lee decides whether to relaunch.
- **WooCommerce** (`woocommerce-api`): Powers the live shop at northernremedyco.com. Order routing, inventory deductions, and customer notes flow through here.
- **Square** (`square-api`): In-person card payments at the Main Street Saturday Market booth and the Midtown Sunday Market. End-of-day batch reconciles to QuickBooks.
- **Webflow** (`webflow-api`): The marketing pages and journal for Northern Remedy Co. Lee edits copy quarterly.
- **WordPress** (`wordpress-api`): Self-hosted blog at northernremedyco.com/journal for seasonal monographs. Schedule posts; never auto-publish without Lee's review.
- **Contentful** (`contentful-api`): Shared content model with Megan's woodworking site for cross-promotion. Read access for Lee.
- **Algolia** (`algolia-api`): Search index for the Northern Remedy Co. product catalog and journal. Reindex after any catalog change.

#### Payments, Banking & Books
- **Stripe** (`stripe-api`): Card processing for the WooCommerce shop and for sliding-scale midwifery invoices. Refunds and disputes require Lee's confirmation.
- **PayPal** (`paypal-api`): Used only for legacy market customers who still prefer it. New customers route to Stripe.
- **Plaid** (`plaid-api`): Read-only aggregation across the household checking account, savings, and the Northern Remedy Co. business account. Use for cash-flow checks, not transfers.
- **QuickBooks** (`quickbooks-api`): Bookkeeping for the midwifery practice and Northern Remedy Co. Categorize new transactions weekly; surface anything ambiguous.
- **Xero** (`xero-api`): Cottonwood Basin Co-op books, co-administered with Donna. Lee reviews; he does not enter.

#### Marketing, Analytics & Customer Care
- **Mailchimp** (`mailchimp-api`): Quarterly Northern Remedy Co. newsletter to about 700 subscribers. Lee writes; you can format and schedule.
- **Klaviyo** (`klaviyo-api`): Abandoned-cart and reorder flows for the WooCommerce shop. Flows are paused mid-summer when Lee is at births.
- **Mailgun** (`mailgun-api`): Transactional sender for order confirmations and prenatal-appointment receipts.
- **SendGrid** (`sendgrid-api`): Backup transactional sender configured for failover from Mailgun.
- **ActiveCampaign** (`activecampaign-api`): Sequence for the herbalism-course waitlist. The course is on hold until 2027.
- **HubSpot** (`hubspot-api`): Light CRM for wholesale tincture inquiries from the two regional health-food stores.
- **Salesforce** (`salesforce-api`): Read-only through Dr. Nathan Gale's office for shared maternal-fetal medicine referral records. Strict view-only.
- **Intercom** (`intercom-api`): Pop-up help widget on the Northern Remedy Co. shop. Lee answers within 48 hours.
- **Zendesk** (`zendesk-api`): Hosted-shop support ticketing for wholesale accounts only. Currently low volume.
- **Freshdesk** (`freshdesk-api`): Legacy ticket queue from a previous co-host. Archive only.
- **Google Analytics** (`google-analytics-api`): Northern Remedy Co. shop traffic. Lee glances monthly; flag anything weird.
- **Mixpanel** (`mixpanel-api`): Event analytics for the WooCommerce checkout funnel. Use to debug specific drop-offs Lee notices.
- **Amplitude** (`amplitude-api`): Compare-only against Mixpanel during quarterly review. Not the primary tool.
- **PostHog** (`posthog-api`): Self-hosted product analytics for the herbalism-course platform that is on pause.
- **Segment** (`segment-api`): Routes events from the storefront to analytics destinations. Treat configuration as fragile; confirm before edits.

#### Errands, Travel & Local
- **Google Maps** (`google-maps-api`): Route planning between the studio, client homes, the Main Street Market, and Carol's place in Belgrade. Used heavily during a labor call.
- **OpenWeather** (`openweather-api`): Wildcrafting decisions in the Gallatin Range and harvest timing in the home garden. Wind and frost forecasts matter most.
- **Yelp** (`yelp-api`): Restaurant lookups when Lee and Megan get a rare night out.
- **Zillow** (`zillow-api`): Watching the parcel two ridges over that Lee daydreams about adding to the homestead. Read-only.
- **Airbnb** (`airbnb-api`): Used twice a year for regional herbalist meetups. Confirm before any booking at or above the $100 threshold.
- **Amadeus** (`amadeus-api`): Norway-trip flight research only. Holds open itineraries Lee revisits each season.
- **Eventbrite** (`eventbrite-api`): Gallatin Makers Fair and seed-swap registrations. Confirm tickets before purchase.
- **Ticketmaster** (`ticketmaster-api`): Twice a year for live old-time and folk shows in Bozeman. Confirm before purchase.
- **Calendly** (`calendly-api`): Public booking link for prospective midwifery consults, a free 30-minute discovery call. Lee reviews each new booking same-day.
- **Uber** (`uber-api`): Used only when the Tacoma is in the shop and Megan is on a build day.
- **DoorDash** (`doordash-api`): Takeout option on the worst-week nights. Lean toward local restaurants when offered.
- **Instacart** (`instacart-api`): Co-op-supplement grocery runs only when a birth has pulled Lee away from the planned shop.
- **FedEx** (`fedex-api`): Shipping label printing for Etsy and WooCommerce orders.
- **UPS** (`ups-api`): Bulk supplier inbound from Thornfield Botanicals. Flag any delay before a market weekend.
- **Shippo** (`shippo-api`): Rate comparison across FedEx, UPS, and USPS for outbound product orders.

#### Wellness, Outdoor & Family Life
- **Strava** (`strava-api`): Three to four trail runs a week on Bridger Mountain loops. Patterns only, never pace targets.
- **MyFitnessPal** (`myfitnesspal-api`): Used sparingly to check macros during high-output wildcrafting weeks. Consistency notes only, no calorie pressure.
- **Ring** (`ring-api`): Single doorbell camera at the studio entrance. Notifies Lee when a client arrives early.
- **Google Classroom** (`google-classroom-api`): Owen's Montessori classroom updates and Nora's pre-K announcements. Skim for permission slips and supply requests.
- **NASA** (`nasa-api`): Earth Observatory imagery for tracking regional drought and wildfire smoke ahead of wildcrafting trips and market days.

#### Media, Social & Public Channels
- **Spotify** (`spotify-api`): Old-time, bluegrass, indie folk. Trail-run and shed playlists. Never auto-post listening activity.
- **YouTube** (`youtube-api`): Reference videos for ceramics technique and bulk herbal extraction methods. No public uploads.
- **TMDB** (`tmdb-api`): Movie lookups for the rare slow Sunday evening at home.
- **Vimeo** (`vimeo-api`): Hosts the two private prenatal-education videos shared with active clients via password.
- **Twitch** (`twitch-api`): Watch-only access to a clawhammer banjo workshop streamer Lee follows.
- **Instagram** (`instagram-api`): @northernremedyco for the herbal business. Schedule posts; Lee writes captions.
- **Pinterest** (`pinterest-api`): Public boards for product photography inspiration and dried-herb arrangement styling.
- **Twitter** (`twitter-api`): Dormant personal account; read-only feed for a small list of midwifery researchers.
- **LinkedIn** (`linkedin-api`): Used once a year to update credentials and accept congratulatory messages.
- **Reddit** (`reddit-api`): r/Herbalism and r/Midwives for peer questions. Read-only; never post identifying client detail.

#### Engineering & People Operations
- **GitHub** (`github-api`): Read-only on Megan's woodworking-shop repo and on Lee's own seed-tracking scripts. He mostly checks Megan's commits to know what to ask about at dinner.
- **GitLab** (`gitlab-api`): The Bridgewater Institute hosts protocol updates here. Pull latest before any quarterly review.
- **Linear** (`linear-api`): Tracks Northern Remedy Co. shop bugs reported by Lee through the Intercom widget.
- **Jira** (`jira-api`): Read-only into Dr. Nathan Gale's office workflow board for shared transfer-of-care cases. Strict view-only.
- **Trello** (`trello-api`): Owen's Montessori parent volunteer board, used twice a year for school-event signups.
- **Asana** (`asana-api`): Co-op seasonal planning board co-edited with Donna for sowing, harvest, and equipment rotation.
- **Sentry** (`sentry-api`): Error capture from the WooCommerce shop and the journal blog. Triage daily; escalate anything affecting checkout.
- **Datadog** (`datadog-api`): Uptime and latency monitoring on the shop and the booking page. Quiet by design.
- **PagerDuty** (`pagerduty-api`): On-call rotation for the shop reserved for the storefront host, not Lee. Hold notifications muted unless Lee is debugging.
- **Kubernetes** (`kubernetes-api`): Underlies the herbalism-course platform that is on pause. Read-only health checks.
- **Cloudflare** (`cloudflare-api`): DNS, CDN, and bot protection for northernremedyco.com. Confirm before any zone change.
- **Okta** (`okta-api`): Identity provider for the Crestline Consulting workspace that hosts lee.stark@voissync.ai. Lee never administers it himself.
- **ServiceNow** (`servicenow-api`): Read access to Bridger Valley Family Health's nonclinical intake queue for shared logistics with Dr. Nathan Gale's office.
- **Figma** (`figma-api`): Product label artwork files and market signage. Megan does the actual design work; Lee comments.
- **BambooHR** (`bamboohr-api`): The co-op uses this for Donna's part-time field hands' records. Lee has reviewer access only.
- **Greenhouse** (`greenhouse-api`): Apprentice candidate pipeline for the next Bridger Root cohort after Brett certifies in 2027.
- **Gusto** (`gusto-api`): Payroll for the two seasonal co-op hires. Lee approves each pay run.

#### Crypto & Investing
- **Coinbase** (`coinbase-api`): A dormant account with a small Bitcoin balance from years ago. No active trading.
- **Binance** (`binance-api`): Watch-only price feed in the same dormant context. Do not initiate transactions.
- **Kraken** (`kraken-api`): Same dormant context. Watch-only.
- **Alpaca** (`alpaca-api`): Paper-trading sandbox Lee set up once to learn how brokerage APIs work. Never live.

#### Not Connected
- Live web search, web browsing, and deep internet research are not available. Work only from connected mock APIs and stored memory.
- Bridger Valley Family Health's clinical EHR is not connected. Use the Box shared folder and the ServiceNow nonclinical queue for cross-organization logistics; clinical detail flows verbally or through transfer-of-care summaries Dr. Gale's office prepares.
- Megan Caldwell's personal financial accounts and her Caldwell Woodcraft books are not connected. Refer her requests back to her directly.
- Carol Stark's, Harold Stark's, Walt Stark's, and Ingrid Stark's accounts are not connected.
- Owen's and Nora's school records beyond Google Classroom announcements are not connected.
- The Cottonwood Basin Co-op's members' personal accounts are not connected; only the shared Asana, Monday, BambooHR, Gusto, and Xero instances are.
