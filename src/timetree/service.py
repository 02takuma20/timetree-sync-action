from timetree_exporter.api.auth import login
from timetree_exporter.api.calendar import TimeTreeCalendar
from timetree_exporter.calendar import Calendar


class TimeTreeService:
    """Bridge to the bundled TimeTree-exporter."""

    def login(self, email: str, password: str) -> None:
        session_id = login(email, password)
        if session_id is None:
            raise RuntimeError("Failed to login to TimeTree.")
        self._calendar_api = TimeTreeCalendar(session_id)

    def get_calendars(self) -> list[dict]:
        if not hasattr(self, "_calendar_api"):
            raise RuntimeError("Not logged in.")
        calendars = self._calendar_api.get_metadata()
        return [
            calendar for calendar in calendars if calendar["deactivated_at"] is None
        ]

    def get_calendar(self, alias_code: str) -> Calendar:
        calendars = self.get_calendars()

        for metadata in calendars:
            if metadata["alias_code"] == alias_code:
                return Calendar(
                    api=self._calendar_api,
                    metadata=metadata,
                )
        raise RuntimeError(f"Calendar '{alias_code}' not found.")

    def get_events(self, calendar: Calendar):
        return calendar.get_events()

    def get_labels(self, calendar: Calendar):
        return calendar.get_labels()


