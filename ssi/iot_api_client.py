#!/usr/bin/env python3
# pylint: disable=attribute-defined-outside-init
# pylint: disable=access-member-before-definition
"""
Subpackage for SSI IOT API CLient
"""
from ssi.api_client import ApiClient
from beartype import beartype
from typing import Optional, List, Dict
from tabulate import tabulate
import argparse
import sys
import os
import time
import enum
import json
import threading
import queue
import websocket

class DeviceApiEndpointType(enum.Enum):
    """
    Possible call types for device api calls
    """
    CALL = 1
    EVENT = 2

class DeviceApiException(Exception):
    pass

class DeviceApi():
    @beartype
    def __init__(self, device: "Device", ws):
        self._device = device
        self._ws = ws
        self._endpoints = {
            "get_call_info": (0, DeviceApiEndpointType.CALL),
            }
        self._last_message_id = 0
        self._msg_queues = {}
        self._running = True
        self._msg_router_thread = threading.Thread(target=self._msg_router, daemon=True)
        self._msg_router_thread.start()
        endpoints = self.call('get_call_info')
        assert endpoints is not None
        self._version = endpoints['version_string']
        for i in range(len(endpoints['endpoints'])):
            type_str = endpoints['endpoint_types'][i]
            if (type_str == 'call'):
                type_enum = DeviceApiEndpointType.CALL
            elif (type_str == 'event'):
                type_enum = DeviceApiEndpointType.EVENT
            self._endpoints[endpoints['endpoints'][i]] = (i, type_enum)
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _msg_router(self):
        while self._running:
            try:
                msg = self._ws.recv_json()
            except websocket._exceptions.WebSocketConnectionClosedException as e:
                if self._running:
                    self._running = False
                    raise DeviceApiException("Connection closed") from e
                return
            if 'message_id' not in msg:
                continue
            if msg['message_id'] not in self._msg_queues:
                print("Got message for unknown message id: %d" % msg['message_id'])
                continue
            self._msg_queues[msg['message_id']].put(msg)

    def _send_msg(self, endpoint: str, **kwargs):
        if endpoint not in self._endpoints:
            raise Exception("Invalid endpoint")
        endpoint_id = self._endpoints[endpoint][0]
        payload = kwargs
        self._last_message_id += 1
        msg_id = self._last_message_id
        self._msg_queues[msg_id] = queue.Queue()
        self._ws.send_json({
            "endpoint_id": endpoint_id,
            "payload": payload,
            "message_id": msg_id
            })
        return msg_id

    @beartype
    def call(self, endpoint: str, as_json: bool = True, timeout=None, **kwargs):
        if self._endpoints[endpoint][1] != DeviceApiEndpointType.CALL:
            raise Exception("Invalid endpoint type")
        else:
            msg_id = self._send_msg(endpoint, **kwargs)
            timeout = 5
            ret = self._msg_queues[msg_id].get(timeout=timeout)
            del self._msg_queues[msg_id]
            if ret['status_code'] != 0:
                raise DeviceApiException(ret['payload'])
            if as_json:
                return json.loads(ret['payload'])
            else:
                return ret['payload']

    def event(self, endpoint: str, as_json: bool=True, **kwargs):
        """
        Returns a generator for an event endpoint
        """
        if self._endpoints[endpoint][1] != DeviceApiEndpointType.EVENT:
            raise Exception("Invalid endpoint type")
        else:
            msg_id = self._send_msg(endpoint, **kwargs)
            while True:
                ret = self._msg_queues[msg_id].get()
                if ret['status_code'] == 0xFFFF:
                    del self._msg_queues[msg_id]
                    break
                if ret['status_code'] != 0:
                    raise DeviceApiException(ret['payload'])
                if as_json:
                    yield json.loads(ret['payload'])
                else:
                    yield ret['payload']

    def get_calls(self):
        return [k for k in self._endpoints.keys() if self._endpoints[k][1] ==
                DeviceApiEndpointType.CALL]

    def get_events(self):
        return [k for k in self._endpoints.keys() if self._endpoints[k][1] ==
                DeviceApiEndpointType.EVENT]

    def close(self):
        self._running = False
        self._ws.close()
        self._msg_router_thread.join()

class Device():
    """
    Represents a device in the SSI IOT Stack and expose data and operations
    which are related to it
    """
    @beartype
    def _init(self, key, val):
        self.__dict__[key] = val

    def _reset(self, attribs=None):
        # This is a dictionary of properties. Properties are key=>value
        # annotations which are used to organize a device, for example, with
        # ODMX equipment IDs. These are lazy loaded
        self._init('_props', None)
        # A dictionary of attributes. Attributes are distinct from properties
        # in that they are understood by the IOT stack. They include things
        # LIKE the hostname, serial, and so forth. These may be lazy loaded or
        # they may be initialzied
        self._init('_attribs', attribs)
        if self._attribs and "properties" in self._attribs:
            self._props = self._attribs['properties']
            del self._attribs['properties']
        # A dictionary of configurations. Configurations are JSON files which
        # are synced with the device to control certian device behvaiors. The
        # specific kidns of configurations that are pushed/pulled depend on the
        # device's software. For example, ert_acquisition.json is one such
        # config and controls ERT monitoring. ert_hardware.json is another and
        # defines the hardware properties of the device such as the board
        # revision. Keys are set to the filename without the extension. They
        # are lazy loaded just like the attributes and properties
        self._init('_configs', {})
        # Same as above except for status files
        self._init('_statuses', {})

    def refresh(self):
        """
        Clear locally stored data about the device, forcing a refetch from the
        server
        """
        self._reset()

    @beartype
    def __init__(self, device_id: int, api_client: 'IotApiClient',
                 attribs: Optional[Dict] = None):
        """
        Takes the device_id and the api client handle.
        """
        # The device ID is always set
        self._init('id', device_id)
        # The API handle
        self._init('_api', api_client)
        # Whether the device is read only. This disallows modifying the device
        # which is on by default. Mutability can be enabled on the API layer.
        self._init('_ro', False)
        self._reset(attribs)

    def __repr__(self):
        return f"<Device device_id={self.id} hostname={self.hostname}>"

    @beartype
    def api(self, call: str, params: Optional[Dict] = None, **kwargs):
        """
        Perform an API call which takes a `device_id` parameter using this
        device's ID
        """
        get_params = kwargs.get('get_params', {})
        get_params['device_id'] = self.id
        kwargs['get_params'] = get_params
        if params is None:
            params = {}
        return self._api(call, params=params, **kwargs)

    @beartype
    def ws(self, path: str, **kwargs):
        """
        Returns a websocket connection to the IOT server
        """
        get_params = kwargs.get('get_params', {})
        get_params['device_id'] = self.id
        kwargs['params'] = get_params
        return self._api.api.ws(path, **kwargs)

    @beartype
    def get_prop(self, prop: str):
        """
        Get's a device property. A property is a user-definable key/value pair.
        Devices may have arbitrary properties which are useful for specific
        applications. Currently this is principally used with ODMX to provide
        device/equipment UUID mappings. None is returned if the property
        doesn't exist.
        """
        if self._props is None:
            self._resolve_attribs()
        if prop in self._props:
            return self._props[prop]
        return None

    @beartype
    def set_prop(self, property: str, value: Optional[str]):
        """
        Sets a device property to a value. This is currently used for ODMX to
        provide device/equipment_uuid mappings. The ERT ingestion code uses
        this to assign equipment uuids to devices. Setting a value of None
        removes the property from the device. 
        """
        if not self._ro:
            assert (self.api("iot/set_device_property", {
                'property': property,
                'value': value
            }) == "OK")
            self.refresh()

    def get_config_files(self):
        """
        Returns a list of configuration files that exist for the device
        """
        return self.api("iot/list_device_configs")

    def remove_config(self, file: str):
        """
        Removes a configuration file from the device
        """
        self._raise_if_ro()
        if file in self._configs:
           del self._configs[file]
        self.api(f"iot/set_device_config?config_name={file}", method="DELETE")

    def create_config(self, file: str, config: Optional[Dict] = None):
        """
        Creates a new configuration file on the device
        """
        self._raise_if_ro()
        if config is None:
            config = {}
        self.api(
            f"iot/set_device_config?config_name={file}",
            config,
            method="POST")

    def replace_config(self, file: str, config: Dict):
        """
        Replaces a configuration file on the device
        """
        self._raise_if_ro()
        if file in self._configs:
           del self._configs[file]
        self.api(
            f"iot/set_device_config?config_name={file}",
            config,
            method="PUT")

    @beartype
    def get_config(self, file: str, refresh=False):
        """
        Returns the configuration set for a given file. A config file is a
        json file which is part of the configuration set for the device. The
        configuration set is synchronized with the device and corresponds to
        the json files under /opt/ssi/config on the device. The file
        parameter assumes no '.json' extension or path, so to get the
        configuration for ert hardware which is under 'ert_hardware.json' the
        file parameter here should just be 'ert_hardware'. None is returned
        if no configuration file exists.
        """
        if file in self._configs and not refresh:
            return self._configs[file]
        self._configs[file] = self.api(
            "iot/get_device_config", {'config_name': file})
        return self.get_config(file)

    def _raise_if_ro(self):
        if self._ro:
            raise RuntimeError("The device is read only")

    @beartype
    def set_config_key(self, file: str, key_name: str, value: str):
        """
        Sets a configuration key in the given configuration file for the
        device. If the file does not exist, then it is created.
        """
        self._raise_if_ro()
        if file in self._configs:
           del self._configs[file]
        return self.api(f"iot/set_device_config?config_name={file}", {key_name:
                                                                      value},
                        method="PATCH")

    @beartype
    def clear_config_key(self, file: str, key_name: str):
        """
        Clears a configuration key in the given configuration file for the
        device. Most software will then set this to a default value, but the
        exact behavior is specific to the software which uses the config file.
        """
        self._raise_if_ro()
        if file in self._configs:
            del self._configs[file]
        # TODO double check the semantics of this
        return self.api(
            f"iot/set_device_config?config_name={file}", {key_name: None}, method="PATCH")

    @beartype
    def get_status_files(self):
        """
        Returns a list of status files for the device.
        """
        return self.api("iot/list_device_statuses")

    @beartype
    def get_status(self, file: str):
        """
        Returns the status set for a given file. A status file is a json file
        which is generated by the device. The status is sychronized
        periodically and corresponds to the json files under /opt/ssi/status on
        the device. The file parameter assumes no '.json'
        """
        return self.api("iot/get_device_status", {'status_name': file})

    def _resolve_attribs(self):
        """
        Queries the IOT api for device attributes. Sometimes these attributes
        are provided by the IotApiClient in bulk. This does a single query
        based on the device id
        """
        self._attribs = self.api("iot/get_device_info", {'with_props': True})
        if 'properties' in self._attribs:
            self._props = self._attribs['properties']
            del self._attribs['properties']

    def __getattr__(self, key):
        if key in self.__dict__:
            return self.__dict__[key]
        if self._attribs is None:
            self._resolve_attribs()
        if key in self._attribs:
            return self._attribs[key]
        prop_val = self.get_prop(key)
        if prop_val:
            return prop_val
        raise AttributeError(f"{self} has no attribute '{key}'")

    def to_dict(self):
        """
        Returns a dictionary representation of the device
        """
        if self._attribs is None:
            self._resolve_attribs()
        if self._props is None:
            self._props = {}
        return {
            **self._attribs,
            **self._props
        }

    def seconds_to_human(self, seconds: int):
        """
        Converts a number of seconds to a human readable string
        """
        ret = ""
        if seconds > 86400:
            ret += f"{int(seconds // 86400)}d "
            seconds %= 86400
        if seconds > 3600:
            ret += f"{int(seconds // 3600)}h "
            seconds %= 3600
        if seconds > 60:
            ret += f"{int(seconds // 60)}m "
            seconds %= 60
        if seconds > 0:
            ret += f"{int(seconds)}s "
        return ret.strip()

    def to_human_dict(self):
        d = self.to_dict()
        last_heartbeat = d.get('heartbeat_utc')
        if last_heartbeat:
            delta = self.seconds_to_human(time.time() - last_heartbeat)
        else:
            delta = "never"
        human_dict = {
            'id': d['device_id'],
            'hostname': d['hostname'],
            'type': d['type'],
            'status': "Connected" if d['connected'] else "Disconnected",
            'last_heartbeat': delta,
        }
        return human_dict

    def mutable(self):
        """
        Gives the device mutability, letting the API modify device parameters,
        configuration, and properties
        """
        self._ro = False

    def const(self):
        """
        Disables mutability
        """
        self._ro = True

    def __setattr__(self, key, value):
        self._raise_if_ro()
        if key == "hostname":
            self.api("iot/set_device_hostname", {'hostname': value})
            self.refresh()
        elif key == "type":
            self.api("iot/set_device_type", {'type': value})
            self.refresh()
        elif key == "serial":
            raise RuntimeError("Modifying 'serial' is currently not supported")
        elif key == "location":
            self.api("iot/update_device_location", {'lat': value[0],
                                                    'lon': value[1]})
            self.refresh()
        elif key in self.__dict__:
            self.__dict__[key] = value
        else:
            self.set_prop(key, value)

    def get_bandwidth_stats(self):
        """
        Returns the bandwidth usage for the device in bytes
        """
        return self.api("iot/get_device_bandwidth_stats")

    def get_events(self, kind=None, limit=10):
        """
        Returns the events for the device
        """
        args = {}
        if limit:
            args['limit'] = limi
        if kind:
            args['events'] = kind.split(',')
        return self.api("iot/get_device_events", args)

    def gen_events(self):
        """
        Returns a generator for for device events as they occur
        """
        return self._api.gen_device_events([self])

    def get_mapped_ports(self):
        """
        Returns the ports mapped to the device
        """
        return self.api("iot/get_device_port_mappings")

    def map_port(self, remote_port: int, remote_host: str) -> int:
        """
        Maps a local port to a remote port on the device
        """
        self._raise_if_ro()
        return self.api("iot/device_map_port", {'remote_port': remote_port,
                                                'remote_host': remote_host})

    def unmap_port(self, local_port: int):
        """
        Unmaps a local port
        """
        self._raise_if_ro()
        return self.api("iot/device_unmap_port", {'local_port': local_port})

    def add_admin(self, user_id: int):
        """
        Adds an admin to the device
        """
        self._raise_if_ro()
        return self.api("iot/assign_user_to_device", {'user_id': user_id})

    def remove_admin(self, user_id: int):
        """
        Removes an admin from the device
        """
        self._raise_if_ro()
        return self.api("iot/remove_user_from_device", {'user_id': user_id})

    def list_admins(self):
        """
        Lists the admins for the device
        """
        return self.api("iot/get_device_users")

    def set_type(self, device_type: str):
        """
        Sets the device type
        """
        self._raise_if_ro()
        return self.api("iot/set_device_type", {'type': device_type})

    def set_hostname(self, hostname: str):
        """
        Sets the device hostname
        """
        self._raise_if_ro()
        return self.api("iot/set_device_hostname", {'hostname': hostname})

    def set_project(self, project_id: int):
        """
        Sets the device project
        """
        self._raise_if_ro()
        return self.api("iot/set_device_project", {'project_id': project_id})

    def open_api(self):
        return DeviceApi(self, self.ws('iot/device_api'))

    @beartype
    def get_file_data(self, path: str):
        """
        Gets a file from the device as data
        """
        device_id = self.device_id
        resp = self._api(f'iot/device/fs/{device_id}/{path}',
                         raw_response=True, method='GET')
        self._api.api.check_status_error(resp, 'iot/device/fs')
        if content_type := resp.headers.get('Content-Type'):
            content_type = content_type.split(';')[0]
            if content_type == 'application/json':
                return resp.json()
            if content_type == 'application/octet-stream':
                return resp.content
            if content_type == 'text/plain':
                return resp.text
        return resp.content

    @beartype
    def get_file(self, path: str, local_path: str, overwrite: bool = False):
        """
        Gets a file from the device as a local file
        """
        device_id = self.device_id
        if not overwrite and os.path.exists(local_path):
            raise RuntimeError(f"File {local_path} already exists")
        resp = self._api(f'iot/device/fs/{device_id}/{path}',
                         raw_response=True, method='GET', stream=True)
        self._api.api.check_status_error(resp, 'iot/device/fs')
        with open(local_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)

    @beartype
    def put_file_data(self, path: str, data: bytes, overwrite: bool = False):
        """
        Puts a file on the device from data
        """
        self._raise_if_ro()
        device_id = self.device_id
        resp = self._api(f'iot/device/fs/{device_id}/{path}', method='HEAD',
                         raw_response=True)
        method = 'POST'
        if resp.status_code == 200:
            method = 'PUT'
            if not overwrite:
                raise RuntimeError(f"File {path} already exists")
        resp = self._api(f'iot/device/fs/{device_id}/{path}',
                         raw_response=True, method=method, data=data)
        self._api.api.check_status_error(resp, 'iot/device/fs')

    @beartype
    def put_file(self, local_path: str, path: str, overwrite: bool = False):
        """
        Puts a file on the device from a local file
        """
        self._raise_if_ro()
        with open(local_path, 'rb') as f:
            self.put_file_data(path, f.read(), overwrite)

    @beartype
    def ls(self, path:str):
        """
        Returns a directory listing for a device file path
        """
        device_id = self.device_id
        resp = self._api(f'iot/device/fs/{device_id}/{path}',
                         params={'dir_only': True},
                         raw_response=True, method='GET')
        self._api.api.check_status_error(resp, 'iot/device/fs')
        file_list = resp.json()
        assert isinstance(file_list, list)
        assert(file_list[0].get('name'))
        # Remove index field
        for f in file_list:
            del f['index']
        return file_list

    @beartype
    def rm(self, path: str):
        """
        Removes a file from the device
        """
        self._raise_if_ro()
        device_id = self.device_id
        resp = self._api(f'iot/device/fs/{device_id}/{path}',
                         raw_response=True, method='DELETE')
        self._api.api.check_status_error(resp, 'iot/device/fs')


class IotApiClient():
    """
    IOT Api Client object which provides methods for querying devices and
    performing operations on them
    """
    @beartype
    def __init__(self, url: Optional[str] = None, token: Optional[str] = None):
        if not url and not os.environ.get('SSI_API_URL'):
            url = "https://things.subsurfaceinsights.com/"
        self.api = ApiClient(url=url, token=token)

    def __call__(self, func, **kwargs):
        return self.api(func, **kwargs)

    @beartype
    def _id_to_device_obj(self, device_id: int):
        pass

    @beartype
    def _device_dict_to_device_obj(self, device: dict):
        pass

    @beartype
    def get_devices_by_property(self, prop: str, value: str):
        devices = self.api("iot/get_devices_by_property",
                           {
                               'property': prop,
                               'value': value
                           })
        return [Device(device, self) for device in devices]

    @beartype
    def get_devices_by_project(self, project_subdomain: str):
        self.api.project = project_subdomain
        devices = self.api("iot/list_devices")
        self.api.project = None
        return [Device(device, self) for device in devices]

    def _generate_event(self, ws):
        while True:
            data = ws.recv_json()
            if data:
                yield data

    @beartype
    def gen_device_events(self, devices: Optional[List[Device]] = None, kind: Optional[List[str]] = None):
        """
        Returns a generator which yields events as they occur
        """
        if devices is None:
            device_ids = [-1]
        else:
            device_ids = ",".join([str(device.id) for device in devices])
        args = {
            'device_ids': device_ids,
        }
        if kind:
            args['kind'] = kind
        return self._generate_event(self.api.ws("iot/device_events", args))

    @beartype
    def get_my_devices(self):
        """
        Returns a list of devices owned by the user associated with the token
        """
        devices = self.api("iot/get_my_devices", {
            'with_info': True,
        });
        return [Device(device['device_id'], self, device) for device in devices]

    @beartype
    def get_devices_by_user(self, user_id: int):
        """
        Returns a list of devices that belong to a user.
        """
        devices = self.api("iot/list_devices", {'user_id': user_id})
        #TODO Improve the efficiency of these queries by preloading attribs
        return [Device(device, self) for device in devices]

    @beartype
    def get_device_by_hostname(self, hostname: str):
        """
        Returns a device handle by the given hostname. If there is no device by
        that hostname, then None is returned
        """
        devices = self.api("iot/get_devices_by_hostname",
                           {'hostname': hostname})
        if devices:
            assert (len(devices) == 1)
            device = devices[0]
            return Device(device, self)
        return None

    @beartype
    def get_device_by_serial(self, serial: str):
        """
        Returns a device by the device serial number. If there is no device by
        that serial, then None is returned
        """
        device = self.api("iot/get_device_by_serial", {'serial': serial})
        if device:
            return Device(device, self)
        return None

    @beartype
    def get_device_by_id(self, device_id: int):
        """
        Returns a device by the device id. If there is no device by that id,
        then None is returned
        """
        device = self.api("iot/get_device_info", {'device_id': device_id})
        if device:
            return Device(device['device_id'], self, device)
        return None

    @beartype
    def get_device_fuzzy(self, device: str):
        ret = None
        try:
            device_id = int(device)
            ret = self.get_device_by_id(device_id)
        except ValueError:
            pass
        if ret:
            return ret
        ret = self.get_device_by_serial(device)
        if ret:
            return ret
        ret = self.get_device_by_hostname(device)
        if ret:
            return ret
        return None

    @beartype
    def list_devices(self):
        """
        Returns all devices in the system
        """
        devices = self.api("iot/list_devices", {'with_info': True})
        return [Device(device['device_id'], self, device)
                for device in devices]

    @beartype
    def list_online_devices(self):
        """
        Returns all online devices in the system
        """
        devices = self.api("iot/get_connected_devices")
        return [Device(device, self) for device in devices]


def cli_display_result_from_json_table(result: dict):
    print(tabulate(result['data'], headers=result['headers']))


def cli_display_result(result):
    if isinstance(result, list) and len(result) > 0:
        if isinstance(result[0], dict):
            print(tabulate(result, headers="keys", tablefmt="fancy_outline"))
        else:
            print(result)
    elif isinstance(result, dict):
        print(tabulate([result], headers="keys", tablefmt="fancy_outline"))
    else:
        print(result)


def cli_display_devices(devices):
    devices_table = [device.to_human_dict() for device in devices]
    cli_display_result(devices_table)

def cli_print_hostnames(devices):
    for device in devices:
        print(device.to_dict()['hostname'])

def cli_tool():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=None)
    parser.add_argument("--user-project-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--project", default=None)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True
    parser_list = subparsers.add_parser("list")
    parser_list.add_argument("--hostnames-only", action="store_true");
    parser_list_online = subparsers.add_parser("list-online")
    parser_list_online.add_argument("--hostnames-only", action="store_true");
    parser_events = subparsers.add_parser("list-events")
    parser_events.add_argument("device", type=str)
    # parser_events.add_argument("device", type=str, nargs="+")
    parser_events.add_argument("--kind", type=str, help="Comma separated string of events to list")
    parser_events.add_argument("--limit", type=int, default=10)
    parser_events_watch = subparsers.add_parser("watch-events")
    parser_events_watch.add_argument("device", type=str, default=None)
    # parser_events_watch.add_argument("device", type=str, nargs="+")
    parser_events_watch.add_argument("--kind", type=str, help="Comma separated string of events to watch")
    parser_events_watch.add_argument("--limit", type=int, default=10)
    subparsers.add_parser("watch-all-events")
    parser_mapped_ports = subparsers.add_parser("mapped-ports")
    parser_mapped_ports.add_argument("device", type=str)
    parser_map_port = subparsers.add_parser("map-port")
    parser_map_port.add_argument("device", type=str)
    parser_map_port.add_argument("remote_port", type=int)
    parser_map_port.add_argument("remote_host", type=str)
    parser_unmap_port = subparsers.add_parser("unmap-port")
    parser_unmap_port.add_argument("device", type=str)
    parser_unmap_port.add_argument("local_port", type=int)
    parser_gen_ssh_host_config = subparsers.add_parser("gen-ssh-host-config")
    parser_gen_ssh_host_config.add_argument("device", type=str)
    parser_set_type = subparsers.add_parser("set-type")
    parser_set_type.add_argument("device", type=str)
    parser_set_type.add_argument("type", type=str)
    parser_set_hostname = subparsers.add_parser("set-hostname")
    parser_set_hostname.add_argument("device", type=str)
    parser_set_hostname.add_argument("hostname", type=str)
    parser_set_project = subparsers.add_parser("set-project")
    parser_set_project.add_argument("device", type=str)
    parser_set_project.add_argument("project", type=str)
    parser_add_admin = subparsers.add_parser("add-admin")
    parser_add_admin.add_argument("device", type=str)
    parser_add_admin.add_argument("email", type=str)
    parser_remove_admin = subparsers.add_parser("remove-admin")
    parser_remove_admin.add_argument("device", type=str)
    parser_remove_admin.add_argument("email", type=str)
    parser_list_admins = subparsers.add_parser("list-admins")
    parser_list_admins.add_argument("device", type=str)
    parser_list_statuses = subparsers.add_parser("list-statuses")
    parser_list_statuses.add_argument("device", type=str)
    parser_get_status = subparsers.add_parser("get-status")
    parser_get_status.add_argument("device", type=str)
    parser_get_status.add_argument("status", type=str)
    parser_list_configs = subparsers.add_parser("list-configs")
    parser_list_configs.add_argument("device", type=str)
    parser_get_config = subparsers.add_parser("get-config")
    parser_get_config.add_argument("device", type=str)
    parser_get_config.add_argument("config", type=str)
    args = parser.parse_args()
    if not args.user_project_url:
        args.user_project_url = args.url
    api = IotApiClient(url=args.url, token=args.token)
    paf_api = ApiClient(url=args.user_project_url, token=args.token)
    device = None
    if hasattr(args, "device"):
        device = api.get_device_fuzzy(args.device)
        if not device:
            print("No device found")
            return 1
    if args.command == "list":
        devices = api.list_devices()
        if args.hostnames_only:
            cli_print_hostnames(devices)
        else:
            cli_display_devices(devices)
    elif args.command == "list-online":
        devices = api.list_online_devices()
        if args.hostnames_only:
            cli_print_hostnames(devices)
        else:
            cli_display_devices(devices)
    elif args.command == "list-events":
        assert device
        events = device.get_events(args.kind, args.limit)
        cli_display_result_from_json_table(events)
    elif args.command == "watch-events":
        assert device
        events = device.gen_events()
        for event in events:
            print(event)
    elif args.command == "watch-all-events":
        events = api.gen_device_events()
        for event in events:
            print(event)
    elif args.command == "mapped-ports":
        assert device
        ports = device.get_mapped_ports()
        cli_display_result(ports)
    elif args.command == "map-port":
        assert device
        port = device.map_port(args.remote_port, args.remote_host)
        print(port['local_port'])
    elif args.command == "unmap-port":
        assert device
        device.unmap_port(args.local_port)
    elif args.command == "gen-ssh-host-config":
        assert device
        port = device.map_port(22, 'localhost')['local_port']
        print(f"""Host {device.hostname}
  HostName localhost
  Port {port}
  User pi
  ProxyJump things.int.subsurfaceinsights.com
""")
    elif args.command == "set-type":
        assert device
        device.set_type(args.type)
    elif args.command == "set-hostname":
        assert device
        device.set_hostname(args.hostname)
    elif args.command == "set-project":
        assert device
        project = paf_api("project/v2/get_project_by_subdomain", {"subdomain": args.project})
        device.set_project(project['paf_project_id'])
    elif args.command == "add-admin":
        assert device
        user_id = paf_api("user/v2/get_user_by_email",
                          {"paf_user_email": args.email})["paf_user_id"]
        device.add_admin(user_id)
    elif args.command == "remove-admin":
        assert device
        user_id = paf_api("user/v2/get_user_by_email",
                          {"paf_user_email": args.email})["paf_user_id"]
        device.remove_admin(user_id)
    elif args.command == "list-admins":
        assert device
        admins = device.list_admins()
        # TODO optimize this call
        users = [paf_api("user/v2/get_user_by_id",
                         {"paf_user_id": user_id}) for user_id in admins]
        cli_display_result(users)
    elif args.command == "list-statuses":
        assert device
        events = device.get_status_files()
        cli_display_result(events)
    elif args.command == "get-status":
        assert device
        status = device.get_status(args.status)
        cli_display_result(status)
    elif args.command == "list-configs":
        assert device
        configs = device.get_config_files()
        cli_display_result(configs)
    elif args.command == "get-config":
        assert device
        config = device.get_config(args.config)
        cli_display_result(config)
    return 0

if __name__ == "__main__":
    sys.exit(cli_tool())
