import logging
import asyncio
import collections
import requests
from lxml import html
import json

import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from homeassistant.helpers import aiohttp_client

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'grohe_sense'

CONF_USERNAME = 'username'
CONF_PASSWORD = 'password'

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema({
            vol.Required(CONF_USERNAME): cv.string,
            vol.Required(CONF_PASSWORD): cv.string,
        }),
    },
    extra=vol.ALLOW_EXTRA,
)

BASE_URL = 'https://idp2-apigw.cloud.grohe.com/v3/iot/'

GROHE_SENSE_TYPE = 101 # Type identifier for the battery powered water detector
GROHE_SENSE_GUARD_TYPE = 103 # Type identifier for sense guard, the water guard installed on your water pipe

GroheDevice = collections.namedtuple('GroheDevice', ['locationId', 'roomId', 'applianceId', 'type', 'name'])

async def get_token(session, username, password):
    try:
        response = await session.get(BASE_URL + 'oidc/login')
    except Exception as e:
        _LOGGER.error('Get Refresh Token Exception %s', str(e))
    else:
        cookie = response.cookies
        tree = html.fromstring(await response.text())

        name = tree.xpath("//html/body/div/div/div/div/div/div/div/form")
        action = name[0].action

        payload = {
            'username': username,
            'password': password,
            'Content-Type': 'application/x-www-form-urlencoded',
            'origin': BASE_URL,
            'referer': BASE_URL + 'oidc/login',
            'X-Requested-With': 'XMLHttpRequest',
        }
        try:
            response = await session.post(url = action, data = payload, cookies = cookie, allow_redirects=False)
        except Exception as e:
            _LOGGER.error('Get Refresh Token Action Exception %s', str(e))
        else:
            ondus_url = response.headers['Location'].replace('ondus', 'https')
            try:
                response = await session.get(url = ondus_url, cookies = cookie)
            except Exception as e:
                _LOGGER.error('Get Refresh Token Response Exception %s', str(e))
            else:
                response_json = json.loads(await response.text())

    return response_json['refresh_token']

async def async_setup(hass, config):
    _LOGGER.debug("Loading Grohe Sense")

    await initialize_shared_objects(hass, config.get(DOMAIN).get(CONF_USERNAME), config.get(DOMAIN).get(CONF_PASSWORD))

    await hass.helpers.discovery.async_load_platform('sensor', DOMAIN, {}, config)
    await hass.helpers.discovery.async_load_platform('switch', DOMAIN, {}, config)
    return True

async def initialize_shared_objects(hass, username, password):
    session = aiohttp_client.async_get_clientsession(hass)
    auth_session = OauthSession(session, username, password, await get_token(session, username, password))
    devices = []

    hass.data[DOMAIN] = { 'session': auth_session, 'devices': devices }

    locations = await auth_session.get(BASE_URL + f'locations')
    for location in locations:
        _LOGGER.debug('Found location %s', location)
        locationId = location['id']
        rooms = await auth_session.get(BASE_URL + f'locations/{locationId}/rooms')
        for room in rooms:
            _LOGGER.debug('Found room %s', room)
            roomId = room['id']
            appliances = await auth_session.get(BASE_URL + f'locations/{locationId}/rooms/{roomId}/appliances')
            for appliance in appliances:
                _LOGGER.debug('Found appliance %s', appliance)
                applianceId = appliance['appliance_id']
                devices.append(GroheDevice(locationId, roomId, applianceId, appliance['type'], appliance['name']))

class OauthException(Exception):
    def __init__(self, error_code, reason):
        self.error_code = error_code
        self.reason = reason

class OauthSession:
    def __init__(self, session, username, password, refresh_token):
        self._session = session
        self._username = username
        self._password = password
        self._refresh_token = refresh_token
        self._access_token = None
        self._fetching_new_token = None

    @property
    def session(self):
        return self._session

    async def token(self, old_token=None):
        """ Returns an authorization header. If one is supplied as old_token, invalidate that one """
        if self._access_token not in (None, old_token):
            return self._access_token

        if self._fetching_new_token is not None:
            await self._fetching_new_token.wait()
            return self._access_token

        self._access_token = None
        self._fetching_new_token = asyncio.Event()
        data = { 'refresh_token': self._refresh_token }
        headers = { 'Content-Type': 'application/json' }

        refresh_response = await self._http_request(BASE_URL + 'oidc/refresh', 'post', headers=headers, json=data)
        if not 'access_token' in refresh_response:
            _LOGGER.error('OAuth token refresh did not yield access token! Got back %s', refresh_response)
        else:
            self._access_token = 'Bearer ' + refresh_response['access_token']

        self._fetching_new_token.set()
        self._fetching_new_token = None
        return self._access_token

    async def get(self, url, **kwargs):
        return await self._http_request(url, auth_token=self, **kwargs)

    async def post(self, url, json, **kwargs):
        return await self._http_request(url, method='post', auth_token=self, json=json, **kwargs)

    async def _http_request(self, url, method='get', auth_token=None, headers={}, **kwargs):
        _LOGGER.debug('Making http %s request to %s, headers %s', method, url, headers)
        headers = headers.copy()
        tries = 0
        while True:
            if auth_token != None:
                # Cache token so we know which token was used for this request,
                # so we know if we need to invalidate.
                token = await auth_token.token()
                headers['Authorization'] = token
            try:
                async with self._session.request(method, url, headers=headers, **kwargs) as response:
                    _LOGGER.debug('Http %s request to %s got response %d', method, url, response.status)
                    if response.status in (200, 201):
                        return await response.json()
                    elif response.status == 401:
                        if auth_token != None:
                            _LOGGER.debug('Request to %s returned status %d, refreshing auth token', url, response.status)
                            token = await auth_token.token(token)
                        else:
                            _LOGGER.error('Grohe sense refresh token is invalid (or expired), please update your configuration with a new refresh token')
                            self._refresh_token = get_token(self._session, self._username, self._password)
                            token = await self.token(token)
                    else:
                        _LOGGER.debug('Request to %s returned status %d, %s', url, response.status, await response.text())
            except OauthException as oe:
                raise
            except Exception as e:
                _LOGGER.debug('Exception for http %s request to %s: %s', method, url, e)

            tries += 1
            await asyncio.sleep(min(600, 2**tries))

