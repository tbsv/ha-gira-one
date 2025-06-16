"""API client for Gira IoT REST API."""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import aiohttp
import async_timeout

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_VERSION, DEFAULT_SKIP_CERT_VERIFY

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
        username: Optional[str],
        password: Optional[str],
        hass: HomeAssistant,
        skip_cert_verify: bool = DEFAULT_SKIP_CERT_VERIFY,
    ) -> None:
        """Initialize the API client."""
        self._host = host
        self._username = username
        self._password = password
        self._session = async_get_clientsession(hass, verify_ssl=not skip_cert_verify)
        self._base_url = f"https://{self._host}/api"
        self._token: Optional[str] = None
        self._client_id: Optional[str] = None  # Will be set during registration
        # The Gira API requires certificate checking to be skipped
        # This is handled by verify_ssl in async_get_clientsession

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        requires_auth: bool = True,
        is_registration: bool = False,
    ) -> Tuple[int, Dict[str, Any]]:
        """Make an API request."""
        url = f"{self._base_url}{path}"
        headers = {"Accept": "application/json"}
        auth = None

        if requires_auth:
            if is_registration:
                if not self._username or not self._password:
                    _LOGGER.error("Username or password not set for registration")
                    raise GiraApiAuthError(
                        "Username or password missing for registration."
                    )
                auth = aiohttp.BasicAuth(self._username, self._password)
                _LOGGER.debug("Using BasicAuth for registration")
            elif self._token:
                if params is None:
                    params = {}
                params["token"] = self._token  # Token as query parameter
                _LOGGER.debug("Using token query parameter for authentication")
            else:
                _LOGGER.error(
                    "Token not available for authenticated request to %s", path
                )
                raise GiraApiAuthError(f"Token missing for {path}")

        _LOGGER.debug(
            "Request: %s %s (params=%s, json=%s)", method, url, params, json_data
        )

        try:
            async with async_timeout.timeout(
                15
            ):  # Increased timeout for potential slow devices
                response = await self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    auth=auth,
                    # ssl=False if self._skip_cert_verify else None # handled by session
                )
        except aiohttp.ClientConnectorCertificateError as err:
            _LOGGER.error(
                "SSL Certificate Verification failed for %s: %s. Ensure Gira device uses a valid cert or disable verification if intended for client talking to Gira.",
                url,
                err,
            )
            # This error primarily applies when HA acts as a client to Gira.
            # The Gira docs say their server might not have a trusted cert
            raise GiraApiConnectionError(f"SSL certificate error: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Request failed for %s: %s", url, err)
            raise GiraApiConnectionError(f"Request failed: {err}") from err
        except asyncio.TimeoutError:
            _LOGGER.error("Request timed out for %s", url)
            raise GiraApiConnectionError(f"Request timed out: {url}")

        status_code = response.status
        _LOGGER.debug("Response status from %s %s: %s", method, url, status_code)

        try:
            response_data = (
                await response.json()
                if response.content_length and response.content_length > 0
                else {}
            )
        except aiohttp.ContentTypeError:
            # Handle cases like 204 No Content
            if status_code == 204:
                response_data = {}
            else:
                _LOGGER.warning(
                    "Non-JSON response from %s: %s", url, await response.text()
                )
                response_data = {"error_text": await response.text()}

        _LOGGER.debug("Response data: %s", response_data)

        if not (200 <= status_code < 300):
            error_info = response_data.get("error", {})
            error_code = error_info.get("code", "unknown_api_error")
            error_message = error_info.get("message", "Unknown API error")
            _LOGGER.error(
                "API request to %s failed with status %s: [%s] %s. Response: %s",
                url,
                status_code,
                error_code,
                error_message,
                response_data,
            )
            if status_code == 401:  # Unauthorized
                if error_code == "invalidAuth":  # Missing or invalid authentication.
                    raise GiraApiAuthError(
                        f"Authentication failed: {error_message} (Code: {error_code})"
                    )
                elif (
                    error_code == "invalidToken" and not is_registration
                ):  # Missing or invalid token as URL parameter.
                    # Token might have been invalidated on the server
                    self._token = None  # Clear stale token
                    raise GiraApiAuthError(
                        f"Invalid token: {error_message} (Code: {error_code})"
                    )
            elif status_code == 423:  # Locked
                raise GiraApiRequestError(
                    f"Device locked: {error_message} (Code: {error_code}, Status: {status_code})"
                )
            raise GiraApiRequestError(
                f"API Error {status_code} for {url}: [{error_code}] {error_message}"
            )

        return status_code, response_data

    async def check_api_availability(self) -> Dict[str, Any]:
        """Check if the Gira IoT REST API is available. Uses /api/{API_VERSION}/ endpoint."""
        status, data = await self._request(
            "GET", f"/{API_VERSION}/", requires_auth=False
        )
        if status == 200 and data.get("info") == "GDS-REST-API":  #
            _LOGGER.info("Gira API is available: %s", data)
            return data
        raise GiraApiConnectionError(
            f"API availability check failed. Status: {status}, Data: {data}"
        )

    async def register_client(self, client_id: str) -> str:
        """Register this client with the Gira device."""
        self._client_id = client_id
        payload = {"client": self._client_id}  #
        _LOGGER.info("Registering client with ID: %s", self._client_id)
        try:
            # Use Basic Auth for registration
            status, data = await self._request(
                "POST",
                f"/{API_VERSION}/clients",
                json_data=payload,
                requires_auth=True,
                is_registration=True,
            )
        except GiraApiAuthError as e:  #
            _LOGGER.error(
                "Client registration failed due to authentication error: %s", e
            )
            raise
        except GiraApiRequestError as e:  #
            _LOGGER.error("Client registration failed: %s", e)
            raise

        if status == 201 and "token" in data:  #
            self._token = data["token"]
            _LOGGER.info("Client registered successfully. Token received.")
            return self._token
        _LOGGER.error(
            "Failed to register client or get token. Status: %s, Data: %s", status, data
        )
        raise GiraApiRequestError(
            f"Client registration failed. Status: {status}, Response: {data}"
        )

    async def unregister_client(self) -> None:
        """Unregister this client from the Gira device."""
        if not self._token:
            _LOGGER.warning("No token available, cannot unregister client.")
            return
        _LOGGER.info("Unregistering client.")
        try:
            # DELETE /api/clients/<access token>
            # The token for this call is part of the path, not a query param
            # However, our _request method adds token to params if requires_auth and not is_registration.
            # For DELETE, the token is in the path. Modify _request or handle specially.
            # For now, let's try to make it work with _request.
            # The API doc actually has an error in the example for license GET:
            # Authorization: Bearer <token> but then states token is a query param.
            # For DELETE /api/clients/<token>, the token is a path param.
            # Let's adjust.

            url = f"{self._base_url}/{API_VERSION}/clients/{self._token}"
            headers = {"Accept": "application/json"}
            # No token needed in query for this specific call as it's in the path
            async with async_timeout.timeout(10):
                response = await self._session.delete(
                    url, headers=headers
                )  # No params, no json_data

            if response.status == 204:  # No Content on success
                _LOGGER.info("Client unregistered successfully.")
                self._token = None
            elif response.status == 401:  # invalidToken
                _LOGGER.warning(
                    "Failed to unregister client: Invalid token (already unregistered?)."
                )
                self._token = None  # Assume token is no longer valid
            else:
                error_text = await response.text()
                _LOGGER.error(
                    "Failed to unregister client. Status: %s, Response: %s",
                    response.status,
                    error_text,
                )
                # Try to parse JSON error if possible
                try:
                    error_json = await response.json()
                    error_info = error_json.get("error", {})
                    error_code = error_info.get("code", "unknown")
                    error_message = error_info.get("message", error_text)
                    raise GiraApiRequestError(
                        f"Client unregistration failed. Status: {response.status}, Code: {error_code}, Msg: {error_message}"
                    )
                except Exception:  # Not JSON or other parsing error
                    raise GiraApiRequestError(
                        f"Client unregistration failed. Status: {response.status}, Response: {error_text}"
                    )

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error during client unregistration request: %s", err)
            raise GiraApiConnectionError(
                f"Unregistration request failed: {err}"
            ) from err

    async def get_server_details(self) -> Dict[str, Any]:
        """Get the server details from info endpoint."""
        if not self._token:
            raise GiraApiAuthError("Token not available for get_server_details.")
        status, data = await self._request(
            "GET", f"/{API_VERSION}"
        )
        if status == 200:
            return data
        raise GiraApiRequestError(
            f"Failed to get server details. Status: {status}, Data: {data}"
        )

    async def get_ui_config(self) -> Dict[str, Any]:
        """Get the UI configuration."""
        if not self._token:
            raise GiraApiAuthError("Token not available for get_ui_config.")
        # expand to get all necessary details
        params = {"expand": "dataPointFlags,locations,trades"}
        status, data = await self._request(
            "GET", f"/{API_VERSION}/uiconfig", params=params
        )
        if status == 200:
            return data
        raise GiraApiRequestError(
            f"Failed to get UI config. Status: {status}, Data: {data}"
        )

    async def get_value(self, uid: str) -> Dict[str, Any]:
        """Get value(s) for a specific UID (datapoint or function)."""
        if not self._token:
            raise GiraApiAuthError("Token not available for get_value.")
        status, data = await self._request("GET", f"/{API_VERSION}/values/{uid}")
        # Response format: {"values": [{"uid": "<uid>", "value": "<val>"}]}
        if status == 200 and "values" in data:
            return data
        raise GiraApiRequestError(
            f"Failed to get value for UID {uid}. Status: {status}, Data: {data}"
        )

    async def set_value(self, uid: str, value: Any) -> bool:
        """Set a single value for a data point UID."""
        if not self._token:
            raise GiraApiAuthError("Token not available for set_value.")
        payload = {"value": value}  #
        # PUT /api/values/<uid>
        status, _ = await self._request(
            "PUT", f"/{API_VERSION}/values/{uid}", json_data=payload
        )
        return status == 200  # PUT returns 200 OK on success

    async def set_multiple_values(self, values_payload: list[dict[str, Any]]) -> bool:
        """Set multiple values. Payload: [{"uid": "...", "value": ...}]"""
        if not self._token:
            raise GiraApiAuthError("Token not available for set_multiple_values.")
        payload = {"values": values_payload}  #
        # PUT /api/values
        status, _ = await self._request(
            "PUT", f"/{API_VERSION}/values", json_data=payload
        )
        return status == 200  # PUT returns 200 OK on success

    async def register_callbacks(
        self, service_callback_url: str, value_callback_url: str
    ) -> bool:
        """Register callback URLs with the Gira device."""
        if not self._token:
            raise GiraApiAuthError("Token not available for register_callbacks.")
        payload = {
            "serviceCallback": service_callback_url,  #
            "valueCallback": value_callback_url,  #
            "testCallbacks": False,
        }
        # POST /api/clients/<access token>/callbacks
        status, data = await self._request(
            "POST",
            f"/{API_VERSION}/clients/{self._token}/callbacks",
            json_data=payload,
            requires_auth=False,
        )  # Token is in path
        if status == 200:  #
            _LOGGER.info("Callbacks registered successfully. Response: %s", data)
            return True
        # Handle specific errors
        # 400 Bad Request - missingContent or callbackTestFailed
        # 422 Unprocessable - Callbacks not HTTPS
        error_info = data.get("error", {})
        error_code = error_info.get("code", "unknown")
        error_message = error_info.get("message", "Failed to register callbacks")
        if error_code == "callbackTestFailed":
            _LOGGER.error(
                "Callback registration failed: Gira API could not reach or validate callback URLs. %s",
                error_message,
            )
        elif error_code == "unprocessable" and "HTTPS" in error_message:
            _LOGGER.error(
                "Callback registration failed: Callback URLs must use HTTPS. %s",
                error_message,
            )
        else:
            _LOGGER.error(
                "Failed to register callbacks. Status: %s, Code: %s, Message: %s",
                status,
                error_code,
                error_message,
            )
        raise GiraApiRequestError(
            f"Callback registration failed: {error_message} (Status: {status}, Code: {error_code})"
        )

    async def remove_callbacks(self) -> bool:
        """Remove callback URLs from the Gira device."""
        if not self._token:
            _LOGGER.warning("No token, cannot remove callbacks.")
            return False  # Or raise error? For unload, perhaps just log.

        # DELETE /api/clients/<access token>/callbacks
        status, data = await self._request(
            "DELETE",
            f"/{API_VERSION}/clients/{self._token}/callbacks",
            requires_auth=False,
        )  # Token is in path
        if status == 200:  #
            _LOGGER.info("Callbacks removed successfully.")
            return True
        error_info = data.get("error", {})
        error_code = error_info.get("code", "unknown")
        error_message = error_info.get("message", "Failed to remove callbacks")
        _LOGGER.error(
            "Failed to remove callbacks. Status: %s, Code: %s, Message: %s",
            status,
            error_code,
            error_message,
        )
        # Don't raise on unload typically, just log
        return False

    def set_credentials(self, client_id: str, token: str):
        """Sets client_id and token, e.g. from existing config."""
        self._client_id = client_id
        self._token = token

    @property
    def token(self) -> Optional[str]:
        """Return the current access token."""
        return self._token

    @property
    def client_id(self) -> Optional[str]:
        """Return the current client ID."""
        return self._client_id
