"""Config flow for the Denon AVR integration.

The user only provides the receiver's IP address. The flow validates it by
fetching the Deviceinfo document, then uses the discovered MAC address as the
unique id and the discovered model name as the entry title. Nothing about the
device is entered by hand.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .avr import DenonAvrDevice
from .avr.profile import load_profile
from .const import (
    CONF_HOST,
    CONF_SOUND_MODE_LEARNING,
    DEFAULT_SOUND_MODE_LEARNING,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class DenonAvrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Denon AVR."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for the "Configure" button."""

        return DenonAvrOptionsFlow()

    def __init__(self) -> None:
        self._host: str | None = None
        self._name: str = "Denon AVR"

    async def _async_identify(self, host: str) -> tuple[str | None, str]:
        """Fetch the receiver's MAC and model name. Raises ConnectionError."""

        session = async_get_clientsession(self.hass)
        # Warm the profile cache off the loop before constructing the device.
        await self.hass.async_add_executor_job(load_profile)
        device = DenonAvrDevice(session, host)
        identity = await device.async_fetch_identity()
        return identity.mac_address, identity.model_name or "Denon AVR"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step where the user enters the IP address."""

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                mac, name = await self._async_identify(host)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if mac:
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                else:
                    self._async_abort_entries_match({CONF_HOST: host})
                return self.async_create_entry(title=name, data={CONF_HOST: host})

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_ssdp(
        self, discovery_info: SsdpServiceInfo
    ) -> ConfigFlowResult:
        """Handle a receiver discovered over SSDP."""

        host = urlparse(discovery_info.ssdp_location or "").hostname
        if not host:
            return self.async_abort(reason="cannot_connect")
        try:
            mac, name = await self._async_identify(host)
        except ConnectionError:
            return self.async_abort(reason="cannot_connect")
        if mac:
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        else:
            self._async_abort_entries_match({CONF_HOST: host})
        self._host = host
        self._name = name
        # Show the friendly name in the discovered-device card and confirm step.
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_ssdp_confirm()

    async def async_step_ssdp_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding an SSDP discovered receiver."""

        if user_input is not None:
            return self.async_create_entry(
                title=self._name, data={CONF_HOST: self._host}
            )
        return self.async_show_form(
            step_id="ssdp_confirm",
            description_placeholders={"name": self._name},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the receiver's IP address without removing the integration."""

        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                mac, _name = await self._async_identify(host)
            except ConnectionError:
                errors["base"] = "cannot_connect"
            else:
                # Make sure the new address still points at the same receiver, so
                # a typo cannot silently rebind the entry to a different device.
                if mac:
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, {CONF_HOST: entry.data[CONF_HOST]}
            ),
            errors=errors,
        )


class DenonAvrOptionsFlow(OptionsFlow):
    """Options for a configured Denon AVR (the "Configure" dialog)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the integration options."""

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SOUND_MODE_LEARNING, DEFAULT_SOUND_MODE_LEARNING
        )
        schema = vol.Schema(
            {vol.Required(CONF_SOUND_MODE_LEARNING, default=current): bool}
        )
        return self.async_show_form(step_id="init", data_schema=schema)
