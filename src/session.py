# App Store Connect iris session using the seeded Mac mini Chrome profile
from __future__ import annotations

import asyncio
from typing import Any

import requests

IRIS_BASE = "https://appstoreconnect.apple.com/iris/"
ASC_DOMAIN = "appstoreconnect.apple.com"
CSRF_HEADER = "[asc-ui]"


# ##################################################################
# session expired
# Chrome is not logged into App Store Connect; a human must sign in
class SessionExpired(RuntimeError):
    pass


# ##################################################################
# cookie source
# iris calls need the Apple ID session cookies from a logged-in Chrome
class CookieSource:
    def cookies(self) -> list[dict[str, Any]]:
        raise NotImplementedError


# ##################################################################
# listed cookie source
# supplies already-captured cookies so iris HTTP can be tested locally
class ListedCookieSource(CookieSource):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def cookies(self) -> list[dict[str, Any]]:
        return self.items


# ##################################################################
# web driver cookie source
# reads cookies from the standing web-driver Chrome on the Mac mini
class WebDriverCookieSource(CookieSource):
    def cookies(self) -> list[dict[str, Any]]:
        return asyncio.run(self._fetch())

    async def _reject_login(self, domain: str) -> None:
        raise SessionExpired(f"App Store Connect Chrome session expired for {domain}")

    async def _fetch(self) -> list[dict[str, Any]]:
        from web_driver import WebDriver
        from web_driver.errors import LoginRequiredError

        try:
            async with WebDriver(on_login_required=self._reject_login) as driver:
                status = await driver.login_status(ASC_DOMAIN)
                if not status.get("logged_in"):
                    raise SessionExpired("App Store Connect Chrome session is not logged in on web-driver")
                return await driver.get_cookies(ASC_DOMAIN)
        except LoginRequiredError as err:
            raise SessionExpired(str(err)) from err


# ##################################################################
# cookie header
# turn Chrome cookie dicts into a Cookie request header
def cookie_header(items: list[dict[str, Any]]) -> str:
    parts = []
    for item in items:
        name = item.get("name")
        value = item.get("value")
        if name and value is not None:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


# ##################################################################
# iris client
# POST/GET appstoreconnect.apple.com/iris with session cookies
class IrisClient:
    def __init__(self, cookie_source: CookieSource, base_url: str = IRIS_BASE) -> None:
        self.cookie_source = cookie_source
        self.base_url = base_url.rstrip("/") + "/"

    def request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        header = cookie_header(self.cookie_source.cookies())
        if not header:
            raise SessionExpired("App Store Connect Chrome session has no cookies")
        response = requests.request(
            method,
            self.base_url + path.lstrip("/"),
            headers={
                "Cookie": header,
                "Content-Type": "application/json",
                "x-csrf-itc": CSRF_HEADER,
                "Connection": "close",
            },
            json=json_body,
            timeout=30,
        )
        if response.status_code in {401, 403}:
            raise SessionExpired(f"iris {method} {path} returned {response.status_code}: {response.text[:400]}")
        if not response.ok:
            raise RuntimeError(f"iris {method} {path} failed ({response.status_code}): {response.text[:800]}")
        if not response.content:
            return {}
        return response.json()


# ##################################################################
# default iris
# production client bound to the Mac mini Chrome session
def default_iris() -> IrisClient:
    return IrisClient(WebDriverCookieSource())
