# Tools: Midori Kelley

## Tool Usage

### General Agent Capabilities

- **Documents**: Draft and edit clinic notes, rodeo entry paperwork, expense summaries, and family logistics in her workspace files.
- **Memory Search** (`memory_search`): Always search before tasks involving people, dates, or past context.

### Connected Services

Only the 25 services below are wired to live mock data. Any other tool the agent might be tempted to reach for is not connected and should not be called.

#### Communication & Messaging
- **Gmail** (`gmail-api`): Her primary inbox at midori.kelley@voissync.ai for clinic, vendors, and rodeo registration mail.
- **Outlook** (`outlook-api`): Read-only bridge to clinic staff who send schedules and lab results from Outlook accounts.
- **WhatsApp** (`whatsapp-api`): Quick messages with Tara, Sachiko, and rodeo travel partners when texting is easier than a call.
- **Twilio** (`twilio-api`): Sends SMS reminders that survive spotty service, like farrier and on-call alerts.
- **Slack** (`slack-api`): The Rimrock Valley clinic workspace for shift coverage and case hand-offs.
- **Discord** (`discord-api`): A barrel racing community server where riders trade arena conditions and results.
- **Zoom** (`zoom-api`): Video calls for vet CE webinars and the rare remote check-in tied to Kenji's recovery house.

#### Calendar, Events & Tickets
- **Google Calendar** (`google-calendar-api`): Her master schedule of clinic shifts, on-call weekends, rodeo entries, and family days.
- **Eventbrite** (`eventbrite-api`): Registration and tickets for rodeos, clinics, and the occasional vet workshop.

#### Files, Notes & Paperwork
- **Dropbox** (`dropbox-api`): Backup of large files like radiograph images shared from the clinic.
- **Notion** (`notion-api`): Her quiet GRE and DVM-application study tracker.
- **Airtable** (`airtable-api`): Tracks Hikari's competition results, supplement schedule, farrier cycle, and the clinic's vaccine inventory.

#### Maps, Weather & Travel
- **Google Maps** (`google-maps-api`): Routes trailer hauls, ranch-call directions, and drive times to Klamath Falls.
- **OpenWeather** (`openweather-api`): Critical forecasts for trailer hauls, ranch work, and rodeo days.

#### Money & Banking
- **Plaid** (`plaid-api`): Links her Cascade Community Credit Union checking and savings for balance checks.
- **Stripe** (`stripe-api`): Processes card payments from her equine bodywork clients.

#### Shopping, Groceries & Shipping
- **FedEx** (`fedex-api`): Tracks shipments of supplements, tack parts, and inbound vaccine deliveries to Henderson and other ranch clients.

#### Social, Community & Marketing
- **Instagram** (`instagram-api`): Posts race results and horse photos and follows the barrel racing community.
- **Mailchimp** (`mailchimp-api`): The clinic's newsletter to ranch clients about seasonal herd health.

#### Clinic Operations & Client Records
- **Salesforce** (`salesforce-api`): The clinic's client and patient records for the horses and herds she treats.
- **HubSpot** (`hubspot-api`): Tracks outreach to ranch clients and the Zoetis distributor portal mirror for vendor orders (deals, vendor_orders).
- **BambooHR** (`bamboohr-api`): Her clinic HR portal for PTO requests, who's-out roster, and on-call scheduling.

#### Project Boards & Knowledge
- **Trello** (`trello-api`): A simple board for rodeo-season travel and entry planning.
- **Linear** (`linear-api`): Watches issues on her brother Kenji's open-source coding project so she can ask about it.
- **Confluence** (`confluence-api`): The clinic's internal protocols and standard-of-care documentation, including the bovine vaccination protocol.

#### Not Connected
- Live web search, web browsing, and deep internet research are not available. The agent works only from the 26 connected mock APIs above and stored memory.
- Rimrock Valley's internal clinical systems beyond what is listed here are not connected; in shared contexts treat them as unavailable.
- Her family's private accounts and Kenji's recovery-house systems are off limits.
- No financial trading is executed; Stripe and Plaid are read or accept-payments only, never order entry.
