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
        events = [
            event for event in events if event.color_id in allowed_color_ids
        ]
        logger.info(
            "Filtered %d TimeTree events to %d events to sync",
            len(raw_events),
            len(events),
        )

    google = GoogleCalendarClient(Config.GOOGLE_CREDENTIALS_JSON)
    logger.info("Connecting to Google Calendar")
    google.get_calendar(Config.GOOGLE_CALENDAR_ID)
    logger.info("Connected to Google Calendar")

    logger.info("Fetching Google Calendar events")
    google_events = google.list_events(Config.GOOGLE_CALENDAR_ID)

    # Index Google events by TimeTree ID, but only for events owned by
    # this sync, to avoid touching events created elsewhere.
    google_event_map: dict[str, list[dict]] = {}
    for google_event in google_events:
        if not _is_managed_google_event(
            google_event,
            Config.TIMETREE_CALENDAR_CODE,
        ):
            continue

        timetree_id = _private_properties(google_event).get("timetree_id")
        google_event_map.setdefault(timetree_id, []).append(google_event)

    timetree_ids = {event.id for event in events}

    created_count = 0
    updated_count = 0
    deleted_count = 0
    skipped_count = 0
    failed_count = 0

    for event in events:
        try:
            matches = google_event_map.get(event.id, [])
            if not matches:
                google.create_event(
                    Config.GOOGLE_CALENDAR_ID,
                    event.to_google(Config.TIMETREE_CALENDAR_CODE),
                )
                created_count += 1
                continue

            # Keep one canonical Google event per TimeTree event.
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

            # Remove duplicate Google events for this TimeTree event.
            for duplicate in matches[1:]:
                google.delete_event(
                    Config.GOOGLE_CALENDAR_ID,
                    duplicate["id"],
                )
                deleted_count += 1
        except Exception:
            failed_count += 1
            logger.error(
                "Failed to sync event %s (id=%s); continuing with remaining events",
                getattr(event, "title", "?"),
                event.id,
                exc_info=True,
            )

    # Delete Google events owned by this sync whose TimeTree events are
    # no longer present (or are now filtered out).
    for timetree_id, matches in google_event_map.items():
        if timetree_id in timetree_ids:
            continue

        for google_event in matches:
            try:
                google.delete_event(
                    Config.GOOGLE_CALENDAR_ID,
                    google_event["id"],
                )
                deleted_count += 1
            except Exception:
                failed_count += 1
                logger.error(
                    "Failed to delete orphaned Google event %s "
                    "(TimeTree event %s); continuing",
                    google_event["id"],
                    timetree_id,
                    exc_info=True,
                )

    logger.info(
        "Sync complete: created=%d updated=%d deleted=%d skipped=%d failed=%d",
        created_count,
        updated_count,
        deleted_count,
        skipped_count,
        failed_count,
    )
