#!/usr/bin/env python3
"""
sync_calendar.py - RESINC Media Schedule -> Google Calendar sync
Reads events + projects from resinc_events.json (saved by resinc-autosave.js from Supabase).
Handles preprod tasks, production events, and release events.
Uses Domain-Wide Delegation to act as chris.j@resinc.com.au.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

# -- Config -------------------------------------------------------------------

CALENDAR_ID      = os.environ["GOOGLE_CALENDAR_ID"]
SERVICE_KEY_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"]
SUPABASE_URL     = os.environ.get("SUPABASE_URL", "https://kosqyettdnibrxskwgfn.supabase.co")
SUPABASE_KEY     = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtvc3F5ZXR0ZG5pYnJ4c2t3Z2ZuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4MTI3NDIsImV4cCI6MjA5MjM4ODc0Mn0.JccP4W0dVw-kcbKlGOwWzwsNwPEb8rBVujN6mQliuMQ")
IMPERSONATE_USER = "tanya.walker@resinc.com.au"
SCOPES           = ["https://www.googleapis.com/auth/calendar"]
EVENTS_FILE      = os.environ.get("RESINC_EVENTS_FILE", "resinc_events.json")
SOURCE_TAG       = "resinc-media-schedule"

# -- Auth ---------------------------------------------------------------------

def get_calendar_service():
    key_data = json.loads(SERVICE_KEY_JSON)
    creds = service_account.Credentials.from_service_account_info(
        key_data, scopes=SCOPES, subject=IMPERSONATE_USER,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

# -- Fetch people from Supabase -----------------------------------------------

def fetch_people():
    url = SUPABASE_URL + "/rest/v1/people?select=id,name,email"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": "Bearer " + SUPABASE_KEY,
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            people = json.loads(resp.read())
            return {p["id"]: p["email"] for p in people if p.get("email")}
    except Exception as e:
        print(f"Warning: Could not fetch people from Supabase: {e}")
        return {}

# -- Field helpers (handles both Supabase snake_case and legacy camelCase) ----

def get(ev, *keys, default=None):
    """Try multiple key names, return first match."""
    for k in keys:
        if k in ev and ev[k] is not None:
            return ev[k]
    return default

# -- Build Google Calendar event body -----------------------------------------

SCHEDULE_LABELS = {
    "preprod":    "Pre-Production",
    "production": "Production",
    "release":    "Release",
}

def to_rfc3339_date(date_str):
    return {"date": date_str}

def to_rfc3339_datetime(date_str, time_str, tz="Australia/Brisbane"):
    dt = datetime.strptime(f"{date_str}T{time_str}", "%Y-%m-%dT%H:%M")
    return {"dateTime": dt.isoformat(), "timeZone": tz}

def build_gcal_event(ev, projects, people_map):
    # Field names: Supabase uses snake_case, legacy localStorage used camelCase
    project_id   = get(ev, "project_id", "projectId")
    schedule_type = get(ev, "schedule_type", "scheduleType", default="")
    title        = get(ev, "title", default="Untitled")
    date         = get(ev, "date")
    end_date     = get(ev, "end_date", "endDate") or date
    all_day      = get(ev, "all_day", "allDay", default=True)
    start_time   = get(ev, "start_time", "startTime")
    end_time     = get(ev, "end_time", "endTime")
    location     = get(ev, "location", default="") or ""
    details      = get(ev, "details", default="") or ""
    drive_link   = get(ev, "drive_link", "driveLink", default="") or ""
    status       = get(ev, "status", default="") or ""
    attendee_ids = get(ev, "attendee_ids", "attendeeIds", default=[]) or []

    project = next((p for p in projects if p.get("id") == project_id), None)
    project_name = project.get("name") if project else "RESINC"
    schedule_label = SCHEDULE_LABELS.get(schedule_type, schedule_type)

    summary = f"[{schedule_label}] {title} - {project_name}"

    if all_day or not start_time:
        start = to_rfc3339_date(date)
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end = to_rfc3339_date(end_dt.strftime("%Y-%m-%d"))
    else:
        start = to_rfc3339_datetime(date, start_time)
        end = to_rfc3339_datetime(end_date, end_time or start_time)

    description_parts = []
    if details:
        description_parts.append(details)
    if drive_link:
        description_parts.append(f"Drive: {drive_link}")
    if status:
        description_parts.append(f"Status: {status}")
    description_parts.append(f"Source: {SOURCE_TAG}/{ev['id']}")

    # Resolve attendee IDs to emails
    attendees = [{"email": people_map[pid]} for pid in attendee_ids if pid in people_map]

    gcal_event = {
        "summary": summary,
        "location": location,
        "description": "\n".join(description_parts),
        "start": start,
        "end": end,
        "extendedProperties": {
            "private": {
                "resinc_event_id": ev["id"],
                "resinc_source": SOURCE_TAG,
            }
        },
    }
    if attendees:
        gcal_event["attendees"] = attendees

    return gcal_event

# -- Sync logic ---------------------------------------------------------------

def fetch_existing_gcal_events(service):
    existing = {}
    page_token = None
    while True:
        resp = service.events().list(
            calendarId=CALENDAR_ID,
            privateExtendedProperty=f"resinc_source={SOURCE_TAG}",
            pageToken=page_token,
            maxResults=500,
        ).execute()
        for ev in resp.get("items", []):
            rid = ev.get("extendedProperties", {}).get("private", {}).get("resinc_event_id")
            if rid:
                existing[rid] = ev
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return existing

def sync(service, resinc_events, projects, people_map):
    existing = fetch_existing_gcal_events(service)
    resinc_ids = {ev["id"] for ev in resinc_events}
    created = updated = deleted = skipped = 0

    for ev in resinc_events:
        if not ev.get("date"):
            skipped += 1
            continue  # Skip events without a date (draft preprod tasks)
        try:
            gcal_body = build_gcal_event(ev, projects, people_map)
            if ev["id"] in existing:
                gcal_id = existing[ev["id"]]["id"]
                service.events().update(calendarId=CALENDAR_ID, eventId=gcal_id, body=gcal_body).execute()
                updated += 1
            else:
                service.events().insert(calendarId=CALENDAR_ID, body=gcal_body).execute()
                created += 1
        except Exception as e:
            print(f"Warning: Could not sync event {ev.get('id')}: {e}")
            skipped += 1

    for rid, gcal_ev in existing.items():
        if rid not in resinc_ids:
            service.events().delete(calendarId=CALENDAR_ID, eventId=gcal_ev["id"]).execute()
            deleted += 1

    print(f"Sync complete: created={created}, updated={updated}, deleted={deleted}, skipped={skipped}")

# -- Entry point --------------------------------------------------------------

def main():
    if not os.path.exists(EVENTS_FILE):
        print(f"Events file not found: {EVENTS_FILE}")
        sys.exit(1)

    with open(EVENTS_FILE) as f:
        data = json.load(f)

    # Support both Supabase format (list of objects) and legacy format
    resinc_events = data.get("events", [])
    projects      = data.get("projects", [])

    if not resinc_events:
        print("No events to sync.")
        sys.exit(0)

    print(f"Fetching people from Supabase...")
    people_map = fetch_people()
    print(f"Found {len(people_map)} people with email addresses")

    print(f"Syncing {len(resinc_events)} event(s) across all schedule types...")
    service = get_calendar_service()
    sync(service, resinc_events, projects, people_map)

if __name__ == "__main__":
    main()
