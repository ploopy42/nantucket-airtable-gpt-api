# ============================================================
# Airtable Sync Helpers
#
# Reusable helper functions for:
# - reading Airtable Properties
# - finding properties by PID
# - updating property records
# - creating Property Events
# - creating Notifications / Tasks
# - creating Refresh Logs
#
# Run test with:
# cd /Users/student/Downloads/Nantucket_VIS_Project
# uv run --with requests python airtable_sync_helpers.py
# ============================================================

import time
from datetime import datetime
from urllib.parse import quote

import requests

import os

try:
    from config_local.config_local import (
        AIRTABLE_PAT,
        AIRTABLE_BASE_ID,
        TABLE_PROPERTIES,
        TABLE_PROPERTY_EVENTS,
        TABLE_TASKS,
        TABLE_REFRESH_LOGS,
    )
except Exception:
    AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "")
    AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "")

    TABLE_PROPERTIES = os.environ.get("TABLE_PROPERTIES", "Properties")
    TABLE_PROPERTY_EVENTS = os.environ.get("TABLE_PROPERTY_EVENTS", "Property Events")
    TABLE_TASKS = os.environ.get("TABLE_TASKS", "Notifications / Tasks")
    TABLE_REFRESH_LOGS = os.environ.get("TABLE_REFRESH_LOGS", "Refresh Logs")


AIRTABLE_API_BASE = "https://api.airtable.com/v0"


# ============================================================
# Basic Airtable API helpers
# ============================================================

def airtable_headers():
    return {
        "Authorization": f"Bearer {AIRTABLE_PAT.strip()}",
        "Content-Type": "application/json",
    }


def table_url(table_name):
    encoded_table_name = quote(table_name, safe="")
    return f"{AIRTABLE_API_BASE}/{AIRTABLE_BASE_ID}/{encoded_table_name}"


def airtable_request(method, table_name, payload=None, params=None):
    response = requests.request(
        method=method,
        url=table_url(table_name),
        headers=airtable_headers(),
        json=payload,
        params=params,
        timeout=60,
    )

    if response.status_code >= 400:
        print("=" * 80)
        print("AIRTABLE API ERROR")
        print("=" * 80)
        print(f"Method: {method}")
        print(f"Table: {table_name}")
        print(f"Status code: {response.status_code}")
        print(response.text)
        print("=" * 80)
        response.raise_for_status()

    return response.json() if response.text else None


# ============================================================
# Generic record helpers
# ============================================================

def list_records(table_name, fields=None, formula=None, max_records=None):
    """
    Reads records from an Airtable table.

    Handles pagination automatically.
    If max_records is None, reads all available records.
    """
    all_records = []
    offset = None

    while True:
        params = {
            "pageSize": 100,
        }

        if offset:
            params["offset"] = offset

        if fields:
            params["fields[]"] = fields

        if formula:
            params["filterByFormula"] = formula

        data = airtable_request(
            method="GET",
            table_name=table_name,
            params=params,
        )

        records = data.get("records", [])
        all_records.extend(records)

        if max_records is not None and len(all_records) >= max_records:
            return all_records[:max_records]

        offset = data.get("offset")

        if not offset:
            break

        time.sleep(0.2)

    return all_records


def create_record(table_name, fields):
    payload = {
        "records": [
            {
                "fields": fields
            }
        ]
    }

    data = airtable_request(
        method="POST",
        table_name=table_name,
        payload=payload,
    )

    return data["records"][0]


def update_record(table_name, record_id, fields):
    payload = {
        "records": [
            {
                "id": record_id,
                "fields": fields
            }
        ]
    }

    data = airtable_request(
        method="PATCH",
        table_name=table_name,
        payload=payload,
    )

    return data["records"][0]


def batch_update_records(table_name, record_updates):
    """
    record_updates should be a list of:
    {
        "id": "rec...",
        "fields": {...}
    }

    Airtable allows up to 10 records per request.
    """
    updated = []

    for i in range(0, len(record_updates), 10):
        batch = record_updates[i:i + 10]

        payload = {
            "records": batch
        }

        data = airtable_request(
            method="PATCH",
            table_name=table_name,
            payload=payload,
        )

        updated.extend(data.get("records", []))
        time.sleep(0.2)

    return updated


# ============================================================
# Properties helpers
# ============================================================

def read_all_properties():
    """
    Reads all Properties records from Airtable.
    """
    return list_records(TABLE_PROPERTIES)


def read_property_by_pid(pid):
    """
    Finds one property by PID.
    Returns the Airtable record or None.
    """
    pid = str(pid).strip().replace('"', '\\"')

    formula = f"{{pid}} = '{pid}'"

    records = list_records(
        TABLE_PROPERTIES,
        formula=formula,
        max_records=1,
    )

    return records[0] if records else None


def build_property_index_by_pid():
    """
    Reads all Properties and returns:
    {
        "1914": Airtable record,
        ...
    }
    """
    records = read_all_properties()
    index = {}

    for record in records:
        fields = record.get("fields", {})
        pid = str(fields.get("pid", "")).strip()

        if pid:
            index[pid] = record

    return index


def update_property(record_id, fields):
    """
    Updates one Properties record.
    """
    return update_record(TABLE_PROPERTIES, record_id, fields)


# ============================================================
# Property Events helpers
# ============================================================

def create_property_event(
    event_summary,
    event_type,
    source,
    matched_pid=None,
    matched_address=None,
    owner_name=None,
    property_record_id=None,
    event_date=None,
    confidence="High",
    requires_review=True,
    suggested_lead_status="Watch",
    source_detail=None,
):
    """
    Creates a row in Property Events.

    If property_record_id is provided, links the event to a Properties record.
    """
    fields = {
        "event_summary": event_summary,
        "event_type": event_type,
        "source": source,
        "confidence": confidence,
        "requires_review": requires_review,
        "suggested_lead_status": suggested_lead_status,
    }

    if matched_pid:
        fields["matched_pid"] = str(matched_pid)

    if matched_address:
        fields["matched_address"] = matched_address

    if owner_name:
        fields["owner_name"] = owner_name

    if event_date:
        fields["event_date"] = event_date

    if source_detail:
        fields["source_detail"] = source_detail

    if property_record_id:
        # Linked-record fields must be lists of Airtable record IDs.
        fields["property"] = [property_record_id]

    return create_record(TABLE_PROPERTY_EVENTS, fields)


# ============================================================
# Notifications / Tasks helpers
# ============================================================

def create_task(
    task_summary,
    notification_type,
    property_record_id=None,
    event_record_id=None,
    priority="Medium",
    task_status="New",
    due_date=None,
    requires_review=True,
    task_detail=None,
):
    """
    Creates a row in Notifications / Tasks.
    """
    fields = {
        "task_summary": task_summary,
        "notification_type": notification_type,
        "priority": priority,
        "task_status": task_status,
        "requires_review": requires_review,
    }

    if property_record_id:
        fields["property"] = [property_record_id]

    if event_record_id:
        fields["event"] = [event_record_id]

    if due_date:
        fields["due_date"] = due_date

    if task_detail:
        fields["task_detail"] = task_detail

    return create_record(TABLE_TASKS, fields)


# ============================================================
# Refresh Logs helpers
# ============================================================

def create_refresh_log(
    refresh_name,
    refresh_type,
    source,
    status,
    records_processed=0,
    valid_records=0,
    invalid_records=0,
    new_records=0,
    updated_records=0,
    error_count=0,
    notes=None,
):
    """
    Creates a row in Refresh Logs.

    This intentionally does not write started_at/completed_at because
    your current Airtable field types rejected API-written dates.
    """
    fields = {
        "refresh_name": refresh_name,
        "refresh_type": refresh_type,
        "source": source,
        "status": status,
        "records_processed": records_processed,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "new_records": new_records,
        "updated_records": updated_records,
        "error_count": error_count,
    }

    if notes:
        fields["notes"] = notes

    return create_record(TABLE_REFRESH_LOGS, fields)


# ============================================================
# Local test
# ============================================================

def test_helpers():
    print("=" * 80)
    print("Airtable Sync Helpers Test")
    print("=" * 80)

    print("Reading all Properties from Airtable...")
    properties = read_all_properties()

    print(f"Properties records read: {len(properties):,}")

    if properties:
        first = properties[0]
        fields = first.get("fields", {})

        print("-" * 80)
        print("First property:")
        print(f"Airtable record ID: {first.get('id')}")
        print(f"PID:                {fields.get('pid')}")
        print(f"Address:            {fields.get('property_address')}")
        print(f"Owner:              {fields.get('owner_name')}")
        print("-" * 80)

        test_pid = fields.get("pid")

        if test_pid:
            print(f"Testing PID lookup for PID {test_pid}...")
            matched = read_property_by_pid(test_pid)

            if matched:
                print("PID lookup worked.")
                print(f"Matched record ID: {matched.get('id')}")
            else:
                print("PID lookup failed.")

    print("Creating helper test Refresh Logs record...")

    today = datetime.now().date().isoformat()

    create_refresh_log(
        refresh_name=f"Helper test - {today}",
        refresh_type="Manual update",
        source="Other",
        status="Completed",
        records_processed=len(properties),
        valid_records=len(properties),
        invalid_records=0,
        new_records=0,
        updated_records=0,
        error_count=0,
        notes="This test record was created by airtable_sync_helpers.py. Safe to delete.",
    )

    print("Refresh Logs test record created.")
    print("=" * 80)
    print("Airtable sync helpers test completed successfully.")
    print("=" * 80)


if __name__ == "__main__":
    test_helpers()
