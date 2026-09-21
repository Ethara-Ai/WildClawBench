"""FastAPI server wrapping woocommerce_data module as REST endpoints.

Mirrors a subset of the WooCommerce REST API v3. Base path: /wp-json/wc/v3
"""

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import List, Optional

import woocommerce_data
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

app = FastAPI(title="WooCommerce REST API v3 (Mock)", version="wc/v3")
install_tracker(app)
install_admin_plane(app, store=woocommerce_data._store)
@app.get("/health")
def health():
    return {"status": "ok"}


# --- Products ---

@app.get("/wp-json/wc/v3/products")
def list_products(
    search: Optional[str] = None,
    sku: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1),
    per_page: int = Query(10),
):
    return woocommerce_data.list_products(
        search=search, sku=sku, status=status, page=page, per_page=per_page,
    )


@app.get("/wp-json/wc/v3/products/{product_id}")
def get_product(product_id: int):
    result = woocommerce_data.get_product(product_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Orders ---

@app.get("/wp-json/wc/v3/orders")
def list_orders(
    customer: Optional[int] = None,
    status: Optional[str] = None,
    page: int = Query(1),
    per_page: int = Query(10),
):
    return woocommerce_data.list_orders(
        customer=customer, status=status, page=page, per_page=per_page,
    )


@app.get("/wp-json/wc/v3/orders/{order_id}")
def get_order(order_id: int):
    result = woocommerce_data.get_order(order_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class OrderBilling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    email: Optional[str] = ""


class OrderLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: Optional[int] = 0
    name: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[int] = 1
    price: Optional[float] = 0.0


class OrderCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: Optional[int] = 0
    status: Optional[str] = "pending"
    currency: Optional[str] = "USD"
    payment_method: Optional[str] = "bacs"
    payment_method_title: Optional[str] = "Direct Bank Transfer"
    billing: Optional[OrderBilling] = None
    line_items: Optional[List[OrderLineItem]] = None
    total: Optional[float] = None
    total_tax: Optional[float] = None


@app.post("/wp-json/wc/v3/orders", status_code=200)
def create_order(body: OrderCreateBody):
    result = woocommerce_data.create_order(
        customer_id=body.customer_id,
        status=body.status,
        currency=body.currency,
        payment_method=body.payment_method,
        payment_method_title=body.payment_method_title,
        billing=body.billing.model_dump() if body.billing else None,
        line_items=([li.model_dump() for li in body.line_items]
                    if body.line_items else None),
        total=body.total,
        total_tax=body.total_tax,
    )
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Customers ---

@app.get("/wp-json/wc/v3/customers")
def list_customers(
    search: Optional[str] = None,
    email: Optional[str] = None,
    page: int = Query(1),
    per_page: int = Query(10),
):
    return woocommerce_data.list_customers(
        search=search, email=email, page=page, per_page=per_page,
    )
