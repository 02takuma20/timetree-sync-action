from .service import TimeTreeService


class TimeTree:
    def __init__(self):
        self._service = TimeTreeService()

    def login(self, email: str, password: str):
        self._service.login(email, password)

    def get_calendars(self):
        return self._service.get_calendars()

    def get_calendar(self, alias_code: str):
        return self._service.get_calendar(alias_code)

    def get_events(self, calendar):
        return self._service.get_events(calendar)

    def get_labels(self, calendar):
        return self._service.get_labels(calendar)


