from config import Config
from gcalendar import GoogleCalendarClient
from logger import logger
from models import Event
from timetree import TimeTree


def _private_properties(google_event: dict) -> dict:
    return google_event.get("extendedProperties", {}).get("private", {})


def _is_managed_google_event(google_event: dict, calendar_code: str) -> bool:
    """Return True only for events owned by this TimeTree sync.

    Events created by older versions are also accepted when they contain a
    timetree_id but do not yet have the newer ownership markers.
    """
    props = _private_properties(google_event)
    timetree_id = props.get("timetree_id")

    if not timetree_id:
        return False

    sync_source = props.get("sync_source")
    source_calendar_code = props.get("timetree_calendar_code")

    # Backward compatibility for events created by older versions.
    if sync_source is None and source_calendar_code is None:
        return True

    return sync_source == Event.SYNC_SOURCE and source_calendar_code == calendar_code


def sync():
    client = TimeTree()
    client.login(
        Config.TIMETREE_EMAIL,
        Config.TIMETREE_PASSWORD,
    )
    calendar = client.get_calendar(
        Config.TIMETREE_CALENDAR_CODE,
    )
    logger.info("Selected TimeTree calendar")

    try:
        labels = client.get_labels(calendar)
        logger.info("Fetched %d labels", len(labels))
        labels_ok = True
    except Exception:
        logger.warning("Failed to fetch labels; syncing without colors", exc_info=True)
        labels = {}
        labels_ok = False

    raw_events = client.get_events(calendar)
    events = [Event.from_timetree(raw, labels) for raw in raw_events]

    # User rule (2026-11-01): sync only the user's own events — those whose
    # TimeTree label maps to Google colorId 5 (yellow) or 2 (green). All
    # other events (the wife's blue 7 / pink 4, any other color, unlabeled)
    # are excluded, and their existing Google copies are removed by the
    # cleanup pass below because their IDs fall out of timetree_ids.
    # Safety: if labels could not be fetched, skip the filter entirely —
    # otherwise every event would look unlabeled and all Google copies
    # would be deleted.
    if labels_ok:
        allowed_color_ids = {"5", "2"}
        total_events = len(events)
        events = [e for e in events if e.color_id in allowed_color_ids]
        logger.info(
            "Color filter: kept %d of %d events (colorId 5/2 only)",
            len(events),
            total_events,
        )
    else:
        logger.warning("Color filter skipped because labels are unavailable")

    timetree_ids = {event.id for event in events}

    google = GoogleCalendarClient(
        Config.GOOGLE_SERVICE_ACCOUNT_JSON,
    )
    logger.info("Connected to Google Calendar")

    google_events = google.list_events(
        Config.GOOGLE_CALENDAR_ID,
    )

    # Keep all Google copies for each TimeTree ID. This lets us clean up
    # duplicates left by older versions or interrupted sync runs.
    google_event_map: dict[str, list[dict]] = {}

    for google_event in google_events:
        if not _is_managed_google_event(
            google_event,
            Config.TIMETREE_CALENDAR_CODE,
        ):
            # Native Google Calendar events and events managed by another
            # calendar/workflow are intentionally ignored.
            continue

        timetree_id = _private_properties(google_event)["timetree_id"]
        google_event_map.setdefault(timetree_id, []).append(google_event)

    created_count = 0
    updated_count = 0
    deleted_count = 0
    skipped_count = 0

    for event in events:
        matches = google_event_map.get(event.id, [])
        if not matches:
            google.create_event(
                Config.GOOGLE_CALENDAR_ID,
                event.to_google(Config.TIMETREE_CALENDAR_CODE),
            )
            created_count += 1
            continue

        # Keep one canonical Google event for this TimeTree event.
        google_event = matches[0]
        props = _private_properties(google_event)
        needs_marker_migration = (
            props.get("sync_source") != Event.SYNC_SOURCE
            or props.get("timetree_calendar_code") != Config.TIMETREE_CALENDAR_CODE
        )

        if needs_marker_migration or not event.equals_google(
            google_event,
            Config.TIMETREE_CALENDAR_CODE,
        ):
            google.update_event(
                Config.GOOGLE_CALENDAR_ID,
                google_event["id"],
                event.to_google(Config.TIMETREE_CALENDAR_CODE),
            )
            updated_count += 1
        else:
            skipped_count += 1

        # Remove duplicate Google copies carrying the same TimeTree ID.
        for duplicate in matches[1:]:
            google.delete_event(
                Config.GOOGLE_CALENDAR_ID,
                duplicate["id"],
            )
            deleted_count += 1

    # Delete Google events owned by this sync when the source TimeTree event
    # no longer exists. Native Google Calendar events are never included in
    # google_event_map, so they are left untouched.
    for timetree_id, matches in google_event_map.items():
        if timetree_id in timetree_ids:
            continue

        for google_event in matches:
            google.delete_event(
                Config.GOOGLE_CALENDAR_ID,
                google_event["id"],
            )
            deleted_count += 1

    logger.info(
        "Sync complete: created=%d updated=%d deleted=%d skipped=%d",
        created_count,
        updated_count,
        deleted_count,
        skipped_count,
    )
