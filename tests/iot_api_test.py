from ssi.iot_api_client import IotApiClient
from ssi.api_client import ApiException
import random

client = IotApiClient()
client.api.trace = True

def test_list_devices():
    devices = client.list_devices()
    assert(len(devices) > 10)

def test_get_devices_by_property():
    devices = client.get_devices_by_property("test_prop", "0x12")
    assert(len(devices) == 0)
    devices = client.get_devices_by_property(
            "equipment_uuid",
            "5144beed-8654-429b-9fe9-064d9c0d4770")
    assert(len(devices)==1)
    assert(devices[0].id == 46)

def test_get_devices_by_project():
    devices = client.get_devices_by_project('rhostick')
    assert len(devices) > 0

def test_get_devices_by_user():
    devices = client.get_devices_by_user(1)
    assert len(devices) > 0

def test_get_device_by_hostname():
    device = client.get_device_by_hostname('test')
    assert device.id == 1

def test_get_device_by_serial():
    device = client.get_device_by_serial('FAKESYSTEM000000')
    assert device.id == 1


test_device = client.get_device_by_hostname('test')
assert(test_device)

def test_device_prop():
    TEST_PROP_VAL = str(random.randint(1000, 2000))
    test_device.set_prop('test_prop', TEST_PROP_VAL)
    test_device.test_prop2 = TEST_PROP_VAL
    assert test_device.test_prop == TEST_PROP_VAL
    assert test_device.test_prop2 == TEST_PROP_VAL
    assert test_device.get_prop('test_prop') == TEST_PROP_VAL
    test_device.test_prop = None
    test_device.test_prop2 = None
    try:
        print(test_device.test_prop2)
        assert False
    except AttributeError:
        pass
    try:
        print(test_device.test_prop)
        assert False
    except AttributeError:
        pass

def test_device_config():
    configs = test_device.get_config_files()
    if 'test' in configs:
        test_device.remove_config('test')
    test_device.create_config('test')
    configs = test_device.get_config_files()
    assert 'test' in configs
    test_config = test_device.get_config('test')
    assert test_config == {}
    test_device.replace_config('test', {'foo': 'bar'})
    test_config = test_device.get_config('test')
    assert test_config == {'foo': 'bar'}
    test_device.set_config_key('test', 'foo', 'baz')
    assert 'foo' in test_device.get_config('test')
    test_device.clear_config_key('test', 'foo')
    assert 'foo' not in test_device.get_config('test')

def test_device_status():
    statuses = test_device.get_status_files()
    modem_status = test_device.get_status('modem-status')
    print(modem_status)

def test_modify_base_attributes():
    # Should fail validation
    try:
        test_device.hostname = "foo_bar"
        assert False
    except ApiException:
        pass

    test_device.hostname = "test-change"
    test_device_check = client.get_device_by_hostname("test-change")
    assert test_device_check.id == test_device.id
    assert test_device.hostname == test_device_check.hostname
    test_device_check.hostname = "test"
    assert test_device_check.hostname == "test"
    try:
        test_device.serial = "foo"
        assert False
    except RuntimeError:
        pass
    test_device.type = "ert-soilprobe"
    test_device.location = [49.0, 8.0];

def test_bandwidth_stats():
    usage = test_device.get_bandwidth_stats()
    assert usage

def test_events():
    events = test_device.get_events()
    assert events
    events = test_device.gen_events()
    # We have to toggle the key to garantee a change event
    test_device.set_config_key('test', 'foo', 'bar');
    test_device.set_config_key('test', 'foo', 'baz');
    event = next(events)
    assert(event['event'] == "config_changed")
    assert(event['msg'] == "test")
    assert event

