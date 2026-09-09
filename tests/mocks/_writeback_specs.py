"""Per-service write->read-back specs consumed by ``test_write_readback.py``.

Pure data: each :class:`WriteSpec` names the create/read/update/delete routes for
one resource plus the field values that must survive each hop. Kept apart from
the test module so adding fleet coverage is a table edit, and so the engine in
``_writeback.py`` stays readable next to the assertions it powers.

Path constants below are the long, repeated route prefixes; the ids embedded in
them (``course_001``, ``orbit-labs/auth-api``, ``appNW1studio0001``) are seed rows
shipped with each mock, not fixtures created here.
"""
from __future__ import annotations

from ._writeback import Step, WriteSpec

GCAL = "/calendar/v3/calendars/amelia@orbit-labs.com/events"
GH_REPO = "/repos/orbit-labs/auth-api/issues"
CLASS_ANN = "/v1/courses/course_001/announcements"
CLASS_TOPIC = "/v1/courses/course_001/topics"
AIRTABLE = "/v0/appNW1studio0001/tblProjects00001"

SPECS = [
    WriteSpec(
        api="linear-api", resource="issue",
        create=Step("POST", "/v1/issues", {"title": "WRB issue", "teamId": "team-backend",
                                           "description": "seed body", "priority": 2}),
        created_id="issue.id", read="/v1/issues/{id}",
        created_expect={"issue.title": "WRB issue", "issue.description": "seed body",
                        "issue.priority": 2, "issue.teamId": "team-backend"},
        update=Step("PUT", "/v1/issues/{id}", {"title": "WRB issue renamed", "priority": 1}),
        updated_expect={"issue.title": "WRB issue renamed", "issue.priority": 1,
                        "issue.description": "seed body"},
        delete="/v1/issues/{id}",
    ),
    WriteSpec(
        api="linear-api", resource="comment",
        create=Step("POST", "/v1/comments", {"body": "WRB comment", "issueId": "BUG-201",
                                             "userId": "user-mira"}),
        created_id="comment.id", read="/v1/comments/{id}",
        created_expect={"comment.body": "WRB comment", "comment.issueId": "BUG-201",
                        "comment.userId": "user-mira"},
        update=Step("PUT", "/v1/comments/{id}", {"body": "WRB comment edited"}),
        updated_expect={"comment.body": "WRB comment edited", "comment.issueId": "BUG-201"},
        delete="/v1/comments/{id}",
    ),
    WriteSpec(
        api="linear-api", resource="project",
        create=Step("POST", "/v1/projects", {"name": "WRB project", "state": "planned",
                                             "description": "seed"}),
        created_id="project.id", read="/v1/projects/{id}",
        created_expect={"project.name": "WRB project", "project.state": "planned",
                        "project.description": "seed"},
        update=Step("PUT", "/v1/projects/{id}", {"name": "WRB project renamed",
                                                 "state": "started"}),
        updated_expect={"project.name": "WRB project renamed", "project.state": "started",
                        "project.description": "seed"},
    ),
    WriteSpec(
        api="google-classroom-api", resource="course",
        create=Step("POST", "/v1/courses", {"name": "WRB course", "section": "S1",
                                            "ownerId": "teacher_001"}),
        created_id="course.id", read="/v1/courses/{id}",
        created_expect={"course.name": "WRB course", "course.section": "S1",
                        "course.ownerId": "teacher_001", "course.courseState": "ACTIVE"},
        update=Step("PATCH", "/v1/courses/{id}", {"name": "WRB course renamed", "room": "R2"}),
        updated_expect={"course.name": "WRB course renamed", "course.room": "R2",
                        "course.section": "S1"},
    ),
    WriteSpec(
        api="google-classroom-api", resource="announcement",
        create=Step("POST", CLASS_ANN, {"text": "WRB announcement", "state": "PUBLISHED"}),
        created_id="announcement.id", read=CLASS_ANN + "/{id}",
        created_expect={"announcement.text": "WRB announcement",
                        "announcement.state": "PUBLISHED",
                        "announcement.courseId": "course_001"},
        update=Step("PATCH", CLASS_ANN + "/{id}", {"text": "WRB announcement edited"}),
        updated_expect={"announcement.text": "WRB announcement edited",
                        "announcement.state": "PUBLISHED"},
        delete=CLASS_ANN + "/{id}",
    ),
    WriteSpec(
        api="google-classroom-api", resource="topic",
        create=Step("POST", CLASS_TOPIC, {"name": "WRB topic"}),
        created_id="topic.topicId", read=CLASS_TOPIC + "/{id}",
        created_expect={"topic.name": "WRB topic", "topic.courseId": "course_001"},
        update=Step("PATCH", CLASS_TOPIC + "/{id}", {"name": "WRB topic renamed"}),
        updated_expect={"topic.name": "WRB topic renamed"},
        delete=CLASS_TOPIC + "/{id}",
    ),
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
        api="asana-api", resource="task",
        create=Step("POST", "/api/1.0/tasks", {"data": {"name": "WRB task", "notes": "seed",
                                                        "projects": ["1203000000002001"]}}),
        created_id="data.gid", read="/api/1.0/tasks/{id}",
        created_expect={"data.name": "WRB task", "data.notes": "seed", "data.completed": False},
        update=Step("PUT", "/api/1.0/tasks/{id}", {"data": {"name": "WRB task renamed",
                                                            "completed": True}}),
        updated_expect={"data.name": "WRB task renamed", "data.completed": True,
                        "data.notes": "seed"},
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
    WriteSpec(
        api="stripe-api", resource="customer",
        create=Step("POST", "/v1/customers", {"name": "WRB customer", "description": "seed",
                                              "email": "wrb-cus@example.com"}),
        created_id="id", read="/v1/customers/{id}",
        created_expect={"name": "WRB customer", "email": "wrb-cus@example.com",
                        "description": "seed", "object": "customer"},
    ),
    WriteSpec(
        api="stripe-api", resource="charge",
        create=Step("POST", "/v1/charges", {"amount": 4242, "currency": "usd",
                                            "customer": "cus_Nb1Aurora",
                                            "description": "WRB charge"}),
        created_id="id", read="/v1/charges/{id}",
        created_expect={"amount": 4242, "currency": "usd", "description": "WRB charge",
                        "customer": "cus_Nb1Aurora", "object": "charge"},
    ),
    WriteSpec(
        api="airtable-api", resource="record",
        create=Step("POST", AIRTABLE, {"records": [{"fields": {"Name": "WRB record",
                                                               "Status": "Active"}}]}),
        created_id="records.0.id", read=AIRTABLE + "/{id}", create_status=200,
        created_expect={"fields.Name": "WRB record", "fields.Status": "Active"},
        update=Step("PATCH", AIRTABLE + "/{id}", {"fields": {"Name": "WRB record renamed"}}),
        updated_expect={"fields.Name": "WRB record renamed", "fields.Status": "Active"},
        delete=AIRTABLE + "/{id}",
    ),
]
