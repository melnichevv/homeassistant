"""Support for Unified Remote remotes."""
from collections import defaultdict
import hashlib
import json
import logging
import uuid

import voluptuous as vol

from homeassistant.components.remote import (
    DOMAIN as COMPONENT,
    PLATFORM_SCHEMA,
    RemoteDevice,
)
from homeassistant.const import (
    CONF_ENTITY_ID,
    CONF_HOST,
    CONF_ID,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.util import slugify

from .const import CONF_RUN, DOMAIN, SERVICE_TRIGGER_COMMAND

_LOGGER = logging.getLogger(__name__)

DEFAULT_LEARNING_TIMEOUT = 20
DEFAULT_NAME = "UnifiedRemote"
DEFAULT_PORT = 9510
DEFAULT_RETRY = 3
DEFAULT_TIMEOUT = 5
REMOTES = []


PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Required(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
    }
)


SERVICE_TRIGGER_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id,
        vol.Required(CONF_ID): cv.string,
        vol.Required(CONF_RUN): cv.string,
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Broadlink remote."""
    host = f"{config[CONF_HOST]}:{config[CONF_PORT]}"
    name = config[CONF_NAME]
    unique_id = f"unified_remote_{slugify(name)}"

    if unique_id in hass.data.setdefault(DOMAIN, {}).setdefault(COMPONENT, []):
        _LOGGER.error("Duplicate: %s", unique_id)
        return
    hass.data[DOMAIN][COMPONENT].append(unique_id)

    api = hass.helpers.aiohttp_client.async_get_clientsession()
    remote = UnifiedRemote(host, name, unique_id, api, username=config.get(CONF_USERNAME), password=config.get(CONF_PASSWORD))
    REMOTES.append(remote)
    async_add_entities([remote], False)
    register_services(hass)


def register_services(hass):
    """Register all services for unified remote devices."""
    hass.services.async_register(
        DOMAIN, SERVICE_TRIGGER_COMMAND, _trigger_command, schema=SERVICE_TRIGGER_COMMAND_SCHEMA
    )


async def _apply_service(service, service_func, *service_func_args):
    """Handle services to apply."""
    entity_ids = service.data.get("entity_id")

    if entity_ids:
        _devices = [device for device in REMOTES if device.entity_id in entity_ids]
    else:
        _devices = REMOTES

    for device in _devices:
        await service_func(device, **service.data)


async def _trigger_command(service):
    await _apply_service(service, UnifiedRemote.async_trigger_command)


class UnifiedRemote(RemoteDevice):
    """Representation of a UnifiedRemote remote."""

    def __init__(self, host, name, unique_id, api=None, username=None, password=None):
        """Initialize the remote."""
        self._host = host
        self._name = name
        self._unique_id = unique_id
        self._api = api
        self._username = username
        self._password = password
        self._codes = {}
        self._flags = defaultdict(int)
        self._state = True
        self._available = True

    @property
    def name(self):
        """Return the name of the remote."""
        return self._name

    @property
    def unique_id(self):
        """Return the unique ID of the remote."""
        return self._unique_id

    @property
    def is_on(self):
        """Return True if the remote is on."""
        return self._state

    @property
    def available(self):
        """Return True if the remote is available."""
        return self._available

    @callback
    def get_flags(self):
        """Return dictionary of toggle flags.

        A toggle flag indicates whether `self._async_send_code()`
        should send an alternative code for a key device.
        """
        return self._flags

    async def async_send_command(self, command, **kwargs):
        """Send a list of commands to a device."""

    async def async_trigger_command(self, **kwargs):
        """Send a list of commands to a device."""
        kwargs = SERVICE_TRIGGER_COMMAND_SCHEMA(kwargs)
        base_url = f"{self._host}/client"
        response = await self._api.get(f"{base_url}/connect")

        # establish connection
        response_json = await response.json()
        conn_id = response_json['id']

        headers = {
            'UR-Connection-ID': conn_id,
        }

        new_guid = str(uuid.uuid4())
        my_guid = str(uuid.uuid4())
        source_guid = "web-" + my_guid
        payload = {
            'Action': 0,
            'Request': 0,
            'Version': 10,
            'Password': new_guid,
            'Platform': 'web',
            'Source': source_guid
        }
        response = await self._api.post(f"{base_url}/request", headers=headers, data=json.dumps(payload))
        response_json = await response.json()
        payload = {
            "Capabilities": {
                "Actions": True,
                "Sync": True,
                "Grid": True,
                "Fast": False,
                "Loading": True,
                "Encryption2": True
            },
            "Action": 1,
            "Request": 1,
            "Source": source_guid
        }
        if self._password:
            password = response_json.get("Password")
            password_to_send = hashlib.sha256(str(password + self._password + new_guid).encode('utf-8')).hexdigest()
            if self._username:
                password_to_send = hashlib.sha256(str(password + self._username + ":" + self._password + new_guid).encode('utf-8')).hexdigest()

            payload["Password"] = password_to_send
        await self._api.post(f"{base_url}/request", headers=headers, data=json.dumps(payload))
        # trigger remote action
        payload = {
            "ID": f"Unified.{kwargs[CONF_ID]}",
            "Action": 7,
            "Request": 7,
            "Run": {
                "Name": kwargs[CONF_RUN]
            },
            "Source": source_guid
        }
        await self._api.post(f"{base_url}/request", headers=headers, data=json.dumps(payload))
