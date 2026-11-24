# Exact owner-retired App Store identities. Keep this registry deliberately small:
# publishing and every release follow-through surface import the same entries.
from dataclasses import dataclass


@dataclass(frozen=True)
class RetiredApp:
    app_id: str
    bundle_id: str
    name: str


RETIRED_APPS = (
    RetiredApp("6759585788", "com.darrenoakey.olVideos", "OL Videos"),
    RetiredApp("1608809460", "com.darrenoakey.dazmusic", "daz_music"),
)


def retirement_for(*, app_id: str = "", bundle_id: str = "", name: str = "") -> RetiredApp | None:
    """Match exact identities only; never retire a similarly named active app."""
    for app in RETIRED_APPS:
        if app_id and app_id == app.app_id:
            return app
        if bundle_id and bundle_id == app.bundle_id:
            return app
        if name and name == app.name:
            return app
    return None


def is_retired(*, app_id: str = "", bundle_id: str = "", name: str = "") -> bool:
    return retirement_for(app_id=app_id, bundle_id=bundle_id, name=name) is not None


def filter_delivery_entries(entries: dict) -> tuple[dict, int]:
    """Remove retired identities, including old fingerprints with no bundle id."""
    kept = {}
    removed = 0
    for fingerprint, entry in entries.items():
        parts = fingerprint.split("|")
        app_id = parts[1] if len(parts) > 1 else ""
        line = entry.get("line", "") if isinstance(entry, dict) else ""
        name = entry.get("app", "") if isinstance(entry, dict) else ""
        retired = is_retired(app_id=app_id, name=name) or any(
            f": {app.name} ({app.app_id})" in line for app in RETIRED_APPS
        )
        if retired:
            removed += 1
        else:
            kept[fingerprint] = entry
    return kept, removed
