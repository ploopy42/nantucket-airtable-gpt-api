# ============================================================
# GPT Airtable API Wrapper
#
# Purpose:
# - Gives a Custom GPT safe, limited ways to write to Airtable
# - Searches Properties
# - Creates Property Events
# - Creates Notifications / Tasks
#
# Run locally with:
# cd /Users/student/Downloads/Nantucket_VIS_Project
# uv run --with fastapi --with uvicorn --with requests python gpt_airtable_api.py
#
# Local docs:
# http://127.0.0.1:8000/docs
# ============================================================

import os
import re
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
import uvicorn

from airtable_sync_helpers import (
    read_all_properties,
    read_property_by_pid,
    create_property_event,
    create_task,
)

try:
    from config_local.config_local import GPT_ACTION_API_KEY
except Exception:
    GPT_ACTION_API_KEY = os.environ.get("GPT_ACTION_API_KEY", "")


app = FastAPI(
    title="Nantucket Airtable GPT API",
    version="1.0.0",
    description="Safe API wrapper for Custom GPT to create Airtable property events and notifications.",
)


# ============================================================
# Auth
# ============================================================

def require_api_key(x_api_key: Optional[str]):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header.")

    if x_api_key != GPT_ACTION_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key.")


# ============================================================
# Helpers
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_address(value):
    value = clean_text(value).upper()

    replacements = {
        " ROAD": " RD",
        " LANE": " LN",
        " STREET": " ST",
        " AVENUE": " AVE",
        " DRIVE": " DR",
        " WAY": " WY",
        " CIRCLE": " CIR",
        " COURT": " CT",
        " PLACE": " PL",
        " BOULEVARD": " BLVD",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = re.sub(r"[^A-Z0-9 ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value


def property_summary(record):
    fields = record.get("fields", {})

    return {
        "record_id": record.get("id"),
        "pid": clean_text(fields.get("pid")),
        "property_address": clean_text(fields.get("property_address")),
        "normalized_property_address": clean_text(fields.get("normalized_property_address")),
        "owner_name": clean_text(fields.get("owner_name")),
        "mailing_address": clean_text(fields.get("mailing_address")),
        "last_sale_date": clean_text(fields.get("last_sale_date")),
        "last_sale_price": clean_text(fields.get("last_sale_price")),
        "lead_status": clean_text(fields.get("lead_status")),
        "exclude_from_weekly_refresh": bool(fields.get("exclude_from_weekly_refresh")),
        "owner_category": clean_text(fields.get("owner_category")),
    }


def search_properties_by_address(query, limit=10):
    query_norm = normalize_address(query)

    if not query_norm:
        return []

    records = read_all_properties()
    matches = []

    for record in records:
        fields = record.get("fields", {})

        property_address = clean_text(fields.get("property_address"))
        normalized_property_address = clean_text(fields.get("normalized_property_address"))

        address_norm = normalize_address(normalized_property_address or property_address)

        if not address_norm:
            continue

        score = 0

        if address_norm == query_norm:
            score = 100
        elif query_norm in address_norm:
            score = 80
        elif address_norm in query_norm:
            score = 70
        else:
            query_parts = set(query_norm.split())
            address_parts = set(address_norm.split())

            if query_parts and address_parts:
                overlap = len(query_parts & address_parts)
                if overlap >= 2:
                    score = 50 + overlap

        if score > 0:
            item = property_summary(record)
            item["match_score"] = score
            matches.append(item)

    matches = sorted(matches, key=lambda x: x["match_score"], reverse=True)

    return matches[:limit]


def find_best_property(pid=None, property_address=None):
    if pid:
        record = read_property_by_pid(pid)
        if record:
            return property_summary(record), []

    if property_address:
        matches = search_properties_by_address(property_address, limit=10)

        if len(matches) == 1:
            return matches[0], matches

        if len(matches) > 1 and matches[0]["match_score"] >= 90:
            return matches[0], matches

        return None, matches

    return None, []


def build_manual_event_summary(event_type, property_address, owner_name):
    property_address = clean_text(property_address) or "unknown property"
    owner_name = clean_text(owner_name)

    if event_type == "owner_deceased":
        return f"Owner death reported for {property_address}"

    if event_type == "possible_move":
        return f"Possible move/sale signal reported for {property_address}"

    if event_type == "family_transition":
        return f"Family transition reported for {property_address}"

    if event_type == "estate_activity":
        return f"Estate activity reported for {property_address}"

    if owner_name:
        return f"Manual lead note for {property_address} / {owner_name}"

    return f"Manual lead note for {property_address}"


# ============================================================
# Models
# ============================================================

class SearchPropertyRequest(BaseModel):
    query: str = Field(..., description="Address or partial address to search for.")
    limit: int = Field(10, description="Maximum number of results to return.")


class CreatePotentialLeadRequest(BaseModel):
    property_address: str = Field(..., description="Property address mentioned by the user.")
    note: str = Field(..., description="Plain-English note/intelligence from the user.")
    owner_name: Optional[str] = Field(None, description="Owner name if mentioned.")
    pid: Optional[str] = Field(None, description="Vision PID if known.")
    event_type: str = Field(
        "manual_note",
        description="Event type such as manual_note, possible_move, owner_deceased, estate_activity, family_transition.",
    )
    confidence: str = Field("Medium", description="Low, Medium, or High.")
    priority: str = Field("Medium", description="Low, Medium, High, or Urgent.")
    requires_review: bool = Field(True, description="Whether a human should review this before outreach.")
    suggested_lead_status: str = Field("Potential listing", description="Suggested property lead status.")


class CreateManualEventRequest(BaseModel):
    property_address: str = Field(..., description="Property address mentioned by the user.")
    event_summary: str = Field(..., description="Short summary of the event.")
    source_detail: str = Field(..., description="Detailed note/source text.")
    owner_name: Optional[str] = None
    pid: Optional[str] = None
    event_type: str = "manual_note"
    confidence: str = "Medium"
    priority: str = "Medium"
    notification_type: str = "Potential lead"
    requires_review: bool = True
    suggested_lead_status: str = "Potential listing"


# ============================================================
# Routes
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "Nantucket Airtable GPT API",
    }


@app.post("/search-property")
def search_property(
    request: SearchPropertyRequest,
    x_api_key: Optional[str] = Header(None),
):
    require_api_key(x_api_key)

    matches = search_properties_by_address(
        query=request.query,
        limit=request.limit,
    )

    return {
        "query": request.query,
        "match_count": len(matches),
        "matches": matches,
    }


@app.post("/create-potential-lead")
def create_potential_lead(
    request: CreatePotentialLeadRequest,
    x_api_key: Optional[str] = Header(None),
):
    require_api_key(x_api_key)

    matched_property, possible_matches = find_best_property(
        pid=request.pid,
        property_address=request.property_address,
    )

    property_record_id = matched_property["record_id"] if matched_property else None

    final_property_address = (
        matched_property["property_address"]
        if matched_property
        else request.property_address
    )

    final_owner_name = (
        request.owner_name
        or (matched_property["owner_name"] if matched_property else "")
    )

    event_summary = build_manual_event_summary(
        event_type=request.event_type,
        property_address=final_property_address,
        owner_name=final_owner_name,
    )

    source_detail = (
        f"User-submitted note: {request.note}\n\n"
        f"Submitted property address: {request.property_address}\n"
        f"Submitted owner name: {request.owner_name or ''}\n"
        f"Matched property: {final_property_address if matched_property else 'NO CONFIDENT MATCH'}"
    )

    created_event = create_property_event(
        event_summary=event_summary,
        event_type=request.event_type,
        source="User submitted note",
        matched_pid=matched_property["pid"] if matched_property else request.pid,
        matched_address=final_property_address,
        owner_name=final_owner_name,
        property_record_id=property_record_id,
        event_date=None,
        confidence=request.confidence,
        requires_review=request.requires_review,
        suggested_lead_status=request.suggested_lead_status,
        source_detail=source_detail,
    )

    created_task = create_task(
        task_summary=f"Review potential lead: {final_property_address}",
        notification_type="Potential lead",
        property_record_id=property_record_id,
        event_record_id=created_event.get("id"),
        priority=request.priority,
        task_status="New",
        requires_review=request.requires_review,
        task_detail=source_detail,
    )

    return {
        "status": "created",
        "matched_property": matched_property,
        "possible_matches": possible_matches if not matched_property else [],
        "property_event_record_id": created_event.get("id"),
        "task_record_id": created_task.get("id"),
        "message": "Potential lead event and notification task created in Airtable.",
    }


@app.post("/create-manual-event")
def create_manual_event(
    request: CreateManualEventRequest,
    x_api_key: Optional[str] = Header(None),
):
    require_api_key(x_api_key)

    matched_property, possible_matches = find_best_property(
        pid=request.pid,
        property_address=request.property_address,
    )

    property_record_id = matched_property["record_id"] if matched_property else None

    final_property_address = (
        matched_property["property_address"]
        if matched_property
        else request.property_address
    )

    final_owner_name = (
        request.owner_name
        or (matched_property["owner_name"] if matched_property else "")
    )

    created_event = create_property_event(
        event_summary=request.event_summary,
        event_type=request.event_type,
        source="User submitted note",
        matched_pid=matched_property["pid"] if matched_property else request.pid,
        matched_address=final_property_address,
        owner_name=final_owner_name,
        property_record_id=property_record_id,
        event_date=None,
        confidence=request.confidence,
        requires_review=request.requires_review,
        suggested_lead_status=request.suggested_lead_status,
        source_detail=request.source_detail,
    )

    created_task = create_task(
        task_summary=f"Review: {request.event_summary}",
        notification_type=request.notification_type,
        property_record_id=property_record_id,
        event_record_id=created_event.get("id"),
        priority=request.priority,
        task_status="New",
        requires_review=request.requires_review,
        task_detail=request.source_detail,
    )

    return {
        "status": "created",
        "matched_property": matched_property,
        "possible_matches": possible_matches if not matched_property else [],
        "property_event_record_id": created_event.get("id"),
        "task_record_id": created_task.get("id"),
        "message": "Manual event and notification task created in Airtable.",
    }


if __name__ == "__main__":
    uvicorn.run(
        "gpt_airtable_api:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )

