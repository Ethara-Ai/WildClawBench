# Tools: Connected Integrations

## Email and Calendar

- **Gmail** (`gmail-api`): Primary personal inbox at ruth.armstrong@Finthesiss.ai. Council briefings, OPUQ notices, community partner correspondence, and family logistics all flow through here.
- **Google Calendar** (`google-calendar-api`): Coordinates Ruth's consultations, council briefings, Emile's school events, and date nights with Marc. Color-coded by stream (city work, family, professional development, personal).
- **Outlook** (`outlook-api`): Secondary inbox monitored when external partners (NGOs, federal contacts) prefer Microsoft. Not connected to her city work account, which stays sandboxed.

## Communication and Messaging

- **WhatsApp** (`whatsapp-api`): Default channel with Marc, Nathalie, and the Plateau parent group chat. Voice notes from Marc during his commute.
- **Slack** (`slack-api`): Used inside the ICU conference planning group and the Alliance Cycliste advisory committee. Notifications muted outside 8 AM to 6 PM.
- **Microsoft Teams** (`microsoft-teams-api`): The city's official channel for cross-departmental coordination on the Climate Adaptation Framework.

## Notes, Documents, and Knowledge

- **Notion** (`notion-api`): Personal hub for project notes, Mobilité Plateau stakeholder map, and the ICU presentation outline.
- **Confluence** (`confluence-api`): City-side documentation space where she publishes the Parc-Extension methodology notes for the team.
- **Google Drive** (`google-drive-api`): Personal archive for family photos, Emile's school documents, ICU conference draft, and the GIS layer references she pulls into design tables.
- **Box** (`box-api`): Where one federal research partner insists on hosting Parc-Extension survey raw data because of their security review.

## Project and Task Management

- **Asana** (`asana-api`): Cross-team tracker for Mobilité Plateau milestones, owned by Ruth and updated each Friday afternoon.
- **Monday** (`monday-api`): A community partner organization runs their housing equity outreach on Monday; Ruth has guest access for the Parc-Extension workstream.
- **Airtable** (`airtable-api`): Stakeholder database for Mobilité Plateau, including business owners, residents, and community group contacts with engagement history.

## Government, Civic, and Reference

- **NASA** (`nasa-api`): Pulls satellite imagery and historical land-cover data when the climate adaptation team wants to visualize urban heat island patterns and to validate the authoritative canopy figure for the climate module.
- **OpenWeather** (`openweather-api`): Decides whether tomorrow's site visit needs rain gear, whether to bike or take transit, and whether the October 15 consultation evening window is clear.

## News, Social, and Community

- **Reddit** (`reddit-api`): r/Montreal and r/UrbanPlanning for unfiltered resident sentiment on the Plateau consultations.

## Communications Infrastructure

- **Twilio** (`twilio-api`): Powers the SMS reminder service the city sends ahead of consultations; Ruth reviews delivery reports the morning after.

## Engineering Observability and Analytics

- **Mixpanel** (`mixpanel-api`): A federal research partner reports Parc-Extension survey funnel metrics from Mixpanel; Ruth gets weekly digest emails.

## Marketing, Outreach, and Engagement

- **Mailchimp** (`mailchimp-api`): Sends the Mobilité Plateau monthly newsletter to opted-in residents and ward associations.
- **Typeform** (`typeform-api`): Hosts the Mobilité Plateau intercept survey for short feedback at public events.

## Design and Visual Review

- **Figma** (`figma-api`): Reviews the city communications team's poster mockups and the bilingual deck pages for consultations and gives mark-ups.

## Auxiliary CRM Access (Light Touch)

These are services Ruth has light-touch credentials for through outside organizations. They are not part of the Mobilité Plateau, Parc-Extension, or Climate Adaptation workflows and should not be queried for council-brief, interim, or climate-module work.

- **Salesforce** (`salesforce-api`): The OPUQ runs its membership database on Salesforce; she logs in to update her own record once a year.
- **LinkedIn** (`linkedin-api`): Maintained for OPUQ and ICU networking; gets updates from former Université Saint-Laurent classmates.
- **HubSpot** (`hubspot-api`): A consulting client uses HubSpot to manage their stakeholder list; Ruth has light user access.
