from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from zoneinfo import ZoneInfo

# Google Calendar event color palette: colorId -> (red, green, blue).
GOOGLE_EVENT_COLORS: dict[str, tuple[int, int, int]] = {
    "1": (0x79, 0x86, 0xCB),  # Lavender
    "2": (0x33, 0xB6, 0x79),  # Sage
    "3": (0x8E, 0x24, 0xAA),  # Grape
    "4": (0xE6, 0x7C, 0x73),  # Flamingo
    "5": (0xF6, 0xBF, 0x26),  # Banana (yellow)
    "6": (0xF4, 0x51, 0x1E),  # Tangerine
    "7": (0x03, 0x9B, 0xE5),  # Peacock
    "8": (0x61, 0x61, 0x61),  # Graphite
    "9": (0x3F, 0x51, 0xB5),  # Blueberry (blue)
    "10": (0x0B, 0x80, 0x43),  # Basil (green)
    "11": (0xD5, 0x00, 0x00),  # Tomato
}


def _parse_hex_color(color) -> tuple[int, int, int] | None:
    """Parse a TimeTree label color (int or '#rrggbb' string) into RGB."""
    if isinstance(color, int):
        return (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    if isinstance(color, str):
        text = color.strip().lstrip("#")
        if len(text) == 6:
            try:
                value = int(text, 16)
            except ValueError:
                return None
            return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
    return None


def nearest_google_color_id(color) -> str | None:
    """Return the Google colorId closest to a TimeTree label color."""
    rgb = _parse_hex_color(color)
    if rgb is None:
        return None

    def distance(item: tuple[str, tuple[int, int, int]]) -> int:
        gr, gg, gb = item[1]
        return (rgb[0] - gr) ** 2 + (rgb[1] - gg) ** 2 + (rgb[2] - gb) ** 2

    return min(GOOGLE_EVENT_COLORS.items(), key=distance)[0]


def extract_label_id(event: dict):
    """Extract the TimeTree label id from a raw event payload."""
    label_id = event.get("label_id")
    if label_id is not None:
        return label_id

    label = event.get("label")
    if isinstance(label, dict) and label.get("id") is not None:
        return label["id"]

    try:
        rel_id = event["relationships"]["label"]["data"]["id"]
    except (KeyError, TypeError):
        return None
    # Format may be "calendar_id,label_number" — keep the label number.
    if isinstance(rel_id, str) and "," in rel_id:
        rel_id = rel_id.split(",")[-1]
        try:
            return int(rel_id)
        except ValueError:
            return rel_id
    return rel_id


@dataclass
class Event:
    SYNC_SOURCE: ClassVar[str] = "timetree-sync-action"

    id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    location: str | None = None
    description: str | None = None
    color_id: str | None = None

    @classmethod
    def from_timetree(cls, event: dict, labels: dict | None = None):
        start_tz = ZoneInfo(event["start_timezone"])
        end_tz = ZoneInfo(event["end_timezone"])

        color_id = None
        if labels:
            label_id = extract_label_id(event)
            if label_id is not None:
                label = labels.get(label_id)
                if label is None:
                    label = labels.get(str(label_id))
                if label is not None:
                    color_id = nearest_google_color_id(label.get("color"))

        return cls(
            id=event["id"],
            title=event["title"],
            start=datetime.fromtimestamp(
                event["start_at"] / 1000,
                tz=start_tz,
            ),
            end=datetime.fromtimestamp(
                event["end_at"] / 1000,
                tz=end_tz,
            ),
            all_day=event["all_day"],
            location=event.get("location") or None,
            description=event.get("note") or None,
            color_id=color_id,
        )

    def to_google(self, calendar_code: str) -> dict:
        event = {
            "summary": self.title,
            "extendedProperties": {
                "private": {
                    "sync_source": self.SYNC_SOURCE,
                    "timetree_calendar_code": calendar_code,
                    "timetree_id": self.id,
                }
            },
        }

        if self.color_id:
            event["colorId"] = self.color_id

        if self.all_day:
            event["start"] = {
                "date": self.start.strftime("%Y-%m-%d"),
            }
            event["end"] = {
                "date": self.end.strftime("%Y-%m-%d"),
            }
        else:
            event["start"] = {
                "dateTime": self.start.isoformat(),
                "timeZone": "Asia/Tokyo",
            }
            event["end"] = {
                "dateTime": self.end.isoformat(),
                "timeZone": "Asia/Tokyo",
            }

        if self.location:
            event["location"] = self.location

        if self.description:
            event["description"] = self.description

        return event

    def equals_google(self, google_event: dict, calendar_code: str) -> bool:
        google = self.to_google(calendar_code)

        return (
            google.get("summary") == google_event.get("summary")
            and google.get("location") == google_event.get("location")
            and google.get("description") == google_event.get("description")
            and google.get("start") == google_event.get("start")
            and google.get("end") == google_event.get("end")
            and google.get("colorId") == google_event.get("colorId")
        )
