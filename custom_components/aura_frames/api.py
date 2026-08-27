"""Aura Frames API client."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .const import (
    API_BASE_URL,
    APP_IDENTIFIER,
    DEFAULT_LOCALE,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class AuraAuthError(Exception):
    """Raised when authentication fails."""


class AuraApiError(Exception):
    """Raised when an API request fails."""


class AuraClient:
    """Client for the Aura Frames REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        device_id: str,
        *,
        user_id: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self.device_id = device_id
        self.user_id = user_id
        self.auth_token = auth_token

    @property
    def is_authenticated(self) -> bool:
        """Return True if the client has valid credentials."""
        return bool(self.user_id and self.auth_token)

    def _base_headers(self) -> dict[str, str]:
        """Return headers common to all requests."""
        headers = {
            "Accept": "*/*",
            "Accept-Language": "de-DE;q=1",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Client-Device-Id": self.device_id,
        }
        if self.is_authenticated:
            headers["X-User-Id"] = self.user_id  # type: ignore[assignment]
            headers["X-Token-Auth"] = self.auth_token  # type: ignore[assignment]
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> Any:
        """Perform an HTTP request against the Aura API."""
        if authenticated and not self.is_authenticated:
            raise AuraAuthError("Not authenticated")

        url = f"{API_BASE_URL}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=self._base_headers(),
                json=json,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                body: Any = None
                if response.content_length != 0:
                    try:
                        body = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        body = await response.text()

                if response.status >= 400:
                    _LOGGER.debug(
                        "Aura API error %s %s: %s", method, url, body
                    )
                    message = f"API request failed ({response.status}): {body}"
                    if response.status in (401, 403):
                        raise AuraAuthError(message)
                    raise AuraApiError(message)

                return body
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AuraApiError(f"Connection error: {err}") from err

    async def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate and store session credentials."""
        payload = {
            "identifier_for_vendor": self.device_id,
            "client_device_id": self.device_id,
            "app_identifier": APP_IDENTIFIER,
            "locale": DEFAULT_LOCALE,
            "user": {"email": email, "password": password},
        }
        data = await self._request(
            "POST", "/login.json", json=payload, authenticated=False
        )

        if not isinstance(data, dict):
            raise AuraAuthError("Login response was not valid JSON")
        if data.get("error"):
            raise AuraAuthError("Login failed")

        try:
            user = data["result"]["current_user"]
            self.user_id = str(user["id"])
            self.auth_token = str(user["auth_token"])
        except (KeyError, TypeError) as err:
            raise AuraAuthError("Login response did not contain credentials") from err
        return user

    async def get_frames(self) -> list[dict[str, Any]]:
        """Return all frames for the authenticated user."""
        data = await self._request(
            "GET",
            "/frames.json",
            params={
                "eligible_promo": "2022-12-14",
                "include_shared_albums": "1",
            },
        )
        if not isinstance(data, dict):
            raise AuraApiError("Frames response was not valid JSON")
        frames = data.get("frames", [])
        if not isinstance(frames, list):
            raise AuraApiError("Frames response did not contain a frame list")
        return frames

    async def get_frame(
        self,
        frame_id: str,
        *,
        include_recent_assets: bool = True,
        ensure_schedule: bool = True,
    ) -> dict[str, Any]:
        """Return detailed information for a single frame.

        Set ``ensure_schedule`` to False when the caller does not read the
        schedule fields. It skips the second request the workaround below
        would otherwise make on every call.
        """
        params = {}
        if include_recent_assets:
            params["include_recent_assets"] = "1"
        data = await self._request(
            "GET",
            f"/frames/{frame_id}.json",
            params=params or None,
        )
        if not isinstance(data, dict):
            raise AuraApiError("Frame response was not valid JSON")
        frame = self._extract_frame(data)
        # Some Aura API versions omit schedule fields on the single-frame
        # endpoint, but include them in the normal frame list response.
        if ensure_schedule and not isinstance(
            frame.get("scheduled_display_on_at"), str
        ):
            frames = await self.get_frames()
            list_frame = next(
                (item for item in frames if str(item.get("id")) == frame_id),
                None,
            )
            if list_frame is not None:
                frame = {**frame, **list_frame}
        return frame

    @staticmethod
    def _extract_frame(data: dict[str, Any]) -> dict[str, Any]:
        """Extract a frame from the response shapes used by Aura API versions."""
        if isinstance(data.get("frame"), dict):
            return data["frame"]
        result = data.get("result")
        if isinstance(result, dict):
            if isinstance(result.get("frame"), dict):
                return result["frame"]
            return result
        frames = data.get("frames")
        if isinstance(frames, list) and len(frames) == 1:
            frame = frames[0]
            if isinstance(frame, dict):
                return frame
        return data

    async def update_frame(
        self, frame_id: str, frame_fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Update frame settings."""
        payload = {
            "frame": {
                **frame_fields,
                "updated_at_on_client": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        }
        response = await self._request(
            "PUT", f"/frames/{frame_id}.json", json=payload
        )
        if isinstance(response, dict):
            updated_frame = response.get("frame", response)
            if isinstance(updated_frame, dict):
                _LOGGER.debug(
                    "Aura schedule update accepted for %s: sleep=%s, on=%s, off=%s",
                    frame_id,
                    updated_frame.get("scheduled_display_sleep"),
                    updated_frame.get("scheduled_display_on_at"),
                    updated_frame.get("scheduled_display_off_at"),
                )
        return response

    async def next_photo(self, frame_id: str) -> Any:
        """Show the next photo on the frame."""
        return await self._request(
            "POST", f"/frames/{frame_id}/next.json", json={}
        )

    async def previous_photo(self, frame_id: str) -> Any:
        """Show the previous photo on the frame."""
        return await self._request(
            "POST", f"/frames/{frame_id}/back.json", json={}
        )
