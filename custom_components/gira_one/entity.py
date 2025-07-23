"""Base entity for the Gira One integration."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .api import GiraApiClient
from .const import DOMAIN
from . import SIGNAL_DATA_UPDATE

_LOGGER = logging.getLogger(__name__)


class GiraOneEntity(Entity, ABC):
    """Base class for Gira One entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
            self,
            config_entry: ConfigEntry,
            api_client: GiraApiClient,
            function_data: Dict[str, Any],
    ) -> None:
        """Initialize the base Gira One entity."""
        self._config_entry_id = config_entry.entry_id
        self._api = api_client
        self._function_data = function_data

        self._attr_unique_id = function_data["uid"]
        self._attr_name = function_data.get(
            "displayName", f"Gira Entity {self._attr_unique_id}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer="Gira",
            model=function_data.get("functionType", "Unknown Gira Function"),
            model_id=function_data.get("uid"),
            via_device=(
                DOMAIN,
                config_entry.unique_id or config_entry.data.get("host"),
            ),
        )

        self._data_points: Dict[str, Dict[str, Any]] = {
            dp["name"]: dp for dp in function_data.get("dataPoints", [])
        }

    def _get_dp_uid(self, dp_name: str) -> Optional[str]:
        """Get the UID for a data point by its name."""
        return self._data_points.get(dp_name, {}).get("uid")

    def _has_dp(self, dp_name: str) -> bool:
        """Check if a data point exists."""
        return dp_name in self._data_points

    def _can_write_dp(self, dp_name: str) -> bool:
        """Check if a data point is writable."""
        return self._data_points.get(dp_name, {}).get("canWrite", False)

    async def _send_command(self, dp_name: str, value: Any) -> None:
        """Helper to send a command to a data point."""
        dp_uid = self._get_dp_uid(dp_name)
        if not dp_uid:
            _LOGGER.error("Entity %s: Data point '%s' not found.", self.entity_id, dp_name)
            return

        _LOGGER.debug(
            "Entity %s: Sending to DP '%s' (UID %s) value '%s'",
            self.entity_id, dp_name, dp_uid, value
        )
        try:
            await self._api.set_value(dp_uid, value)
        except Exception as e:
            _LOGGER.error(
                "Entity %s: Error sending command to DP '%s': %s", self.entity_id, dp_name, e
            )

    async def async_added_to_hass(self) -> None:
        """Register for callbacks and fetch initial state."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DATA_UPDATE}_{self._config_entry_id}",
                self._handle_value_update,
            )
        )
        await self._fetch_initial_state()

    @callback
    def _handle_value_update(self, dp_uid: str, value: Any) -> None:
        """Handle a value update from the dispatcher."""
        if self._update_state_from_dp_value(dp_uid, value):
            self.async_write_ha_state()

    @abstractmethod
    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the entity's data points."""
        raise NotImplementedError

    @abstractmethod
    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """
        Update internal state from a single data point update.
        Must be implemented by subclasses.
        Returns True if the state changed.
        """
        raise NotImplementedError