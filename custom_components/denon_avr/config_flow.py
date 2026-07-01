"""Config flow for the Denon AVR integration.

The user only provides the receiver's IP address. The flow validates it by
fetching the Deviceinfo document, then uses the discovered MAC address as the
unique id and the discovered model name as the entry title. Nothing about the
device is entered by hand.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .avr import DenonAvrDevice
from .avr.profile import load_profile
from .const import CONF_HOST, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class DenonAvrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Denon AVR."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step where the user enters the IP address."""

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            session = async_get_clientsession(self.hass)
            # Warm the profile cache off the loop before constructing the device.
            await self.hass.async_add_executor_job(load_profile)
            device = DenonAvrDevice(session, host)
            try:
                discovery = await device.async_discover()
            except ConnectionError:
                errors["base"] = "cannot_connect"
            else:
                mac = discovery.device.mac_address
                if mac:
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                else:
                    # Fall back to the host when the receiver reports no MAC.
                    self._async_abort_entries_match({CONF_HOST: host})

                title = discovery.device.model_name or "Denon AVR"
                return self.async_create_entry(title=title, data={CONF_HOST: host})

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )
