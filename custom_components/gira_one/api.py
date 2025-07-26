"""API client for Gira IoT REST API."""

import logging
from collections.abc import Callable
from typing import Any

import aiohttp
import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_VERSION

_LOGGER = logging.getLogger(__name__)


class GiraApiClientError(Exception):
    """Generic API client error."""


class GiraApiAuthError(GiraApiClientError):
    """Authentication error."""


class GiraApiConnectionError(GiraApiClientError):
    """Connection error."""


class GiraApiRequestError(GiraApiClientError):
    """Invalid request or unexpected response."""


class GiraApiClient:
    """Gira IoT REST API Client."""

    def __init__(
        self,
        host: str,
        username: str | None,
        password: str | None,
        hass: HomeAssistant,
        auth_error_callback: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the API client."""
        self._host = host
        self._username = username
        self._password = password
        self._session = async_get_clientsession(
            hass, verify_ssl=False
        )  # Gira IoT API uses self-signed certs
        self._base_url = f"https://{self._host}/api"
        self._token: str | None = None
        self._client_id: str | None = None
        self._auth_error_callback = auth_error_callback

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        requires_auth: bool = True,
        is_registration: bool = False,
        token_in_path: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """Make a generic API request."""
        url = f"{self._base_url}{path}"
        headers = {"Accept": "application/json"}
        auth = None

        if requires_auth:
            if is_registration:
                if not self._username or not self._password:
                    msg = "Username or password missing for registration."
                    raise GiraApiAuthError(msg)
                auth = aiohttp.BasicAuth(self._username, self._password)
            elif self._token and not token_in_path:
                if params is None:
                    params = {}
                params["token"] = self._token
            elif not self._token:
                msg = f"Token missing for authenticated request to {path}"
                raise GiraApiAuthError(msg)

        _LOGGER.debug(
            "Request: %s %s (params=%s, json=%s)", method, url, params, json_data
        )

        try:
            async with async_timeout.timeout(15):
                response = await self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    auth=auth,
                )
        except aiohttp.ClientError as err:
            _LOGGER.exception("Request failed for %s %s: %s", method, url, err)
            msg = f"Request failed: {err}"
            raise GiraApiConnectionError(msg) from err
        except TimeoutError:
            _LOGGER.exception("Request timed out for %s %s", method, url)
            msg = f"Request timed out: {url}"
            raise GiraApiConnectionError(msg)

        status_code = response.status
        _LOGGER.debug("Response status from %s %s: %s", method, url, status_code)

        try:
            response_data = await response.json() if response.content else {}
        except aiohttp.ContentTypeError:
            response_text = await response.text()
            _LOGGER.warning("Non-JSON response from %s: %s", url, response_text)
            response_data = {"error_text": response_text}

        if not (200 <= status_code < 300):
            error_info = response_data.get("error", {})
            error_code = error_info.get("code", "unknown_api_error")
            error_message = error_info.get("message", "Unknown API error")
            _LOGGER.error(
                "API request %s %s failed with status %s: [%s] %s",
                method,
                url,
                status_code,
                error_code,
                error_message,
            )
            if status_code in [401, 403]:
                # If we have a callback for auth errors, trigger it.
                if self._auth_error_callback:
                    self._auth_error_callback()
                msg = f"Authentication failed: {error_message} (Code: {error_code})"
                raise GiraApiAuthError(msg)
            if status_code == 423:
                msg = f"Device locked: {error_message} (Code: {error_code})"
                raise GiraApiRequestError(msg)
            msg = f"API Error {status_code}: [{error_code}] {error_message}"
            raise GiraApiRequestError(msg)

        return status_code, response_data

    def disable_auth_error_callback(self) -> None:
        """Disable the auth error callback to prevent reload loops during cleanup."""
        _LOGGER.debug("Disabling auth error callback.")
        self._auth_error_callback = None

    async def check_api_availability(self) -> dict[str, Any]:
        """Check if the Gira IoT REST API is available."""
        status, data = await self._request(
            "GET", f"/{API_VERSION}/", requires_auth=False
        )
        if status == 200 and data.get("info") == "GDS-REST-API":
            return data
        msg = "API availability check failed."
        raise GiraApiConnectionError(msg)

    async def register_client(self, client_id: str) -> str:
        """Register a client or refresh its token using username/password."""
        self._client_id = client_id
        payload = {"client": self._client_id}
        _LOGGER.info("Registering/refreshing client with ID: %s", self._client_id)
        status, data = await self._request(
            "POST",
            f"/{API_VERSION}/clients",
            json_data=payload,
            is_registration=True,
        )
        if status == 201 and "token" in data:
            self._token = data["token"]
            _LOGGER.info("Client token acquired successfully.")
            return self._token
        msg = "Client registration/token refresh failed."
        raise GiraApiRequestError(msg)

    async def unregister_client(self) -> None:
        """Unregister this client from the Gira device."""
        if not self._token:
            _LOGGER.warning("No token available, cannot unregister client.")
            return
        _LOGGER.info("Unregistering client.")
        try:
            status, _ = await self._request(
                "DELETE",
                f"/{API_VERSION}/clients/{self._token}",
                token_in_path=True,
            )
            if status == 204:
                _LOGGER.info("Client unregistered successfully.")
                self._token = None
        except GiraApiAuthError:
            _LOGGER.warning("Failed to unregister: token was already invalid.")
            self._token = None
        except GiraApiRequestError as e:
            _LOGGER.exception("Failed to unregister client: %s", e)

    async def get_server_details(self) -> dict[str, Any]:
        """Get the server details from info endpoint."""
        status, data = await self._request("GET", f"/{API_VERSION}")
        return data

    async def get_ui_config(self) -> dict[str, Any]:
        """Get the UI configuration."""
        params = {"expand": "dataPointFlags,locations,trades"}
        status, data = await self._request(
            "GET", f"/{API_VERSION}/uiconfig", params=params
        )
        return data

    async def get_value(self, uid: str) -> dict[str, Any]:
        """Get value(s) for a specific UID (datapoint or function)."""
        status, data = await self._request("GET", f"/{API_VERSION}/values/{uid}")
        return data

    async def set_value(self, uid: str, value: Any) -> bool:
        """Set a single value for a data point UID."""
        payload = {"value": value}
        status, _ = await self._request(
            "PUT", f"/{API_VERSION}/values/{uid}", json_data=payload
        )
        return status == 200

    async def set_multiple_values(self, values_payload: list[dict[str, Any]]) -> bool:
        """Set multiple values."""
        payload = {"values": values_payload}
        status, _ = await self._request(
            "PUT", f"/{API_VERSION}/values", json_data=payload
        )
        return status == 200

    async def register_callbacks(
        self, service_callback_url: str, value_callback_url: str
    ) -> bool:
        """Register callback URLs with the Gira device."""
        payload = {
            "serviceCallback": service_callback_url,
            "valueCallback": value_callback_url,
            "testCallbacks": False,
        }
        status, _ = await self._request(
            "POST",
            f"/{API_VERSION}/clients/{self._token}/callbacks",
            json_data=payload,
            token_in_path=True,
        )
        return status == 200

    async def remove_callbacks(self) -> bool:
        """Remove callback URLs from the Gira device."""
        if not self._token:
            _LOGGER.warning("No token, cannot remove callbacks.")
            return False
        try:
            status, _ = await self._request(
                "DELETE",
                f"/{API_VERSION}/clients/{self._token}/callbacks",
                token_in_path=True,
            )
            return status == 200
        except GiraApiClientError as e:
            _LOGGER.exception("Failed to remove callbacks during unload: %s", e)
            return False

    @property
    def token(self) -> str | None:
        """Return the current access token."""
        return self._token

    @property
    def client_id(self) -> str | None:
        """Return the current client ID."""
        return self._client_id

    def set_credentials(self, token: str, client_id: str) -> None:
        """Set the token and client_id for an existing client."""
        self._token = token
        self._client_id = client_id