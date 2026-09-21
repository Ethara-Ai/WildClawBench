"""FastAPI server wrapping bigcommerce_data module as REST endpoints.

Mirrors a subset of the BigCommerce APIs: Catalog/Customers (v3) and Orders
(v2). v3 list endpoints wrap data in `{"data": [...], "meta": {...}}`.
"""

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import List, Optional

import bigcommerce_data
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

app = FastAPI(title="BigCommerce API (Mock)", version="v3")
install_tracker(app)
install_admin_plane(app, store=bigcommerce_data._store)
@app.get("/health")
def health():
    return {"status": "ok"}


# --- Catalog / Products (v3) ---

@app.get("/v3/catalog/products")
def list_products(
    name: Optional[str] = None,
    sku: Optional[str] = None,
    is_visible: Optional[bool] = None,
    page: int = Query(1),
    limit: int = Query(50),
):
    return bigcommerce_data.list_products(
        name=name, sku=sku, is_visible=is_visible, page=page, limit=limit,
    )


@app.get("/v3/catalog/products/{product_id}")
def get_product(product_id: int):
    result = bigcommerce_data.get_product(product_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Orders (v2) ---

@app.get("/v2/orders")
def list_orders(
    customer_id: Optional[int] = None,
    status_id: Optional[int] = None,
    page: int = Query(1),
    limit: int = Query(50),
):
    return bigcommerce_data.list_orders(
        customer_id=customer_id, status_id=status_id, page=page, limit=limit,
    )


@app.get("/v2/orders/{order_id}")
def get_order(order_id: int):
    result = bigcommerce_data.get_order(order_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


class OrderBillingAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    email: Optional[str] = ""


class OrderProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: Optional[int] = 0
    quantity: Optional[int] = 1
    price_inc_tax: Optional[float] = 0.0


class OrderCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: Optional[int] = 0
    status_id: Optional[int] = 1
    payment_method: Optional[str] = "manual"
    currency_code: Optional[str] = "USD"
    billing_address: Optional[OrderBillingAddress] = None
    products: Optional[List[OrderProduct]] = None


@app.post("/v2/orders", status_code=200)
def create_order(body: OrderCreateBody):
    result = bigcommerce_data.create_order(
        customer_id=body.customer_id,
        status_id=body.status_id,
        payment_method=body.payment_method,
        currency_code=body.currency_code,
        billing_address=(body.billing_address.model_dump()
                         if body.billing_address else None),
        products=[p.model_dump() for p in body.products] if body.products else None,
    )
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(status_code=404, content=result)
    return result


# --- Customers (v3) ---

@app.get("/v3/customers")
def list_customers(
    email: Optional[str] = None,
    company: Optional[str] = None,
    page: int = Query(1),
    limit: int = Query(50),
):
    return bigcommerce_data.list_customers(
        email=email, company=company, page=page, limit=limit,
    )
