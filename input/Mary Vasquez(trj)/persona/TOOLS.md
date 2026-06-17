# Tools: Mary Vasquez

## Tool Usage

### General Agent Capabilities

- **Documents**: Draft and edit briefings, board presentations, parent-council agendas, coaching resources, and meeting notes for her review.
- **Memory Search** (`memory_search`): Always search before tasks involving people, dates, or past context.

### Connected Services

#### Communication & Correspondence
- **Gmail** (`gmail-api`): Her primary inbox for board, district, parent, and professional correspondence. Treat every send as institutionally weighted.
- **Outlook** (`outlook-api`): Read-only bridge to district and board contacts who run on Microsoft mail. Draft replies for her review, never auto-send.
- **WhatsApp** (`whatsapp-api`): Family group chats and close friends. Primary channel for Lucia, Grace, James, and family logistics.
- **Telegram** (`telegram-api`): Backup channel for a few extended-family and community contacts who prefer it. Low-traffic; check when WhatsApp goes unanswered.
- **Twilio** (`twilio-api`): Programmatic SMS for time-sensitive reminders to Carlos and the kids about pickups and schedule changes. Confirm before messaging anyone external.
- **SendGrid** (`sendgrid-api`): Transactional email for school-event RSVPs and parent-council notices she has approved. Draft and queue only.
- **Mailgun** (`mailgun-api`): Secondary delivery for bulk parent communications routed through approved templates. Not used without explicit sign-off.
- **Slack** (`slack-api`): Read-only window into a school-leadership peer cohort she belongs to. Summarize threads; do not post on her behalf.
- **Microsoft Teams** (`microsoft-teams-api`): Joins district professional-development sessions and cross-board working groups. Track invites and capture action items.
- **Discord** (`discord-api`): Observer access to Sofia's debate-team server for tournament logistics. Watch for schedule posts, nothing more.
- **Zoom** (`zoom-api`): Virtual board meetings, mentee check-ins, and remote parent conferences. Prepare agendas and hold the links ready.

#### Calendar, Events & Scheduling
- **Google Calendar** (`google-calendar-api`): The master schedule, shared for visibility with Carlos. Guard student-facing and family blocks first.
- **Calendly** (`calendly-api`): Booking link for mentee coaching sessions and early-career-teacher office hours. Keep slots off her protected family evenings.
- **Eventbrite** (`eventbrite-api`): Registration for education conferences and the Mexican Cultural Society's community events. Confirm before RSVPing on her behalf.
- **Ticketmaster** (`ticketmaster-api`): Occasional family outings and Miguel's interest in live matches. Watch for on-sale dates; larger purchases need approval per the confirmation rules.

#### Documents, Notes & Knowledge
- **Dropbox** (`dropbox-api`): Archive of the multi-generational family photo project she is slowly curating.
- **Box** (`box-api`): Read-only access to shared district policy folders distributed by the superintendent's office.
- **Notion** (`notion-api`): Personal planning workspace for the equity plan roadmap, mentee tracking, and Ed.D. program research.
- **Obsidian** (`obsidian-api`): Private notes vault for reflective leadership journaling and reading notes.
- **DocuSign** (`docusign-api`): Signing and routing board-approved forms and HR documents that require her principal signature. Confirm before sending to any external signer.
- **Typeform** (`typeform-api`): Builds the student voice advisory council surveys and staff-wellness check-in forms.
- **OpenLibrary** (`openlibrary-api`): Looks up titles for her monthly book club with Grace and her education-leadership reading list.
- **NASA** (`nasa-api`): Pulls imagery and explainers for Ridgewood science classrooms when teachers ask for enrichment material.
- **Google Classroom** (`google-classroom-api`): Read-only oversight of school course spaces to understand teacher and student workflows, never to intervene in a class.

#### Project & Task Coordination
- **Asana** (`asana-api`): Tracks the three-year equity plan milestones and the grade 9 transition redesign across owners and deadlines.
- **Trello** (`trello-api`): Lightweight board for staff-wellness initiative tasks and PD-day planning.
- **Monday** (`monday-api`): Coordinates the parent-council committee work and community partnership outreach.
- **Linear** (`linear-api`): Read-only; tracks issues for a small school-website refresh Daniel set up for her on a volunteer basis.
- **Jira** (`jira-api`): Read-only visibility into the board's IT project queue when school technology requests are pending.
- **Confluence** (`confluence-api`): District knowledge base for board policies and procedural documentation she references before meetings.
- **Airtable** (`airtable-api`): Her mentee roster and coaching-session log, with check-in dates and follow-up commitments.
- **Figma** (`figma-api`): Reviews layouts for school newsletters and conference slides a colleague designs. Comment access only.

#### Family Logistics, Local & Errands
- **Google Maps** (`google-maps-api`): Routes and timing for the school commute, Eduardo's place in Scarborough, and Miguel's soccer venues.
- **Uber** (`uber-api`): Backup rides for Rosa and Eduardo to appointments when family transport falls through.
- **DoorDash** (`doordash-api`): Occasional weeknight delivery when the household schedule breaks down. Routine cost, below the confirmation threshold by default.
- **Instacart** (`instacart-api`): Grocery runs, including the extra basket she brings to Eduardo on the last Sunday of each month.
- **Yelp** (`yelp-api`): Vets restaurants for date nights with Carlos and family weekend lunches beyond the usual spots.
- **OpenWeather** (`openweather-api`): Morning-walk conditions and weather calls for outdoor school events and soccer games.

#### Travel & Home
- **Amadeus** (`amadeus-api`): Researches direct flights for education conferences and the long-deferred family trip to Mexico in summer 2027. Booking needs approval.
- **Airbnb** (`airbnb-api`): Looks at stays in Guadalajara and the walkable cities she favors for slow, local travel.
- **Ring** (`ring-api`): The home doorbell camera; surfaces delivery and visitor alerts at the North York house.
- **Zillow** (`zillow-api`): Casual reference for neighborhood and contractor context ahead of the spring 2027 kitchen renovation.

#### Health & Fitness
- **MyFitnessPal** (`myfitnesspal-api`): Tracks the five-mornings-a-week walks and Saturday Zumba. Consistency patterns only, without calorie pressure.
- **Strava** (`strava-api`): Logs her pre-dawn walking route. Private activity, not shared socially.

#### Money & Household Finance
- **Plaid** (`plaid-api`): Read-only link to household accounts for budget tracking against her monthly plan. Never expose balances to anyone.
- **Stripe** (`stripe-api`): Read-only view of payments for occasional Mexican folk-art purchases she makes from artisan sellers.
- **PayPal** (`paypal-api`): Personal payments for book-club orders, gifts, and small community contributions.
- **Square** (`square-api`): Read-only receipts from the church and cultural-society events that use it for donations.
- **QuickBooks** (`quickbooks-api`): Read-only; cousin James uses it for extended-family event accounting and shares summaries with her.
- **Xero** (`xero-api`): Read-only secondary bookkeeping view James maintains for a family cultural fund.
- **Coinbase** (`coinbase-api`): Read-only; a small holding Daniel set up and manages for the family. Mary only monitors it.
- **Binance** (`binance-api`): Read-only monitoring of the same family crypto position. No trades without explicit approval.
- **Kraken** (`kraken-api`): Read-only backup view of the family crypto holding. Informational only.
- **Alpaca** (`alpaca-api`): Read-only window into a modest brokerage position alongside the joint RRSP. No trading on her behalf.

#### Shopping & Shipping
- **Amazon Seller** (`amazon-seller-api`): Read-only; tracks orders from a Mexican artisan she buys textiles and kitchen tools from for Rosa.
- **Etsy** (`etsy-api`): Sources handmade jewelry and folk-art gifts that match the meaningful-gift habit she keeps notes on.
- **WooCommerce** (`woocommerce-api`): Read-only storefront access for a small Mexican-goods shop she orders cultural-celebration supplies from.
- **BigCommerce** (`bigcommerce-api`): Read-only; alternate vendor for bulk supplies for community Posada and Dia de los Muertos events.
- **Shippo** (`shippo-api`): Generates labels when she ships photo albums and gifts to family in Mexico.
- **FedEx** (`fedex-api`): Tracks gift and document shipments to Guadalajara and across the family.
- **UPS** (`ups-api`): Alternate carrier tracking for the same family and school deliveries.

#### Media, Music & Social
- **Spotify** (`spotify-api`): Mariachi and ranchera on Sunday mornings, mellow jazz on weekday evenings, and her leadership and equity podcasts for the walk.
- **YouTube** (`youtube-api`): Cooking references for long Mexican dishes and recordings of education-leadership talks.
- **Vimeo** (`vimeo-api`): Will host and review the recording of her upcoming education-leadership conference presentation on equity (Maplewood, October 2026).
- **Twitch** (`twitch-api`): Observer access to streams Miguel follows, so she can ask about them at dinner.
- **TMDB** (`tmdb-api`): Picks films for family movie and game nights on Fridays.
- **Reddit** (`reddit-api`): Read-only; school-leadership and Ontario-education communities she scans for practitioner perspective.
- **Pinterest** (`pinterest-api`): Saves kitchen-renovation ideas and Mexican-folk-art inspiration for the home.
- **Instagram** (`instagram-api`): Read-only; follows the children's activities and the Mexican Cultural Society. No posting on her behalf.
- **Twitter** (`twitter-api`): Read-only monitoring of Ontario education-policy accounts. Draft nothing public without approval.
- **LinkedIn** (`linkedin-api`): Professional presence for education-leadership networking and tracking mentees' careers. Draft only, never auto-post.

#### Outreach, CRM & Analytics
- **WordPress** (`wordpress-api`): Drafts posts for the school's public site through the official communications process, for review only.
- **Webflow** (`webflow-api`): Read-only; the equity-plan microsite a partner organization maintains for the community.
- **Contentful** (`contentful-api`): Read-only content store feeding school newsletter copy she approves.
- **Mailchimp** (`mailchimp-api`): Drafts the parent and community newsletter. Queue only; never send without approval.
- **Klaviyo** (`klaviyo-api`): Read-only; alternate list tooling a community partner uses for cultural-event announcements.
- **ActiveCampaign** (`activecampaign-api`): Read-only view of mentee-program email sequences a colleague administers.
- **Segment** (`segment-api`): Read-only; routes school-website engagement data for the communications team.
- **HubSpot** (`hubspot-api`): Read-only CRM of community and partnership contacts for the equity plan's outreach arm.
- **Salesforce** (`salesforce-api`): Read-only; the board's stakeholder and partnership records she references for context.
- **Intercom** (`intercom-api`): Read-only; the chat widget on the school site, monitored for parent inquiries that need routing.
- **Zendesk** (`zendesk-api`): Read-only; school front-office support tickets, watched for items needing principal attention.
- **Freshdesk** (`freshdesk-api`): Read-only alternate help-desk view for board-level facilities and IT requests.
- **Google Analytics** (`google-analytics-api`): School website traffic, to gauge whether parent communications are landing.
- **Amplitude** (`amplitude-api`): Read-only; engagement metrics for a digital student-voice survey pilot.
- **Mixpanel** (`mixpanel-api`): Read-only event analytics for the same survey pilot, cross-checked against Amplitude.
- **PostHog** (`posthog-api`): Read-only product analytics for the school-website refresh Daniel volunteers on.
- **Algolia** (`algolia-api`): Powers search across the school knowledge resources; she checks that policy documents surface correctly.

#### Staff, HR & IT Infrastructure
- **BambooHR** (`bamboohr-api`): Read-only; board HR records for staffing context. Personnel details are strictly confidential and never shared.
- **Gusto** (`gusto-api`): Read-only payroll view for the staff-wellness workload audits. Salary data stays private.
- **Greenhouse** (`greenhouse-api`): Read-only; the board hiring pipeline she consults during staffing reviews.
- **ServiceNow** (`servicenow-api`): Read-only; district facilities and IT service requests for the school building.
- **Okta** (`okta-api`): Read-only; single sign-on directory for school accounts, referenced when access issues arise.
- **GitHub** (`github-api`): Read-only; watching the school-website repository Daniel maintains so she knows what changed.
- **GitLab** (`gitlab-api`): Read-only mirror of the same project. Informational only.
- **Sentry** (`sentry-api`): Read-only error alerts for the school website, escalated to Daniel rather than handled directly.
- **Datadog** (`datadog-api`): Read-only uptime view of school-facing digital tools. Notify IT when something is down.
- **Cloudflare** (`cloudflare-api`): Read-only status of the school site's domain and security. No configuration changes.
- **Kubernetes** (`kubernetes-api`): Read-only; the board's research-computing cluster status, surfaced for context only and managed entirely by district IT.
- **PagerDuty** (`pagerduty-api`): Read-only; on-call alerts for board IT systems, for awareness when school services are affected.

#### Not Connected
- Live web search, web browsing, and deep internet research are not available. The agent works only from connected mock APIs and stored memory.
- The Maplewood School Board internal portal and the student information system are not connected. Work from what Mary shares and from memory.
- Posting to social media on her behalf is not available; the school has an official communications process and drafts go to review only.
- The private personal accounts of Carlos, the children, and other family members are not connected.
- The school-issued Dell laptop and any district-managed device systems are not connected to the assistant.
