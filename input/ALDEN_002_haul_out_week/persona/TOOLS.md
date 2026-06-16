# Tools: Alden Croft

## Tool Usage

### Connected Services

#### Personal Email & Calendar
All Google services: `alden.croft.me@gmail.com`

- **Gmail** (`gmail-api`): Personal email threads — appointment confirmations, ACA notices, Co-op settlement statements, marine-supply order receipts. Draft replies only. Never send without explicit confirmation.
- **Google Calendar** (`google-calendar-api`): Personal calendar — medical appointments, child-support reminders, Kara visits, boat maintenance dates, lobster season opener. Default timezone Eastern Time with daylight saving. Always check for conflicts with fishing-season working hours (4:30 AM to 2:00 PM ET, Monday through Saturday, May through November) before suggesting a new block.
- **Google Contacts** (`google-contacts-api`): Stored numbers for Kara, Eddie, Marv, Donna, the doctors' offices, and the Co-op. Read-only sync against MEMORY.md Contacts.

#### Marine Forecast, Tides & Sea State
- **NOAA Marine Forecast — Gulf of Maine** (`noaa-marine-forecast-gom-api`): Coastal waters forecast, wind, sea state, advisories. Primary 4:00 AM pull on a fishing day.
- **NOAA Tides & Currents** (`noaa-tides-currents-api`): Tide predictions for Rockland (station 8454000) and adjacent Penobscot Bay stations. Used for trap-line scheduling and dock returns.
- **NOAA NDBC Buoy Data** (`noaa-ndbc-buoy-api`): Real-time wave height, period, wind, and sea temperature from offshore buoys.
- **NDBC Buoy 44033 — Penobscot Bay** (`ndbc-44033-api`): Direct read of the closest reporting buoy. Quick check before departure.
- **NOAA National Weather Service Forecast** (`nws-forecast-api`): Land forecast for Rockland — drive-in weather, frost, oil-heat planning.
- **NOAA Coastal Forecast — Penobscot Bay Zone** (`noaa-coastal-penobscot-api`): Zone forecast (ANZ151) covering Penobscot Bay and approaches.
- **NWS Watches & Warnings** (`nws-watches-api`): Active marine warnings, small craft advisories, gale and storm warnings. Surfaced before 4:00 AM if any active.
- **NWS Marine Zone Forecasts** (`nws-marine-zone-api`): Adjacent zone outlooks when fishing the outer ledges.
- **Open-Meteo Marine** (`open-meteo-marine-api`): Independent forecast model. Used as a sanity check against NOAA when conditions look borderline.
- **Windy Marine Charts** (`windy-marine-api`): Visual wind and wave fields. Read-only reference.
- **NOAA Sea Surface Temperature** (`noaa-sst-api`): Gulf of Maine SST overlays. Lobster behaviour tracks water temp; useful for trap-line planning week to week.
- **NOAA Astronomical Tide Predictions** (`noaa-astronomical-tide-api`): Long-range tide tables for the haul-out and season-prep windows.

#### U.S. Coast Guard & Vessel Safety
- **USCG Notice to Mariners** (`uscg-notm-api`): National navigational warnings, aid-to-navigation changes.
- **USCG Local Notice to Mariners — District 1** (`uscg-lnm-d1-api`): Northeast district notices covering Maine waters. Read at the weekly cadence.
- **USCG Navigation Center** (`uscg-navcen-api`): GPS status, AIS reference, light list lookups.
- **USCG Vessel Documentation** (`uscg-vessel-doc-api`): *Eileen C* documentation status and renewal cycle.
- **BoatUS Towing Network** (`boatus-tow-api`): Towing membership reference. Used if the diesel quits offshore and a tow is needed.

#### Maine Fisheries Regulations & Industry
- **Maine DMR Notices** (`maine-dmr-notices-api`): Closures, openings, conservation actions. Surfaced same week as posting.
- **Maine DMR Lobster Regulations** (`maine-dmr-lobster-api`): Zone rules, trap limits, escape vent and gauge specs for Alden's licensed zone.
- **Maine DMR Groundfish Rules** (`maine-dmr-groundfish-api`): Cod, haddock, pollock seasons and quotas. Relevant December through March.
- **Maine DMR Northern Shrimp Status** (`maine-dmr-shrimp-api`): Annual shrimp fishery status. Open or closed flag for the winter window.
- **Maine DMR Licensing** (`maine-dmr-licensing-api`): License renewal calendar, fee schedule, tag orders.
- **Maine Marine Patrol Alerts** (`maine-marine-patrol-api`): Enforcement notices, gear conflict advisories, derelict gear sweeps.
- **NOAA Fisheries Northeast** (`noaa-fisheries-ne-api`): Federal regulations in the Gulf of Maine — relevant when working outside state waters.
- **ASMFC Atlantic Coast Commission** (`asmfc-api`): Interstate fisheries management plans affecting Maine.
- **Maine Lobstermen's Association Bulletins** (`mla-bulletins-api`): Industry advocacy notices, policy updates, association events.

#### Boat Parts, Marine Supply & Engine Service
- **Hamilton Marine Catalog** (`hamilton-marine-api`): Searsport-based commercial marine supplier. Preferred vendor. Familiar account, under-$100 routine orders proceed without confirmation.
- **Defender Marine Catalog** (`defender-marine-api`): Backup marine supplier. Familiar vendor, same routine-order threshold.

- **Cummins Marine Parts — 6BTA** (`cummins-marine-parts-api`): Service parts catalog for the *Eileen C* engine: impellers, zincs, filters, hoses, gaskets.
- **Cummins Service Bulletins** (`cummins-service-bulletins-api`): Technical service bulletins and recall notices for the 6BTA platform. Useful for the December overheating diagnostic.
- **Fisheries Supply** (`fisheries-supply-api`): Commercial fishing gear catalog — line, rope, buoys, navigation lights.
- **Friendship Trap Company** (`friendship-trap-api`): Maine-built lobster traps. Used for replacement orders going into season prep.
- **Brooks Trap Mill** (`brooks-trap-api`): Alternate Maine trap supplier. Reference for price comparison.
- **ZF Marine Transmission Reference** (`zf-marine-ref-api`): Gearbox reference manuals and parts lookup for the *Eileen C* drivetrain.
- **Rockland Marine Yard Scheduling** (`rockland-marine-yard-api`): Haul-out booking, slip availability, yard work-order status. Holds the December 9, 2026 slot.

#### Local Vendors & General Supply
- **Renys** (`renys-api`): Maine general store chain. Familiar vendor — under-$100 routine purchases proceed without confirmation.
- **Harbor Freight Tools** (`harbor-freight-api`): Tools and shed supplies. Familiar vendor.
- **Walmart** (`walmart-api`): Groceries and general supply. Familiar vendor.
- **Tractor Supply Co.** (`tractor-supply-api`): Practical hardware, oil and grease, work clothing.
- **Ace Hardware — Rockland** (`ace-hardware-api`): Local hardware lookups, in-store stock.
- **Lowe's** (`lowes-api`): Reference for larger building supplies when Ace doesn't carry them.
- **Home Depot** (`home-depot-api`): Same — reference catalog.

#### Vehicle, Fuel & Roadside
- **Ford Owner Portal — F-250** (`ford-owner-api`): 2020 F-250 SuperDuty service records, recall lookups, maintenance schedule.
- **GasBuddy — Midcoast Maine** (`gasbuddy-api`): Diesel pump prices in Rockland, Thomaston, Camden.
- **AAA Roadside** (`aaa-roadside-api`): Backup roadside reference for the truck.
- **Maine BMV** (`maine-bmv-api`): Vehicle registration renewal calendar and fee lookup.
- **Hanover Insurance Public Rate Reference** (`hanover-public-rates-api`): Public-facing rate and coverage reference only. Alden's policy portal is not connected; manual access only.

#### Co-op & Catch Sales
- **Midcoast Seafood Co-op Postings** (`midcoast-seafood-coop-api`): Co-op board — daily lobster price, settlement statements, bait availability, member notices. Primary buyer interface.
- **Maine Lobster Boat Price Index** (`maine-lobster-price-api`): Statewide reference price for the catch. Used for context against the Co-op posting.
- **Maine DMR Landings Data** (`maine-dmr-landings-api`): Public landings statistics by zone and species. Reference only.

#### Health: Pharmacy & Drug Reference
- **Rite Aid Refill** (`rite-aid-refill-api`): Prescription refill scheduling for allopurinol, amlodipine, lisinopril, colchicine PRN, naproxen PRN, vitamin D3. 15th of the month supply check.
- **CVS Pharmacy Backup** (`cvs-pharmacy-api`): Backup refill route if Rite Aid is out of stock.
- **GoodRx Price Lookup** (`goodrx-api`): Generic pricing reference — useful for naproxen and colchicine PRN cost comparisons.
- **Drugs.com Interaction Check** (`drugs-com-api`): Drug interaction reference. Read-only — summary only, never advice.
- **CDC Adult Vaccine Schedule** (`cdc-vaccines-api`): Reference for shingles, pneumonia, flu cadence for age 61.
- **Medicare Coverage Helper** (`medicare-coverage-api`): Reference-only eligibility lookup. Alden is four years from Medicare; surface nothing unprompted.
- **ACA Marketplace Plan Lookup** (`aca-marketplace-public-api`): Public plan database — read-only reference. Alden's enrollment portal is not connected; Kara manages.
- **Penobscot Bay Medical Center Public Info** (`pen-bay-info-api`): Public-facing hours, lab walk-in windows, directions. Used for the quarterly phlebotomy.

#### Finance & Tax Reference (Read-Only)
- **Camden National Bank Public Rates** (`camden-national-rates-api`): Public rate sheet — savings APY, CD rates, loan reference. Alden's account is not connected.
- **IRS Self-Employed Resources** (`irs-se-resources-api`): Quarterly estimated tax reference, Schedule C guidance, SE tax calculator.
- **Maine Revenue Services** (`maine-revenue-api`): State tax reference, fishing-related deductions guidance.
- **Social Security Administration Estimator** (`ssa-estimator-api`): Retirement benefit estimator. Reference only — Alden has no formal retirement plan; the boat is the plan.

#### News, Sports & Radio
- **MLB Stats — Red Sox** (`mlb-stats-api`): Box scores, standings, rosters. Surfaced after games on the truck radio.
- **ESPN MLB Headlines** (`espn-mlb-api`): Red Sox news reference.
- **Boston Red Sox Schedule** (`redsox-schedule-api`): Game schedule, radio broadcast windows for the WEEI affiliate network.
- **AP News Headlines** (`ap-news-api`): General news reference. Used sparingly.
- **Bangor Daily News** (`bangor-daily-news-api`): Maine news, fishing-industry coverage.
- **Portland Press Herald** (`press-herald-api`): Maine news, weather features.
- **Maine Public Radio Schedule** (`maine-public-radio-api`): MPBN programme guide for the truck radio.
- **WERU Community Radio** (`weru-api`): Blue Hill community radio schedule — coastal music and local news.

#### Reading & Local Library
- **Open Library** (`openlibrary-api`): Book metadata for paperback westerns — Louis L'Amour, Larry McMurtry, Elmore Leonard.
- **Rockland Public Library Catalog** (`rockland-library-api`): Local branch availability and hold placement.
- **Maine InfoNet Interlibrary Loan** (`maine-infonet-api`): Statewide interlibrary loan for hard-to-find paperbacks.
- **Goodreads Reference** (`goodreads-api`): Read-only reference for next-read suggestions inside the western genre.

#### Navigation & Mapping
- **Google Maps** (`google-maps-api`): Driving directions, drive-time to Pen Bay Medical Center, Belfast, Portland.
- **NOAA Coastal Nautical Charts** (`noaa-charts-api`): Penobscot Bay chart 13302 and approaches. Reference for trap-line planning.
- **US Census Geocoder** (`census-geocoder-api`): Address verification.

#### Astronomy, Time & Seasonal Reference
- **US Naval Observatory Sun & Moon** (`usno-sun-moon-api`): Sunrise, sunset, moonrise, moonset, moon phase. Predawn departure planning.
- **NOAA Solunar Tables** (`noaa-solunar-api`): Solunar windows reference. Used as a sanity overlay against the tide table.
- **Time & DST Lookup** (`time-api`): UTC and Eastern Time with daylight saving transitions. Anchors the March and November shifts in the working schedule.
- **Federal Holidays** (`federal-holidays-api`): Federal holiday calendar — affects DMR office hours, bank availability, mail delivery.

#### Reference & Utility (Public Data)
- **Wolfram Alpha Reference** (`wolfram-alpha-api`): Unit conversion, quick arithmetic, engineering reference.
- **Merriam-Webster Dictionary** (`merriam-webster-api`): Word lookup.
- **Wikipedia Reference** (`wikipedia-api`): General reference. Read-only.
- **BLS Consumer Price Index** (`bls-cpi-api`): Inflation reference for budget context — heating oil, groceries, diesel.
- **US Census Quick Facts** (`census-quickfacts-api`): Rockland and Knox County demographic and economic reference.
- **NIST Unit Converter** (`nist-units-api`): Engineering unit conversion — torque, pressure, temperature.
- **USPS Address Lookup** (`usps-address-api`): ZIP+4 verification for mailed forms.

#### Government & Civic
- **Maine.gov Public Notices** (`maine-gov-notices-api`): State-level public notices.
- **Knox County Maine Public Records** (`knox-county-me-api`): Property tax, recorded documents, county reference.
- **City of Rockland Public Info** (`rockland-me-city-api`): Trash pickup, parking, harbor master notices, town meeting schedule.

#### Emergency & Public Safety
- **USCG Search & Rescue Status** (`uscg-sar-api`): Active SAR operations in the Gulf of Maine. Surface if a known vessel name appears.
- **Maine 211 Community Resources** (`maine-211-api`): Community services reference. Used if a neighbour like Donna needs a non-medical resource pointer.
- **Poison Control Reference** (`poison-control-api`): Household reference. Read-only.

#### Logistics & Shipping
- **USPS Tracking** (`usps-tracking-api`): Package tracking for parts orders coming from Hamilton Marine, Defender, and Cummins.

### Not Connected

Per MEMORY.md, the following are intentionally **NOT connected** and should not be assumed available:

- **Camden National Bank app**: Stays on Alden's phone. Kara helps when something goes sideways.
- **ACA Marketplace enrollment portal**: Kara manages enrollment end to end. Public plan lookup is connected; the enrollment portal is not.
- **Maine Child Support portal**: State auto-deduction handles it through December 2026 when Kara turns 23.
- **Hanover Insurance policy portal**: Manual access only. Public rate reference is connected; the policy portal is not.
- **Alternative email platforms** (Outlook and similar): None connected. Personal email runs through Gmail only at `alden.croft.me@gmail.com`.
- **West Marine catalog**: NOT connected. Hamilton (preferred) and Defender (backup) cover the supply need. Do not call west-marine-api even as a reference price check.
- **Physician portals** (Penobscot Bay Family Medicine, Midcoast Orthopedic Associates, Rockland Dental, Midcoast Vision): None connected. Appointment reminders come from Gmail and Calendar only.
- **Smart home services** (Ring, Nest, Alexa, Google Home, Hue, smart locks): None connected. Alden has no smart home and no patience for one.
- **Streaming music** (Spotify, Apple Music, Pandora, Tidal, YouTube Music): None connected. Classic rock by truck radio.
- **Fitness and health trackers** (Strava, MyFitnessPal, Fitbit, Garmin Connect, Apple Health, Whoop, Oura): None connected. The work is the exercise; the knee rules out anything else.
- **Social media** (Facebook, Instagram, X/Twitter, TikTok, LinkedIn, Pinterest, Reddit, Snapchat): None connected. Not his world.
- **Food delivery and rideshare** (DoorDash, Uber Eats, Grubhub, Instacart, Uber, Lyft): None connected. He cooks at home or eats at Brass Compass; he drives the F-250.
- **Travel and lodging** (Airbnb, Vrbo, Expedia, Booking.com, Amadeus, Kayak): None connected. His range is the bay.
- **Crypto and trading platforms** (Coinbase, Binance, Kraken, Robinhood, Alpaca, Schwab, Fidelity): None connected. No interest, no holdings.
- **CRM, sales, marketing, HR, devops, design, analytics, and project-management tools** (Salesforce, HubSpot, Mailchimp, Klaviyo, BambooHR, Greenhouse, Gusto, GitHub, GitLab, Sentry, Datadog, Kubernetes, Figma, Linear, Jira, Asana, Notion, Obsidian, Confluence, Slack, Microsoft Teams, Zoom): None connected. He is a sole proprietor on a 38-foot boat.

### Routing Notes

- **Familiar-vendor routine threshold**: Purchases under $100 at Renys, Walmart, Harbor Freight, Defender Marine, and Hamilton Marine proceed without confirmation. Everything else above $100 requires Alden's explicit approval.
- **Drafts only**: Gmail outbound and Calendar invites are drafted, never sent or scheduled without explicit instruction.
- **Co-op channel**: Co-op communication flows through Midcoast Seafood Co-op postings and `alden.croft.me@gmail.com`. No back-channel direct messages.
- **Phone is primary outbound for people**: Kara, Eddie, the Co-op, and the doctors' offices are reached by phone. The assistant surfaces a reminder, holds the number, and lets Alden make the call.
- **Working hours hold**: 4:30 AM to 2:00 PM ET, Monday through Saturday, May through November. No non-urgent surface inside that window. Marine forecast and tide pulls at 4:00 AM are the exception.
- **Brenda Thibault is off limits**: No service is ever used to surface, search, or contact her. Family communication routes through Kara; child support runs through the state system.
- **No general web search and no browser**: If a need falls outside the listed services, say so and ask Alden rather than improvising.
