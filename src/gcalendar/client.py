import json
import time
from typing import Callable, ClassVar, TypeVar


from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from logger import logger


T = TypeVar("T")

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRYABLE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_MAX_ATTEMPTS = 6
_INITIAL_DELAY_SECONDS = 5.0
_MAX_DELAY_SECONDS = 60.0
_WRITE_PACING_SECONDS = 0.2


def _error_reason(error: HttpError) -> str:
    try:
        details = json.loads(error.content.decode("utf-8"))
        errors = details.get("error", {}).get("errors", [])
        if errors:
            return str(errors[0].get("reason", ""))
    except Exception:
        pass
    return ""


def _is_retryable(error: HttpError) -> bool:
    resp = getattr(error, "resp", None)
    status = getattr(resp, "status", None) if resp is not None else None
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    if status == 403:
        return _error_reason(error) in _RETRYABLE_REASONS
    return status in _RETRYABLE_STATUSES


def _retry_after_seconds(error: HttpError):
    resp = getattr(error, "resp", None)
    if resp is None:
        return None
    try:
        value = resp.get("retry-after")
    except Exception:
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _execute_with_retry(operation: str, make_request: Callable[[], T]) -> T:
    delay = _INITIAL_DELAY_SECONDS
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return make_request()
        except HttpError as error:
            if not _is_retryable(error) or attempt == _MAX_ATTEMPTS:
                raise
            retry_after = _retry_after_seconds(error)
            wait = retry_after if retry_after is not None else delay
            logger.warning(
                "Google Calendar API %s failed with %s (attempt %d/%d); "
                "retrying in %.1f seconds",
                operation,
                error,
                attempt,
                _MAX_ATTEMPTS,
                wait,
            )
            time.sleep(wait)
            delay = min(delay * 2, _MAX_DELAY_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


class GoogleCalendarClient:
    SCOPES: ClassVar[list[str]] = ["https://www.googleapis.com/auth/calendar"]

    def __init__(self, credentials_json: str):
        credentials_info = json.loads(credentials_json)

        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=self.SCOPES,
        )

        self._service = build(
            "calendar",
            "v3",
            credentials=credentials,
        )

    def get_calendar(self, calendar_id: str):
        return _execute_with_retry(
            "get calendar",
            lambda: self._service.calendars()
            .get(calendarId=calendar_id)
            .execute(),
        )

    def create_event(self, calendar_id: str, event: dict):
        result = _execute_with_retry(
            "create event",
            lambda: self._service.events()
            .insert(
                calendarId=calendar_id,
                body=event,
            )
            .execute(),
        )
        time.sleep(_WRITE_PACING_SECONDS)
        return result

    def list_events(self, calendar_id: str):
        events = []
        page_token = None
        while True:
            response = _execute_with_retry(
                "list events",
                lambda: self._service.events()
                .list(
                    calendarId=calendar_id,
                    singleEvents=True,
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute(),
            )
            events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return events

    def update_event(self, calendar_id: str, event_id: str, event: dict):
        result = _execute_with_retry(
            "update event",
            lambda: self._service.events()
            .update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event,
            )
            .execute(),
        )
        time.sleep(_WRITE_PACING_SECONDS)
        return result

    def delete_event(self, calendar_id: str, event_id: str):
        result = _execute_with_retry(
            "delete event",
            lambda: self._service.events()
            .delete(
                calendarId=calendar_id,
                eventId=event_id,
            )
            .execute(),
        )
        time.sleep(_WRITE_PACING_SECONDS)
        return result
