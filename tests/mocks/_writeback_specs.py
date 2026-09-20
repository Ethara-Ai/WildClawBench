"""Per-service write->read-back specs consumed by ``test_write_readback.py``.

Pure data: each :class:`WriteSpec` names the create/read/update/delete routes for
one resource plus the field values that must survive each hop. Kept apart from
the test module so adding fleet coverage is a table edit, and so the engine in
``_writeback.py`` stays readable next to the assertions it powers.

Path constants below are the long, repeated route prefixes; the ids embedded in
them (``orbit-labs/auth-api``, ``zone1aaaa1111bbbb2222cccc3333dddd``, project
``101``) are seed rows shipped with each mock, not fixtures created here.

The newreq convergence retired ten specs with their services (linear x3,
google-classroom x3, asana, stripe x2, airtable). Nothing was lost that the
table did not regain: every hop they covered -- create/read-back, partial
update merging rather than replacing, delete not served again -- is driven by
the surviving five and the arriving twelve below.
"""
from __future__ import annotations

from ._writeback import Step, WriteSpec

GCAL = "/calendar/v3/calendars/amelia@orbit-labs.com/events"
GH_REPO = "/repos/orbit-labs/auth-api/issues"
BAMBOO = "/api/gateway.php/orbitlabs/v1/employees"
CF_DNS = "/client/v4/zones/zone1aaaa1111bbbb2222cccc3333dddd/dns_records"
GL_ISSUES = "/api/v4/projects/101/issues"
OUTLOOK_MAIL = "/v1.0/me/messages"

SPECS = [
    WriteSpec(
        api="google-calendar-api", resource="event",
        create=Step("POST", GCAL, {"summary": "WRB event", "location": "Room A",
                                   "start": {"dateTime": "2026-03-02T10:00:00Z"},
                                   "end": {"dateTime": "2026-03-02T11:00:00Z"}}),
        created_id="id", read=GCAL + "/{id}",
        created_expect={"summary": "WRB event", "location": "Room A",
                        "start.dateTime": "2026-03-02T10:00:00Z", "status": "confirmed"},
        update=Step("PATCH", GCAL + "/{id}", {"summary": "WRB event renamed",
                                              "location": "Room B"}),
        updated_expect={"summary": "WRB event renamed", "location": "Room B",
                        "start.dateTime": "2026-03-02T10:00:00Z"},
        delete=GCAL + "/{id}",
    ),
    WriteSpec(
        api="github-api", resource="issue",
        create=Step("POST", GH_REPO, {"title": "WRB issue", "body": "seed body"}),
        created_id="number", read=GH_REPO + "/{id}",
        created_expect={"title": "WRB issue", "body": "seed body", "state": "open"},
        update=Step("PATCH", GH_REPO + "/{id}", {"title": "WRB issue renamed",
                                                 "state": "closed"}),
        updated_expect={"title": "WRB issue renamed", "state": "closed", "body": "seed body"},
    ),
    WriteSpec(
        api="jira-api", resource="issue",
        create=Step("POST", "/rest/api/3/issue",
                    {"fields": {"project": {"key": "ENG"}, "summary": "WRB issue",
                                "issuetype": {"name": "Task"}, "description": "seed"}}),
        created_id="key", read="/rest/api/3/issue/{id}",
        created_expect={"fields.summary": "WRB issue", "fields.description": "seed",
                        "fields.project.key": "ENG", "fields.issuetype.name": "Task"},
        update=Step("PUT", "/rest/api/3/issue/{id}", {"fields": {"summary": "WRB renamed"}}),
        updated_expect={"fields.summary": "WRB renamed", "fields.description": "seed"},
        update_status=204,
    ),
    WriteSpec(
        api="hubspot-api", resource="contact",
        create=Step("POST", "/crm/v3/objects/contacts",
                    {"properties": {"firstname": "Wrb", "lastname": "Probe",
                                    "email": "wrb-contact@example.com"}}),
        created_id="id", read="/crm/v3/objects/contacts/{id}",
        created_expect={"properties.firstname": "Wrb", "properties.lastname": "Probe",
                        "properties.email": "wrb-contact@example.com", "archived": False},
        update=Step("PATCH", "/crm/v3/objects/contacts/{id}",
                    {"properties": {"lastname": "Renamed"}}),
        # firstname must survive: PATCH merges properties, it does not replace them.
        updated_expect={"properties.lastname": "Renamed", "properties.firstname": "Wrb"},
    ),
    WriteSpec(
        api="hubspot-api", resource="deal",
        create=Step("POST", "/crm/v3/objects/deals",
                    {"properties": {"dealname": "WRB deal", "amount": "5000"}}),
        created_id="id", read="/crm/v3/objects/deals/{id}",
        created_expect={"properties.dealname": "WRB deal", "properties.amount": "5000"},
        update=Step("PATCH", "/crm/v3/objects/deals/{id}", {"properties": {"amount": "7500"}}),
        updated_expect={"properties.amount": "7500", "properties.dealname": "WRB deal"},
    ),

    # --- the 25 services that arrived in the newreq convergence ------------
    # They came from a tree predating the write-read-back sweeps, so none of
    # them had a spec here. Every one below writes through the hardened
    # forbid-model route layer and reads the row back through a second GET.
    WriteSpec(
        api="freshdesk-api", resource="ticket",
        create=Step("POST", "/api/v2/tickets",
                    {"subject": "WRB ticket", "description": "seed body",
                     "priority": 2, "status": 2, "type": "Question"}),
        created_id="id", read="/api/v2/tickets/{id}",
        created_expect={"subject": "WRB ticket", "description": "seed body",
                        "priority": 2, "status": 2},
        update=Step("PUT", "/api/v2/tickets/{id}", {"status": 4, "priority": 3}),
        updated_expect={"status": 4, "priority": 3, "subject": "WRB ticket",
                        "description": "seed body"},
    ),
    WriteSpec(
        api="gitlab-api", resource="issue",
        create=Step("POST", GL_ISSUES, {"title": "WRB issue", "description": "seed body",
                                        "labels": ["bug", "urgent"]}),
        created_id="iid", read=GL_ISSUES + "/{id}",
        created_expect={"title": "WRB issue", "description": "seed body",
                        "state": "opened", "labels": ["bug", "urgent"]},
        update=Step("PUT", GL_ISSUES + "/{id}", {"title": "WRB issue renamed",
                                                 "state_event": "close"}),
        updated_expect={"title": "WRB issue renamed", "state": "closed",
                        "description": "seed body"},
    ),
    WriteSpec(
        api="cloudflare-api", resource="dns_record",
        create=Step("POST", CF_DNS, {"type": "A", "name": "wrb.orbit-labs.com",
                                     "content": "203.0.113.77", "ttl": 300,
                                     "proxied": False}),
        created_id="result.id", read=CF_DNS + "/{id}",
        created_expect={"result.name": "wrb.orbit-labs.com", "result.content": "203.0.113.77",
                        "result.type": "A", "result.ttl": 300},
        update=Step("PUT", CF_DNS + "/{id}", {"type": "A", "name": "wrb.orbit-labs.com",
                                              "content": "203.0.113.88", "ttl": 600}),
        updated_expect={"result.content": "203.0.113.88", "result.ttl": 600,
                        "result.name": "wrb.orbit-labs.com"},
        delete=CF_DNS + "/{id}", create_status=200,
    ),
    WriteSpec(
        api="activecampaign-api", resource="contact",
        create=Step("POST", "/api/3/contacts",
                    {"contact": {"email": "wrb.contact@example.com", "firstName": "Wrb",
                                 "lastName": "Probe", "phone": "+1-503-555-0199"}}),
        created_id="contact.id", read="/api/3/contacts/{id}",
        created_expect={"contact.email": "wrb.contact@example.com",
                        "contact.firstName": "Wrb", "contact.lastName": "Probe",
                        "contact.phone": "+1-503-555-0199"},
    ),
    WriteSpec(
        api="klaviyo-api", resource="profile",
        create=Step("POST", "/api/profiles",
                    {"data": {"type": "profile",
                              "attributes": {"email": "wrb.lead@example.com",
                                             "first_name": "Wrb", "last_name": "Lead",
                                             "organization": "Orbit",
                                             "location": {"city": "Seattle",
                                                          "region": "Washington",
                                                          "country": "United States"}}}}),
        created_id="data.id", read="/api/profiles/{id}",
        created_expect={"data.attributes.email": "wrb.lead@example.com",
                        "data.attributes.first_name": "Wrb",
                        "data.attributes.organization": "Orbit",
                        "data.attributes.location.city": "Seattle"},
    ),
    WriteSpec(
        api="greenhouse-api", resource="candidate",
        create=Step("POST", "/v1/candidates",
                    {"first_name": "Wrb", "last_name": "Applicant",
                     "email": "wrb.applicant@example.com", "title": "SRE",
                     "company": "Northwind", "source": "Referral"}),
        created_id="id", read="/v1/candidates/{id}",
        created_expect={"first_name": "Wrb", "last_name": "Applicant",
                        "email": "wrb.applicant@example.com", "title": "SRE",
                        "company": "Northwind", "source": "Referral"},
    ),
    WriteSpec(
        api="bamboohr-api", resource="employee",
        create=Step("POST", BAMBOO,
                    {"firstName": "Wrb", "lastName": "Hire",
                     "workEmail": "wrb.hire@orbit-labs.com", "department": "Engineering",
                     "jobTitle": "SRE", "location": "San Francisco",
                     "hireDate": "2026-06-01"}),
        created_id="id", read=BAMBOO + "/{id}",
        created_expect={"firstName": "Wrb", "lastName": "Hire",
                        "workEmail": "wrb.hire@orbit-labs.com",
                        "department": "Engineering", "jobTitle": "SRE"},
    ),
    WriteSpec(
        api="alpaca-api", resource="order",
        create=Step("POST", "/v2/orders", {"symbol": "AAPL", "qty": "3", "side": "buy",
                                           "type": "market", "time_in_force": "day"}),
        created_id="id", read="/v2/orders/{id}",
        created_expect={"symbol": "AAPL", "qty": "3", "side": "buy", "type": "market",
                        "time_in_force": "day"},
    ),
    WriteSpec(
        api="paypal-api", resource="order",
        create=Step("POST", "/v2/checkout/orders",
                    {"intent": "CAPTURE",
                     "purchase_units": [{"amount": {"currency_code": "USD",
                                                    "value": "77.40"},
                                         "description": "WRB order"}]}),
        created_id="id", read="/v2/checkout/orders/{id}",
        created_expect={"status": "CREATED", "intent": "CAPTURE",
                        "purchase_units.0.amount.value": "77.40",
                        "purchase_units.0.amount.currency_code": "USD"},
    ),
    # The payout _pk regression: this create used to raise StoreError because the
    # write path never lifted batch_header.payout_batch_id to the row's key.
    WriteSpec(
        api="paypal-api", resource="payout",
        create=Step("POST", "/v1/payments/payouts",
                    {"sender_batch_header": {"sender_batch_id": "WRB_Batch_01",
                                             "email_subject": "WRB payout"},
                     "items": [{"amount": {"currency_code": "USD", "value": "310.25"},
                                "receiver": "wrb.payee@orbit-labs.com"}]}),
        created_id="batch_header.payout_batch_id",
        read="/v1/payments/payouts/{id}",
        created_expect={"batch_header.batch_status": "PENDING",
                        "batch_header.amount.value": "310.25",
                        "batch_header.sender_batch_header.sender_batch_id": "WRB_Batch_01",
                        "recipient_email": "wrb.payee@orbit-labs.com"},
    ),
    # The send route is an ACTION (202 accepted, no resource path of its own),
    # so the only proof the message became a row is the independent GET by the
    # id the 202 hands back -- exactly the hop this engine exists to drive.
    WriteSpec(
        api="outlook-api", resource="message",
        create=Step("POST", "/v1.0/me/sendMail",
                    {"message": {"subject": "WRB outlook mail",
                                 "body": {"contentType": "HTML",
                                          "content": "WRB body"},
                                 "toRecipients": [
                                     {"emailAddress":
                                      {"address": "noor@orbit-labs.com"}}]}}),
        created_id="id", read=OUTLOOK_MAIL + "/{id}", create_status=202,
        created_expect={"subject": "WRB outlook mail", "body.content": "WRB body",
                        "toRecipients.0.emailAddress.address": "noor@orbit-labs.com"},
    ),
    WriteSpec(
        api="paypal-api", resource="refund",
        create=Step("POST", "/v2/payments/refunds",
                    {"capture_id": "CAP_3C679384HN8401234",
                     "amount": {"currency_code": "USD", "value": "12.00"},
                     "note_to_payer": "WRB refund"}),
        created_id="id", read="/v2/payments/refunds/{id}",
        created_expect={"amount.value": "12.00", "note_to_payer": "WRB refund",
                        "capture_id": "CAP_3C679384HN8401234"},
    ),
]
