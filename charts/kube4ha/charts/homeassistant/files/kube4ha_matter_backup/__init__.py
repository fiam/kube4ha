"""Coordinate the chart-managed Matter Server with Home Assistant."""
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import HomeAssistant, callback

from .timezone import TimezoneBridge


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if os.environ.get("KUBE4HA_MATTER_TIME_SYNC_ENABLED") == "true":
        bridge = TimezoneBridge(lambda: hass.config.time_zone)

        @callback
        def config_updated(event):
            bridge.config_changed()

        entry.async_on_unload(hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, config_updated))
        entry.async_create_background_task(hass, bridge.run(), "Matter timezone bridge")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # ConfigEntry cancels its background tasks and removes event listeners.
    return True
