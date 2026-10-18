"""Base entity for Boiler Demand."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .controller import BoilerDemandController


class BoilerDemandEntity(Entity):
    """Entity bound to one controller; updates on controller signal."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: BoilerDemandController, key: str) -> None:
        self.controller = controller
        entry = controller.entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Boiler Demand",
            model="Valve-based boiler control",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.controller.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
