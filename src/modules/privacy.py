# publish App Privacy Details as DATA_NOT_COLLECTED via the iris session
from __future__ import annotations

from pathlib import Path

from session import IrisClient, SessionExpired, default_iris
from state import ProjectState
from notices import PostgresNoticeStore
from utils import print_error, print_info, print_success


# ##################################################################
# not collected body
# Spaceship AppDataUsage.create with protection DATA_NOT_COLLECTED
def not_collected_body(app_id: str) -> dict:
    return {
        "data": {
            "type": "appDataUsages",
            "relationships": {
                "app": {"data": {"type": "apps", "id": app_id}},
                "dataProtection": {
                    "data": {
                        "type": "appDataUsageDataProtections",
                        "id": "DATA_NOT_COLLECTED",
                    }
                },
            },
        }
    }


# ##################################################################
# publish not collected
# POST iris /v1/appDataUsages for this app id
def publish_state_body(app_id: str) -> dict:
    return {
        "data": {
            "type": "appDataUsagesPublishState",
            "id": app_id,
            "attributes": {"published": True},
        }
    }


def publish_not_collected(iris: IrisClient, app_id: str) -> dict:
    if not app_id:
        raise ValueError("publish_not_collected requires an app id")
    existing = iris.request("GET", f"v1/apps/{app_id}/dataUsages?limit=1")
    if not existing.get("data"):
        iris.request("POST", "v1/appDataUsages", json_body=not_collected_body(app_id))
    # creating the usage only drafts the answers; review submission requires
    # the publish state to be flipped, exactly as the "Publish" button does
    return iris.request(
        "PATCH",
        f"v1/appDataUsagesPublishState/{app_id}",
        json_body=publish_state_body(app_id),
    )


# ##################################################################
# run
# pipeline step: declare that this app does not collect data
def run(
    project_path: Path,
    state: ProjectState,
    iris: IrisClient | None = None,
    notices=None,
) -> bool:
    del project_path
    if not state.app_store_id:
        print_error("No App Store id - run appstore_create first")
        return False
    client = iris or default_iris()
    try:
        publish_not_collected(client, state.app_store_id)
    except SessionExpired as err:
        store = notices or PostgresNoticeStore()
        store.notify(
            "app-publish",
            f"session-privacy:{state.app_store_id}",
            (
                "App Store Connect Chrome session expired. Cannot publish "
                f"DATA_NOT_COLLECTED for {state.app_name} ({state.bundle_id}). "
                "Log into appstoreconnect.apple.com in the Mac mini Chrome "
                "profile used by web-driver."
            ),
        )
        print_error(str(err))
        return False
    print_success("Privacy labels published as DATA_NOT_COLLECTED")
    print_info(f"App Store ID: {state.app_store_id}")
    return True
