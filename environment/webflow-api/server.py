"""FastAPI server wrapping webflow_data module as REST endpoints.

Mirrors a subset of the Webflow Data API v2 (api.webflow.com/v2): sites,
collections, and CMS collection items (list + create). Items carry a
`fieldData` object as in the real v2 API.
"""

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict, Optional

import webflow_data
try:
    from tracking_middleware import install_tracker
    from admin_plane import install_admin_plane
except ModuleNotFoundError as _shared_plane_err:  # standalone run without the shared module on sys.path
    import logging as _logging
    _logging.error("SHARED PLANE MISSING - audit + admin disabled: %s", _shared_plane_err)
    def install_tracker(app):  # no-op fallback: audit endpoints disabled
        return None

    def install_admin_plane(app, store=None, one_shot_registry=None):
        return None

app = FastAPI(title="Webflow Data API (Mock)", version="v2")
install_tracker(app)
install_admin_plane(app, store=webflow_data._store)
@app.get("/health")
def health():
    return {"status": "ok"}


# --- Sites ---

@app.get("/v2/sites")
def list_sites():
    return webflow_data.list_sites()


@app.get("/v2/sites/{site_id}")
def get_site(site_id: str):
    result = webflow_data.get_site(site_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Collections ---

@app.get("/v2/sites/{site_id}/collections")
def list_collections(site_id: str):
    result = webflow_data.list_collections(site_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Collection items ---

@app.get("/v2/collections/{collection_id}/items")
def list_items(
    collection_id: str,
    limit: int = Query(default=100),
    offset: int = Query(default=0),
):
    result = webflow_data.list_items(collection_id, limit=limit, offset=offset)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class CollectionItemCreateBody(BaseModel):
    """The v2 staged-item envelope. Closed at the top level, open inside.

    ``fieldData`` carries the CMS collection's OWN user-defined schema, which
    this mock does not model, so it stays a free mapping for the same reason
    salesforce SObjectBody does -- the field names belong to the customer, not
    to us. The envelope around it does not: Webflow answers 400 validation_error
    for a key it does not recognise there
    (https://developers.webflow.com/data/reference/cms/collection-items/staged-items/create-item).
    """

    model_config = ConfigDict(extra="forbid")

    fieldData: Dict[str, Any] = Field(min_length=1)
    isDraft: Optional[bool] = False
    isArchived: Optional[bool] = False


@app.post("/v2/collections/{collection_id}/items", status_code=202)
def create_item(collection_id: str, payload: CollectionItemCreateBody):
    result = webflow_data.create_item(
        collection_id,
        field_data=payload.fieldData or {},
        is_draft=bool(payload.isDraft),
        is_archived=bool(payload.isArchived),
    )
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result
