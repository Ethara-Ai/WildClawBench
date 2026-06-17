# Tools: Jae Chandler

## Tool Usage

### General Agent Capabilities

- **Wide Research**: Gather and compare information across connected services and stored memory, such as supplier options, product specs, and code references, without accessing the open web.
- **Documents**: Create and edit estimates, invoices, contracts, and client notices, and keep them organized in the data folder.
- **Memory Search** (`memory_search`): Always search before tasks involving people, dates, or past context.

### Connected Services

#### Communication & Coordination

- **Gmail** (`gmail-api`): Primary inbox at jae.chandler@Finthesiss.ai for client estimates, supplier orders, and permit correspondence.
- **Outlook** (`outlook-api`): Secondary inbox watched only for the occasional general contractor or property manager who works in Outlook.
- **Google Calendar** (`google-calendar-api`): The master schedule for job sites, client appointments, and the family calendar shared with Mina.
- **Calendly** (`calendly-api`): Booking link for new-client estimate visits so homeowners pick a slot without phone tag.
- **WhatsApp** (`whatsapp-api`): Occasional thread with a supplier rep and one commercial client who prefers it; keep it light.
- **Telegram** (`telegram-api`): Rarely used, watched only for a parts-sourcing group a fellow contractor invited him to.
- **Slack** (`slack-api`): Read-only window into a general contractor's project workspace on the Harborview Condos job.
- **Microsoft Teams** (`microsoft-teams-api`): Used only when a property-management client schedules a walkthrough call through Teams.
- **Discord** (`discord-api`): Lurks a classic-truck restoration server for C10 wiring tips; never posts on his behalf.
- **Zoom** (`zoom-api`): Occasional NABCEP study webinars and the rare remote client consultation.
- **Twilio** (`twilio-api`): Sends appointment and inspection-window text reminders to clients from the business number.
- **SendGrid** (`sendgrid-api`): Delivers invoice and estimate emails reliably so they do not land in client spam folders.
- **Mailgun** (`mailgun-api`): Backup transactional email for invoice receipts if SendGrid delivery stalls.

#### Books, Payments & Investing

- **QuickBooks** (`quickbooks-api`): The business books for Chandler Electric LLC: invoicing, expenses, payroll records, and CPA-ready reports.
- **Xero** (`xero-api`): Read-only mirror the CPA uses for quarterly review; cross-check figures against QuickBooks.
- **Stripe** (`stripe-api`): Card payments on invoices for clients who prefer to pay online rather than by check.
- **Square** (`square-api`): Tap-to-pay on the job site for small residential service calls and deposits.
- **PayPal** (`paypal-api`): Alternate payment method for a handful of repeat clients and occasional tool purchases.
- **Plaid** (`plaid-api`): Securely links the business and personal bank accounts for balance checks before large supply orders.
- **Coinbase** (`coinbase-api`): A small long-term crypto holding he watches occasionally; no active trading.
- **Binance** (`binance-api`): Dormant account; monitor only, no trades without explicit approval.
- **Kraken** (`kraken-api`): Dormant account held alongside Coinbase; balance checks only.
- **Alpaca** (`alpaca-api`): Read-only view of a modest brokerage position earmarked toward the SEP-IRA goal.

#### Jobs, Estimates & Documents

- **Jira** (`jira-api`): Tracks the multi-week Harborview Condos panel-upgrade tasks unit by unit.
- **Linear** (`linear-api`): Lightweight punch-list tracking for the Bay View historic rewire's open items.
- **Trello** (`trello-api`): Visual board of active jobs from estimate to final inspection.
- **Asana** (`asana-api`): Shared task list with a general contractor on larger commercial jobs.
- **Monday** (`monday-api`): Crew assignment board showing who is on which site each day.
- **Notion** (`notion-api`): Personal workspace for the NABCEP study plan and supplier notes.
- **Obsidian** (`obsidian-api`): Local notes vault for code references and wiring diagrams he keeps for himself.
- **Airtable** (`airtable-api`): Client and job database with addresses, panel details, and warranty dates.
- **Confluence** (`confluence-api`): Read access to a commercial client's project documentation when required on a job.
- **Typeform** (`typeform-api`): Intake form for new clients to describe a job before the estimate visit.
- **Docusign** (`docusign-api`): Sends contracts and change orders for client e-signature.

#### Hiring & Payroll

- **BambooHR** (`bamboohr-api`): Light HR records for the three-person crew: certifications, time off, and apprentice hours.
- **Greenhouse** (`greenhouse-api`): Used only when hiring, to track applicants for a journeyman or apprentice opening.
- **Gusto** (`gusto-api`): Runs payroll and handles tax filings for Ryan, Danny, and Jake.
- **Okta** (`okta-api`): Single sign-on a commercial client requires before granting site-system access.

#### Supplies, Shipping & Travel

- **Amazon Seller** (`amazon-seller-api`): Watches a dormant side listing for surplus tools and fittings; no active storefront.
- **Instacart** (`instacart-api`): Household grocery runs for Mina when the week gets tight.
- **Doordash** (`doordash-api`): Occasional crew lunch on a long site day, within the spending threshold.
- **Uber** (`uber-api`): Backup ride when the truck is in the shop.
- **Fedex** (`fedex-api`): Tracks specialty electrical parts shipped from out-of-state suppliers.
- **Ups** (`ups-api`): Tracks routine supply-house deliveries to the workshop.
- **Shippo** (`shippo-api`): Generates return labels for defective fixtures and breakers.
- **Amadeus** (`amadeus-api`): Researches flights and logistics for the long-planned family trip to Seoul.

#### Field Maps, Weather & Property

- **Google Maps** (`google-maps-api`): Routes between job sites, supply houses, and client addresses around greater Milwaukee.
- **OpenWeather** (`openweather-api`): Checks conditions before rough-in days and outdoor work, especially in Wisconsin winter.
- **Yelp** (`yelp-api`): Looks up client-recommended subcontractors and the occasional new lunch spot near a site.
- **Zillow** (`zillow-api`): Pulls property age and details to anticipate wiring vintage before a rewire estimate.
- **Ring** (`ring-api`): Monitors the workshop and driveway camera where tools and materials are stored.
- **Airbnb** (`airbnb-api`): Researches lodging for the Door County fishing trip and a future Seoul stay.

#### Customer Support & CRM

- **Zendesk** (`zendesk-api`): Tracks warranty and callback requests from past clients so nothing slips.
- **Freshdesk** (`freshdesk-api`): Backup ticketing for service requests routed through the website form.
- **Intercom** (`intercom-api`): Handles website chat inquiries from prospective clients during business hours.
- **ServiceNow** (`servicenow-api`): Read access to a commercial client's facilities-ticket system on contracted work.
- **HubSpot** (`hubspot-api`): The client pipeline: leads, estimates outstanding, and follow-up reminders.
- **Salesforce** (`salesforce-api`): Read access to a property-management partner's vendor records when coordinating jobs.

#### Storefront, Marketing & Analytics

- **Etsy** (`etsy-api`): Watches a dormant listing where Mina occasionally sells small woodworking pieces.
- **BigCommerce** (`bigcommerce-api`): Not actively used; reserved if the business ever sells surplus materials online.
- **WooCommerce** (`woocommerce-api`): Backs a simple request-a-quote page on the Chandler Electric website.
- **Mailchimp** (`mailchimp-api`): Sends an occasional seasonal note to past clients about panel safety and maintenance.
- **Klaviyo** (`klaviyo-api`): Dormant; reserved for any future client follow-up automation.
- **Activecampaign** (`activecampaign-api`): Dormant marketing automation; not in active use.
- **Segment** (`segment-api`): Aggregates website visitor events if the quote page is ever instrumented.
- **Amplitude** (`amplitude-api`): Unused product analytics; reserved, not relevant to daily work.
- **PostHog** (`posthog-api`): Unused analytics; reserved for any future website experiment.
- **Mixpanel** (`mixpanel-api`): Unused analytics; reserved, no current relevance.
- **Google Analytics** (`google-analytics-api`): Tracks traffic to the Chandler Electric website to see which services draw inquiries.
- **Eventbrite** (`eventbrite-api`): Registers for IBEW trainings and the Korean Festival when tickets are required.
- **Ticketmaster** (`ticketmaster-api`): Buys Brewers tickets to pass down to friends and family through the season.

#### Files, Faith & Learning

- **Data folder** (`data/`): Local folder holding your source artifacts (estimates, invoices, permits, quote PDFs, rate sheets, cover art, job photos) and where you save the documents you produce.
- **Dropbox** (`dropbox-api`): Shares large job-site photo sets with general contractors and clients.
- **Box** (`box-api`): Read access to a commercial client's document portal for contracted projects.
- **Google Classroom** (`google-classroom-api`): Read-only window into Derek's and Yuna's school assignments and deadlines.
- **OpenLibrary** (`openlibrary-api`): Looks up NABCEP and electrical code study references and the occasional Tom Clancy title.

#### Health & Movement

- **MyFitnessPal** (`myfitnesspal-api`): Tracks the diet changes from his doctor without turning it into calorie pressure.
- **Strava** (`strava-api`): Logs the Monday, Wednesday, and Friday morning walks with Mina.

#### Media & Downtime

- **Spotify** (`spotify-api`): Classic rock in the garage and country while working; the account Yuna set up for him.
- **YouTube** (`youtube-api`): C10 restoration walkthroughs and the occasional code-update explainer.
- **TMDB** (`tmdb-api`): Looks up titles and runtimes for Friday family movie night.
- **Vimeo** (`vimeo-api`): Watches trade-association technique videos shared by the IBEW.
- **Twitch** (`twitch-api`): Read-only glance at the streamers Derek talks about so he can keep up.
- **Reddit** (`reddit-api`): Reads the electricians and Brewers communities for shop talk and scores.
- **Twitter** (`twitter-api`): Follows the Brewers, the Packers, and trade news; observer only.
- **Instagram** (`instagram-api`): Watches local trade and Korean-community accounts; never posts on his behalf.
- **Pinterest** (`pinterest-api`): Saves backyard grill builds and basement-finishing ideas for home projects.

#### Web, Site & Dev Tools

- **GitHub** (`github-api`): Read-only, following the open-source home-automation projects Derek has started poking at.
- **GitLab** (`gitlab-api`): Read-only mirror of the same hobby projects when a maintainer hosts there.
- **Sentry** (`sentry-api`): Alerts if the Chandler Electric website's quote form throws errors.
- **Datadog** (`datadog-api`): Basic uptime monitoring for the business website.
- **PagerDuty** (`pagerduty-api`): Notifies him only if the website goes fully down during business hours.
- **Kubernetes** (`kubernetes-api`): Not operated by Jae; reserved through the web host that runs the site.
- **Cloudflare** (`cloudflare-api`): Protects and speeds up the Chandler Electric website and its DNS.
- **Algolia** (`algolia-api`): Powers search on the website's service and FAQ pages.
- **Contentful** (`contentful-api`): Stores the website's service descriptions and project photos.
- **Webflow** (`webflow-api`): The platform the Chandler Electric marketing site is built on.
- **WordPress** (`wordpress-api`): Hosts the occasional blog post on home electrical safety.
- **Figma** (`figma-api`): Read-only view of the web designer's mockups for site updates.
- **NASA** (`nasa-api`): Pulls solar-irradiance and daylight data to sanity-check residential solar feasibility.
- **LinkedIn** (`linkedin-api`): Maintains a light professional profile and watches local contractor and supplier updates.

#### Not Connected

- Live web search, web browsing, and deep internet research are not available. You work only from connected mock APIs and stored memory.
- Clients' internal building-management and security systems beyond the explicit read access noted above.
- City of Milwaukee permitting and inspection systems; Jae files and speaks with inspectors directly.
- Social media posting on Jae's behalf; you may draft content for review but never publish.
- Trade-specific estimating or load-calculation software; those calculations stay with Jae.
