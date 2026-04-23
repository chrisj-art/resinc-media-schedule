#!/usr/bin/env python3
"""
sync_calendar.py - RESINC Media Schedule -> Google Calendar sync
Uses Domain-Wide Delegation to write events as chris.j@resinc.com.au
Fetches people from Supabase to resolve attendeeIds -> email addresses
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

# -- Config -------------------------------------------------------------------

CALENDAR_ID = os.environ["GOOGLE_CALENDAR_ID"]
SERVICE_KEY_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_KEY"]
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://kosqyettdnibrxskwgfn.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtvc3F5ZXR0ZG5pYnJ4c2t3Z2ZuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4MTI3NDIsImV4cCI6MjA5MjM4ODc0Mn0.JccP4W0dVw-kcbKlGOwWzwsNwPEb8rBVujN6mQliuMQ")
IMPERSONATE_USER = "chris.j@resinc.com.au"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
EVENTS_FILE = os.environ.get("RESINC_EVENTS_FILE", "resinc_events.json")
SOURCE_TAG = "resinc-media-schedule"

# -- Auth ---------------------------------------------------------------------

def get_calendar_service():
    key_data = json.loads(SERVICE_KEY_JSON)
    creds = service_account.Credentials.from_service_account_info(
        key_data,
        scopes=SCOPES,
        subject=IMPERSONATE_USER,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

# -- Fetch people from Supabase -----------------------------------------------

def fetch_people():
    """Returns a dict of {person_id: email} from Supabase."""
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

# -- Helpers ------------------------------------------------------------------

def to_rfc3339_date(date_str):
    return {"date": date_str}

def to_rfc3339_datetime(date_str, time_str, tz="Australia/Brisbane"):
    dt = datetime.strptime(f"{date_str}T{time_str}", "%Y-%m-%dT%H:%M")
    return {"dateTime": dt.isoformat(), "timeZone": tz}

def build_gcal_event(ev, projects, people_map):
    project = next((p for p in projects if p["id"] == ev.get("projectId")), None)
    project_name = project["name"] if project else "RESINC"
    schedule_label = {
        "preprod": "Pre-Production",
        "production": "Production",
        "release": "Release",
    }.get(ev.get("scheduleType", ""), ev.get("scheduleType", ""))

    summary = f"[{schedule_label}] {ev['title']} - {project_name}"

    if ev.get("allDay", True) or not ev.get("startTime"):
        start = to_rfc3339_date(ev["date"])
        end_date = ev.get("endDate") or ev["date"]
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end = to_rfc3339_date(end_dt.strftime("%Y-%m-%d"))
    else:
        start = to_rfc3339_datetime(ev["date"], ev["startTime"])
        end_time = ev.get("endTime") or ev["startTime"]
        end = to_rfc3339_datetime(ev.get("endDate") or ev["date"], end_time)

    description_parts = []
    if ev.get("details"):
        description_parts.append(ev["details"])
    if ev.get("driveLink"):
        description_parts.append(f"Drive: {ev['driveLink']}")
    description_parts.append(f"Status: {ev.get('status', 'todo')}")
    description_parts.append(f"Source: {SOURCE_TAG}/{ev['id']}")

    # Build attendees list from attendeeIds
    attendees = []
    for person_id in ev.get("attendeeIds", []):
        email = people_map.get(person_id)
        if email:
            attendees.append({"email": email})

    gcal_event = {
        "summary": summary,
        "location": ev.get("location", ""),
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
    created = updated = deleted = 0

    for ev in resinc_events:
        gcal_body = build_gcal_event(ev, projects, people_map)
        if ev["id"] in existing:
            gcal_id = existing[ev["id"]]["id"]
            service.events().update(
                calendarId=CALENDAR_ID, eventId=gcal_id, body=gcal_body
            ).execute()
            updated += 1
        else:
            service.events().insert(
                calendarId=CALENDAR_ID, body=gcal_body
            ).execute()
            created += 1

    for rid, gcal_ev in existing.items():
        if rid not in resinc_ids:
            service.events().delete(
                calendarId=CALENDAR_ID, eventId=gcal_ev["id"]
            ).execute()
            deleted += 1

    print(f"Sync complete - created: {created}, updated: {updated}, deleted: {deleted}")

# -- Entry point --------------------------------------------------------------

def main():
    if not os.path.exists(EVENTS_FILE):
        print(f"Events file not found: {EVENTS_FILE}")
        sys.exit(1)

    with open(EVENTS_FILE) as f:
        data = json.load(f)

    resinc_events = data.get("events", [])
    projects = data.get("projects", [])

    if not resinc_events:
        print("No events to sync.")
        sys.exit(0)

    print(f"Fetching people from Supabase...")
    people_map = fetch_people()
    print(f"Found {len(people_map)} people with email addresses")

    print(f"Syncing {len(resinc_events)} event(s) to Google Calendar...")
    service = get_calendar_service()
    sync(service, resinc_events, projects, people_map)

if __name__ == "__main__":
    main()
