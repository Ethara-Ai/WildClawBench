# Tools: Lee James Powers

## Tool Usage

### General Agent Capabilities
- **Wide Research**: Reading across connected mock APIs to surface dance studio competitive landscape, CEU vendor options, FNP program details, and small business grants for first-generation entrepreneurs in Oregon.
- **Documents**: Drafting class plans, student emails, choreography notes, the Powers Swing Academy business plan, monthly budgets, and showcase run-of-show docs.
- **Memory Search** (`memory_search`): Always search before tasks involving people, dates, or past context.

### Connected Services

#### Crestline Workspace: Email, Calendar & Files
- **Gmail** (`gmail-api`): Studio correspondence, CEU vendor mail, and personal email through lee.powers@voissync.ai.
- **Outlook** (`outlook-api`): Hospital-issued mailbox for shift swaps and HR notices. Never crossed with studio business.
- **Google Calendar** (`google-calendar-api`): ER shift rotation, dance class blocks, Sundays with Barbara, and showcase prep weeks.
- **Local files** (on Lee's laptop): Class playlists, choreography videos, Powers Swing Academy business plan, and monthly budget sheets.
- **Dropbox** (`dropbox-api`): Backup of choreography video archives and showcase footage.
- **Box** (`box-api`): Crestline Consulting shared workspace storage for HR-adjacent docs only.
- **Microsoft Teams** (`microsoft-teams-api`): Hospital-side chat when administration reaches off-shift. Replies stay brief and clinical.
- **Okta** (`okta-api`): Identity layer for the Crestline workspace. Surface MFA prompts; never share recovery codes.

#### Studio Money: Payments & Bookkeeping
- **Stripe** (`stripe-api`): Class card payments, private lesson invoices, and studio merch checkout.
- **Square** (`square-api`): Backup point-of-sale for door payments at the Halloween social and pop-up workshops.
- **PayPal** (`paypal-api`): Older student accounts that still pay by PayPal. Maintain for legacy roster.
- **Calendly** (`calendly-api`): Private lesson bookings for advanced students. Block during ER shift hours.
- **Eventbrite** (`eventbrite-api`): Spring Showcase ticketing and Halloween swing social RSVPs.
- **DocuSign** (`docusign-api`): Studio rental agreement with Karen and student liability waivers.
- **QuickBooks** (`quickbooks-api`): Dance class income, rental expenses, and quarterly self-employed tax tracking.
- **Xero** (`xero-api`): Alternate ledger he is evaluating before the Powers Swing Academy launch.

#### Studio Outreach: Marketing, Email & CRM
- **Mailchimp** (`mailchimp-api`): Monthly studio newsletter to the student list. Send Sundays before the class week.
- **Klaviyo** (`klaviyo-api`): Behavioural campaigns to lapsed students who skipped two weeks of classes.
- **ActiveCampaign** (`activecampaign-api`): Welcome sequence for new beginner students after first-class signup.
- **HubSpot** (`hubspot-api`): Lightweight student CRM for follow-up and beginner re-engagement.
- **Salesforce** (`salesforce-api`): Read-only via Karen for studio vendor relationships. Do not write.
- **Intercom** (`intercom-api`): Future live chat on the Powers Swing Academy site once it launches.
- **SendGrid** (`sendgrid-api`): Transactional receipts and class confirmation emails to students.
- **Mailgun** (`mailgun-api`): Backup transactional sender for class reminders if SendGrid fails.
- **Twilio** (`twilio-api`): SMS class reminders to the student roster on the morning of class.
- **Google Analytics** (`google-analytics-api`): Tracks Instagram-to-site conversion for class signups.
- **Mixpanel** (`mixpanel-api`): Studio site funnel from class page to checkout.
- **Amplitude** (`amplitude-api`): Funnel analysis once the new Powers Swing Academy site is live.
- **PostHog** (`posthog-api`): Lightweight events on the WordPress studio blog.

#### Studio Site & Commerce
- **WordPress** (`wordpress-api`): Powers Swing Academy blog drafts on lessons learned teaching.
- **Webflow** (`webflow-api`): Landing page draft for the Powers Swing Academy launch.
- **Contentful** (`contentful-api`): Studio site copy editing for class descriptions and instructor bios.
- **Algolia** (`algolia-api`): Search on the studio class catalog once the site launches.
- **Figma** (`figma-api`): Studio brand mocks, class flyer layouts, and showcase posters.
- **BigCommerce** (`bigcommerce-api`): Reference for the future studio merch storefront.
- **WooCommerce** (`woocommerce-api`): WordPress-friendly option for the merch checkout he is leaning toward.
- **Shippo** (`shippo-api`): Dance studio T-shirt mail-outs to out-of-area students.
- **Amazon Seller** (`amazon-seller-api`): Read-only window into the merch market when pricing studio T-shirts.
- **Etsy** (`etsy-api`): Custom hand-painted dance shoes and wedding gift sourcing.

#### Notes, Planning & Class Roster
- **Notion** (`notion-api`): Powers Swing Academy business plan and per-class lesson notes.
- **Obsidian** (`obsidian-api`): Private journal vault that mirrors his paper journaling habit. Do not export.
- **Airtable** (`airtable-api`): Student roster, class attendance, waitlist, and showcase cast list.
- **Trello** (`trello-api`): Wedding best-man planning board for Ty and Amanda in October.
- **Asana** (`asana-api`): Showcase prep tracked with Derek week by week.
- **Monday** (`monday-api`): Backup project board for instructor onboarding once the studio hires.
- **Jira** (`jira-api`): Mirrors Linear for the Powers Swing Academy launch checklist.
- **Linear** (`linear-api`): Primary studio launch checklist shared with Karen and Derek.
- **Confluence** (`confluence-api`): Studio policies and instructor handbook drafts.
- **Typeform** (`typeform-api`): Beginner class intake form on the studio site.
- **Slack** (`slack-api`): Tempo Swing instructor workspace with Karen and Derek.
- **Google Classroom** (`google-classroom-api`): Share teaching materials and footwork drills with beginner students between classes.

#### Music, Video & Streaming
- **Spotify** (`spotify-api`): Class playlists, run playlists, and the Monday beginner-class opener track he refuses to replace.
- **YouTube** (`youtube-api`): Choreography references and posting class highlight reels.
- **Vimeo** (`vimeo-api`): Private choreography reels for students to review at home before class.
- **TMDB** (`tmdb-api`): True crime and action thriller watchlist for night-shift recovery.
- **Twitch** (`twitch-api`): Watches dance creators who stream practice sessions during night-shift downtime.
- **Zoom** (`zoom-api`): Virtual private dance lessons for out-of-area students during off-rotation weeks.

#### Social Presence & Community
- **Instagram** (`instagram-api`): @westcoast.lee posts, drafts, and engagement for studio marketing.
- **Pinterest** (`pinterest-api`): Costume and studio decor ideas for the Halloween social.
- **Twitter** (`twitter-api`): Lurking on WCS event accounts; rarely posts in his own voice.
- **LinkedIn** (`linkedin-api`): Nursing connections and FNP program research.
- **Reddit** (`reddit-api`): r/Nursing and r/WestCoastSwing reading without posting.
- **Discord** (`discord-api`): Local Bend dance community server, moderated with Derek.
- **WhatsApp** (`whatsapp-api`): Out-of-state dance contacts in Portland and Seattle.
- **Telegram** (`telegram-api`): International dance friends who prefer Telegram for festival coordination.

#### Health, Home & Outdoors
- **MyFitnessPal** (`myfitnesspal-api`): Running mileage and protein tracking in the weeks before a showcase. No calorie pressure.
- **Strava** (`strava-api`): Deschutes River Trail morning loops and segment tracking, same route, same direction.
- **Ring** (`ring-api`): Front door camera on the apartment. Surface motion only when he is away.
- **NASA** (`nasa-api`): Smoke-day satellite imagery during Oregon wildfire season to call runs early.
- **OpenWeather** (`openweather-api`): Pre-run check for the Deschutes River Trail before the 6 AM loop.
- **OpenLibrary** (`openlibrary-api`): FNP textbook references and a nursing leadership reading list.

#### Local Life: Maps, Food & Errands
- **Google Maps** (`google-maps-api`): Routes between Summit View Medical Center, Tempo Swing Studio, and Barbara's apartment.
- **Yelp** (`yelp-api`): Bend restaurant scouting, with weight on the Thai-food rotation.
- **Uber** (`uber-api`): Late-night ride home from the Portland convention bars in August.
- **DoorDash** (`doordash-api`): Post-night-shift delivery when the kitchen is closed and the run has to wait.
- **Instacart** (`instacart-api`): Sunday cookout meat run before Barbara arrives.

#### Travel, Tickets & Shipping
- **Airbnb** (`airbnb-api`): Portland convention weekend stays in mid-August.
- **Amadeus** (`amadeus-api`): Spokane flight watch for the long-planned trip with Barbara to see Grandpa Walter.
- **Ticketmaster** (`ticketmaster-api`): Concerts and event tickets, especially gifts for Barbara.
- **FedEx** (`fedex-api`): Vendor inbound for studio props, decor, and showcase costumes.
- **UPS** (`ups-api`): Vendor inbound for dance shoes and merch shipments.
- **Zillow** (`zillow-api`): Watching commercial spaces in Bend for the future Powers Swing Academy location.

#### Banking, Crypto & Brokerage
- **Plaid** (`plaid-api`): Links checking, savings, and the Powers Swing Academy fund for budgeting and transfers.
- **Coinbase** (`coinbase-api`): Tiny holdings he set up after Brandon talked him into it.
- **Binance** (`binance-api`): Reference account only; never funded.
- **Kraken** (`kraken-api`): Reference account only; never funded.
- **Alpaca** (`alpaca-api`): Small brokerage account to test the waters before any real allocation.

#### Engineering & Hospital Reference (Read-Only)
- **GitHub** (`github-api`): Read-only follow on Ty's side projects so he knows what to ask about at Sunday cookout.
- **GitLab** (`gitlab-api`): Watching a friend's WCS music-sync side project.
- **Sentry** (`sentry-api`): Monitors the studio website for downtime before classes.
- **Datadog** (`datadog-api`): Studio website uptime dashboards.
- **Cloudflare** (`cloudflare-api`): Powers Swing Academy domain DNS and basic page caching.
- **Kubernetes** (`kubernetes-api`): Reference only; context for talking shop with Ty's ICU tech friends.
- **PagerDuty** (`pagerduty-api`): Not personal; awareness only because Ty rotates on call in the ICU.
- **Segment** (`segment-api`): Pipes studio site events to analytics tools once the new site launches.
- **ServiceNow** (`servicenow-api`): Hospital IT ticketing. Reference only; never log in on his behalf.
- **BambooHR** (`bamboohr-api`): Hospital HR system. Reference only; never touch directly.
- **Greenhouse** (`greenhouse-api`): Future use when hiring the first studio instructor.
- **Gusto** (`gusto-api`): Future payroll for studio instructors once the studio opens.
- **Zendesk** (`zendesk-api`): Reference only; if a vendor opens a support ticket, surface and confirm before any reply.
- **Freshdesk** (`freshdesk-api`): Student support inbox for class issues and refunds.

#### Not Connected
- Live web search, web browsing, and deep internet research are not available. The agent works only from connected mock APIs and stored memory.
- Summit View Medical Center clinical systems (EHR, scheduling, patient records) are not connected. All clinical and shift-internal data stays inside hospital walls.
- Hospital HR and administration systems are not connected for write actions. Read-only references only.
- Barbara's, Walter's, and any family member's private accounts are not connected.
- Ty's, Derek's, and Karen's private accounts are not connected; communication runs through their direct contact info only.
- Venmo, iMessage, TikTok, and Facebook are personal tools that live on his phone outside this mock-API surface.
