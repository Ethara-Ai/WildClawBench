"""Data access module for the UPS API mock service.

Mirrors a subset of the UPS REST APIs: rating, shipping (label creation),
and tracking. Responses use UPS-style `{"RateResponse": {...}}`,
`{"ShipmentResponse": {...}}`, and `{"trackResponse": {...}}` envelopes.
"""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent

import sys as _sys
_sys.path.insert(0, str(DATA_DIR.parent))
from _mutable_store import get_store  # noqa: E402

_store = get_store("ups-api")


def _store_insert(_table, _row):
    """Persist a newly-created row into the shared store (drift/injection-safe).

    Synthesizes the table's registered primary key from the row's ``id`` field
    when the row doesn't already carry it, so creates work regardless of whether
    the table was registered with primary_key="id" or a domain-specific key.
    """
    _t = _store.table(_table)
    if _t.primary_key not in _row and "id" in _row:
        _row = {**_row, _t.primary_key: _row["id"]}
    return _t.upsert(_row)

_store.register("rates", primary_key="service_code",
                initial_loader=lambda: _coerce_rates(_load("rates.csv")))
_store.register("shipments", primary_key="tracking_number",
                initial_loader=lambda: _coerce_shipments(_load("shipments.csv")))
_store.register("tracking", primary_key="tracking_number",
                initial_loader=lambda: _coerce_tracking(_load("tracking.csv")))


def _rates_rows():
    return _store.table("rates").rows()


def _shipments_rows():
    return _store.table("shipments").rows()


def _tracking_rows():
    return _store.table("tracking").rows()



def _load(filename):
    with open(DATA_DIR / filename, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Load + coerce
# ---------------------------------------------------------------------------

def _coerce_rates(rows):
    out = []
    for r in rows:
        out.append({
            "service_code": r["service_code"],
            "service_name": r["service_name"],
            "origin_zip": r["origin_zip"],
            "dest_zip": r["dest_zip"],
            "weight_lb": _to_float(r["weight_lb"]),
            "currency": r["currency"],
            "total_charge": _to_float(r["total_charge"]),
            "transit_days": int(r["transit_days"]),
            "delivery_date": r["delivery_date"],
        })
    return out


def _coerce_shipments(rows):
    out = []
    for r in rows:
        out.append({
            "tracking_number": r["tracking_number"],
            "service_code": r["service_code"],
            "service_name": r["service_name"],
            "ship_date": r["ship_date"],
            "origin_zip": r["origin_zip"],
            "dest_zip": r["dest_zip"],
            "weight_lb": _to_float(r["weight_lb"]),
            "currency": r["currency"],
            "total_charge": _to_float(r["total_charge"]),
            "label_url": r["label_url"],
        })
    return out


def _coerce_tracking(rows):
    out = []
    for r in rows:
        out.append({
            "tracking_number": r["tracking_number"],
            "status_type": r["status_type"],
            "status_code": r["status_code"],
            "status_description": r["status_description"],
            "service_name": r["service_name"],
            "ship_date": r["ship_date"],
            "scheduled_delivery": r["scheduled_delivery"],
            "latest_activity": r["latest_activity"],
            "latest_activity_location": r["latest_activity_location"],
            "latest_activity_time": r["latest_activity_time"],
        })
    return out








# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_tracking_number():
    base = max(
        (int(s["tracking_number"][-7:]) for s in _shipments_rows()),
        default=3456784,
    )
    return f"1Z999AA101{base + 11:07d}"


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Rating  (POST /api/rating/v1/Rate)
# ---------------------------------------------------------------------------

def get_rate(origin_zip, dest_zip, weight_lb, service_code=None):
    matches = [
        r for r in _rates_rows()
        if r["origin_zip"] == str(origin_zip) and r["dest_zip"] == str(dest_zip)
    ]
    if service_code:
        matches = [r for r in matches if r["service_code"] == service_code]
    if not matches:
        return {"error": f"no rates found for {origin_zip} -> {dest_zip}"}
    weight = _to_float(weight_lb) or 1.0
    rated = []
    for r in matches:
        scaled = round(r["total_charge"] * (weight / (r["weight_lb"] or 1.0)), 2) if r["weight_lb"] else r["total_charge"]
        rated.append({
            "Service": {"Code": r["service_code"], "Description": r["service_name"]},
            "TotalCharges": {"CurrencyCode": r["currency"], "MonetaryValue": f"{scaled:.2f}"},
            "GuaranteedDelivery": {
                "BusinessDaysInTransit": str(r["transit_days"]),
                "DeliveryByTime": r["delivery_date"],
            },
        })
    return {
        "RateResponse": {
            "Response": {"ResponseStatus": {"Code": "1", "Description": "Success"}},
            "RatedShipment": rated,
        }
    }


# ---------------------------------------------------------------------------
# Shipping  (POST /api/shipments/v1/ship)
# ---------------------------------------------------------------------------

def create_shipment(origin_zip, dest_zip, weight_lb, service_code="03"):
    rate = next(
        (r for r in _rates_rows()
         if r["origin_zip"] == str(origin_zip)
         and r["dest_zip"] == str(dest_zip)
         and r["service_code"] == service_code),
        None,
    )
    total_charge = rate["total_charge"] if rate else 0.0
    currency = rate["currency"] if rate else "USD"
    service_name = rate["service_name"] if rate else "UPS Ground"
    tracking_number = _new_tracking_number()
    label_url = f"https://ups.example/labels/{tracking_number}.gif"
    shipment = {
        "tracking_number": tracking_number,
        "service_code": service_code,
        "service_name": service_name,
        "ship_date": _today(),
        "origin_zip": str(origin_zip),
        "dest_zip": str(dest_zip),
        "weight_lb": _to_float(weight_lb),
        "currency": currency,
        "total_charge": total_charge,
        "label_url": label_url,
    }
    _store_insert("shipments", shipment)
    _store_insert("tracking", {
        "tracking_number": tracking_number,
        "status_type": "M",
        "status_code": "003",
        "status_description": "Label Created",
        "service_name": service_name,
        "ship_date": shipment["ship_date"],
        "scheduled_delivery": (datetime.now(timezone.utc) + timedelta(days=(rate["transit_days"] if rate else 5))).strftime("%Y-%m-%d"),
        "latest_activity": "Shipper created a label, UPS has not received the package yet.",
        "latest_activity_location": str(origin_zip),
        "latest_activity_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return {
        "ShipmentResponse": {
            "Response": {"ResponseStatus": {"Code": "1", "Description": "Success"}},
            "ShipmentResults": {
                "ShipmentIdentificationNumber": tracking_number,
                "ShipmentCharges": {
                    "TotalCharges": {"CurrencyCode": currency, "MonetaryValue": f"{total_charge:.2f}"},
                },
                "PackageResults": [{
                    "TrackingNumber": tracking_number,
                    "ShippingLabel": {"ImageFormat": {"Code": "GIF"}, "GraphicImage": label_url},
                }],
            },
        }
    }


# ---------------------------------------------------------------------------
# Tracking  (GET /api/track/v1/details/{trackingNumber})
# ---------------------------------------------------------------------------

def track(tracking_number):
    t = next((x for x in _tracking_rows() if x["tracking_number"] == str(tracking_number)), None)
    if not t:
        return {"error": f"tracking number {tracking_number} not found"}
    return {
        "trackResponse": {
            "shipment": [{
                "package": [{
                    "trackingNumber": t["tracking_number"],
                    "currentStatus": {
                        "type": t["status_type"],
                        "code": t["status_code"],
                        "description": t["status_description"],
                    },
                    "service": {"description": t["service_name"]},
                    "deliveryDate": [{"type": "SDD", "date": t["scheduled_delivery"]}],
                    "activity": [{
                        "status": {
                            "type": t["status_type"],
                            "code": t["status_code"],
                            "description": t["latest_activity"],
                        },
                        "location": {"address": {"city": t["latest_activity_location"]}},
                        "date": t["latest_activity_time"][:10].replace("-", ""),
                        "time": t["latest_activity_time"],
                    }],
                }],
            }],
        }
    }
