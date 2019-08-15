import logging
import json
import asyncio
from datetime import (datetime, timezone, timedelta)

from homeassistant.components.sensor import PLATFORM_SCHEMA
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from homeassistant.helpers.entity import Entity
from homeassistant.util import Throttle
from homeassistant.const import (STATE_UNAVAILABLE, STATE_UNKNOWN)

from homeassistant.helpers import aiohttp_client

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'grohe_sense'

CONF_REFRESH_TOKEN = 'refresh_token'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_REFRESH_TOKEN): cv.string,
})


BASE_URL = 'https://idp-apigw.cloud.grohe.com/v3/iot/'

GROHE_SENSE_TYPE = 101 # Type identifier for the battery powered water detector
GROHE_SENSE_GUARD_TYPE = 103 # Type identifier for sense guard, the water guard installed on your water pipe

SENSOR_TYPES = {
        GROHE_SENSE_TYPE: [ 'temperature', 'humidity'],
        GROHE_SENSE_GUARD_TYPE: [ 'flowrate', 'pressure', 'temperature_guard']
        }

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    import aiohttp
    _LOGGER.debug("Starting Grohe Sense sensor")

    session = aiohttp_client.async_get_clientsession(hass)
    auth_session = OauthSession(session, config.get(CONF_REFRESH_TOKEN))

    locations = await auth_session.get(BASE_URL + 'locations')
    entities = []

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
                reader = GroheSenseGuardReader(auth_session, locationId, roomId, applianceId, appliance['type'])
                if appliance['type'] in SENSOR_TYPES:
                    entities += [GroheSenseSensorEntity(reader, appliance['name'], key) for key in SENSOR_TYPES[appliance['type']]]
                if appliance['type'] == GROHE_SENSE_TYPE:
                    # Grohe sense, battery powered water detector
                    pass
                elif appliance['type'] == GROHE_SENSE_GUARD_TYPE:
                    # Grohe sense guard
                    entities.append(GroheSenseGuardWithdrawalsEntity(reader, appliance['name'], 1))
                    entities.append(GroheSenseGuardWithdrawalsEntity(reader, appliance['name'], 7))
                else:
                    _LOGGER.warning('Unrecognized appliance %s, ignoring.', appliance)
    if entities:
        async_add_entities(entities)

class OauthSession:
    def __init__(self, session, refresh_token):
        self._session = session
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
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 401 and auth_token != None:
                        _LOGGER.debug('Request to %s returned status %d, refreshing auth token', url, response.status)
                        token = await auth_token.token(token)
                    else:
                        _LOGGER.debug('Request to %s returned status %d', url, response.status)
            except Exception as e:
                _LOGGER.debug('Exception for http %s request to %s: %s', method, url, e)
            tries += 1
            await asyncio.sleep(min(600, 2**tries))


class GroheSenseGuardReader:
    def __init__(self, auth_session, locationId, roomId, applianceId, device_type):
        self._auth_session = auth_session
        self._locationId = locationId
        self._roomId = roomId
        self._applianceId = applianceId
        self._type = device_type

        self._withdrawals = []
        self._measurements = {}
        self._poll_from = datetime.now(tz=timezone.utc) - timedelta(7)
        self._fetching_data = None
        self._data_fetch_completed = datetime.min

    @property
    def applianceId(self):
        """ returns the appliance Identifier, looks like a UUID, so hopefully unique """
        return self._applianceId

    async def async_update(self):
        if self._fetching_data != None:
            await self._fetching_data.wait()
            return

        if datetime.now() - self._data_fetch_completed < timedelta(minutes=1):
            _LOGGER.debug('Skipping fetching new data, time since last fetch was only %s', datetime.now() - self._data_fetch_completed)
            return

        _LOGGER.debug("Fetching new data for appliance %s", self._applianceId)
        self._fetching_data = asyncio.Event()

        def parse_time(s):
            # XXX: Fix for python 3.6 - Grohe emits time zone as "+HH:MM", python 3.6's %z only accepts the format +HHMM
            # So, some ugly code to remove the colon for now...
            if s.rfind(':') > s.find('+'):
                s = s[:s.rfind(':')] + s[s.rfind(':')+1:]
            return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S.%f%z')

        poll_from=self._poll_from.strftime('%Y-%m-%d')
        measurements_response = await self._auth_session.get(BASE_URL + f'locations/{self._locationId}/rooms/{self._roomId}/appliances/{self._applianceId}/data?from={poll_from}')
        if 'withdrawals' in measurements_response['data']:
            withdrawals = measurements_response['data']['withdrawals']
            _LOGGER.debug('Received %d withdrawals in response', len(withdrawals))
            for w in withdrawals:
                w['starttime'] = parse_time(w['starttime'])
            withdrawals = [ w for w in withdrawals if w['starttime'] > self._poll_from]
            withdrawals.sort(key = lambda x: x['starttime'])

            _LOGGER.debug('Got %d new withdrawals totaling %f volume', len(withdrawals), sum((w['waterconsumption'] for w in withdrawals)))
            self._withdrawals += withdrawals
            if len(self._withdrawals) > 0:
                self._poll_from = max(self._poll_from, self._withdrawals[-1]['starttime'])
        elif self._type != GROHE_SENSE_TYPE:
            _LOGGER.info('Data response for appliance %s did not contain any withdrawals data', self._applianceId)

        if 'measurement' in measurements_response['data']:
            measurements = measurements_response['data']['measurement']
            measurements.sort(key = lambda x: x['timestamp'])
            if len(measurements):
                for key in SENSOR_TYPES[self._type]:
                    if key in measurements[-1]:
                        self._measurements[key] = measurements[-1][key]
                self._poll_from = max(self._poll_from, parse_time(measurements[-1]['timestamp']))
        else:
            _LOGGER.info('Data response for appliance %s did not contain any measurements data', self._applianceId)


        self._data_fetch_completed = datetime.now()

        self._fetching_data.set()
        self._fetching_data = None

    def consumption(self, since):
        # XXX: As self._withdrawals is sorted, we could speed this up by a binary search,
        #      but most likely data sets are small enough that a linear scan is fine.
        return sum((w['waterconsumption'] for w in self._withdrawals if w['starttime'] >= since))

    def measurement(self, key):
        if key in self._measurements:
            return self._measurements[key]
        return STATE_UNKNOWN

class GroheSenseGuardWithdrawalsEntity(Entity):
    def __init__(self, reader, name, days):
        self._reader = reader
        self._name = name
        self._days = days

    #@property
    #def unique_id(self):
    #    return '{}-{}'.format(self._reader.applianceId, self._days)

    @property
    def name(self):
        return '{} {} day'.format(self._name, self._days)

    @property
    def state(self):
        if self._days == 1: # special case, if we're averaging over 1 day, just count since midnight local time
            since = datetime.now().astimezone().replace(hour=0,minute=0,second=0,microsecond=0)
        else: # otherwise, it's a rolling X day average
            since = datetime.now(tz=timezone.utc) - timedelta(self._days)
        return self._reader.consumption(since)

    async def async_update(self):
        await self._reader.async_update()

class GroheSenseSensorEntity(Entity):
    def __init__(self, reader, name, key):
        self._reader = reader
        self._name = name
        self._key = key

    @property
    def name(self):
        return '{} {}'.format(self._name, self._key)

    @property
    def state(self):
        return self._reader.measurement(self._key)

    async def async_update(self):
        await self._reader.async_update()
