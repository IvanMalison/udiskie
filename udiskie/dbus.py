"""
Common DBus utilities.
"""

from functools import partial

from gi.repository import Gio
from gi.repository import GLib

from .async_ import Async, gio_callback, pack


__all__ = [
    'InterfaceProxy',
    'PropertiesProxy',
    'ObjectProxy',
    'BusProxy',
    'connect_service',
    'MethodsProxy',
]


unpack_variant = GLib.Variant.unpack


def DBusCall(proxy, method_name, signature, args, flags=0, timeout_msec=-1):
    """
    Asynchronously call the specified method on a DBus proxy object.

    :param Gio.DBusProxy proxy:
    :param str method_name:
    :param str signature:
    :param tuple args:
    :param int flags:
    :param int timeout_msec:
    """
    future = Async()
    cancellable = None
    proxy.call(
        method_name,
        GLib.Variant(signature, tuple(args)),
        flags,
        timeout_msec,
        cancellable,
        _DBusCall_callback,
        future,
    )
    return future


@gio_callback
def _DBusCall_callback(proxy, result):
    value = proxy.call_finish(result)
    return pack(*unpack_variant(value))


def DBusCallWithFdList(proxy, method_name, signature, args, fds, flags=0,
                       timeout_msec=-1):
    """
    Asynchronously call the specified method on a DBus proxy object.

    :param Gio.DBusProxy proxy:
    :param str method_name:
    :param str signature:
    :param tuple args:
    :param int flags:
    :param int timeout_msec:
    """
    future = Async()
    cancellable = None
    fd_list = Gio.UnixFDList.new_from_array(fds)
    proxy.call_with_unix_fd_list(
        method_name,
        GLib.Variant(signature, tuple(args)),
        flags,
        timeout_msec,
        fd_list,
        cancellable,
        _DBusCallWithFdList_callback,
        future,
    )
    return future


@gio_callback
def _DBusCallWithFdList_callback(proxy, result):
    value, fds = proxy.call_with_unix_fd_list_finish(result)
    return pack(*unpack_variant(value))


class InterfaceProxy(object):

    """
    DBus proxy object for a specific interface.

    Provides attribute accessors to properties and methods of a DBus
    interface on a DBus object.

    :ivar str object_path: object path of the DBus object
    :ivar PropertiesProxy property: attribute access to DBus properties
    :ivar Gio.DBusProxy method: attribute access to DBus methods
    :ivar Gio.DBusProxy _proxy: underlying proxy object
    """

    def __init__(self, proxy):
        """
        Initialize property and method attribute accessors for the interface.

        :param Gio.DBusProxy proxy: accessed object
        :param str interface: accessed interface
        """
        self._proxy = proxy
        self.object_path = proxy.get_object_path()

    @property
    def object(self):
        """
        Get a proxy for the underlying object.

        :rtype: ObjectProxy
        """
        proxy = self._proxy
        return ObjectProxy(proxy.get_connection(),
                           proxy.get_name(),
                           proxy.get_object_path())

    def connect(self, event, handler):
        """
        Connect to a DBus signal.

        :param str event: event name
        :param handler: callback
        :returns: subscription id
        :rtype: int
        """
        interface = self._proxy.get_interface_name()
        return self.object.connect(interface, event, handler)

    def call(self, method_name, signature='()', *args):
        return DBusCall(self._proxy, method_name, signature, args)


class PropertiesProxy(InterfaceProxy):

    Interface = 'org.freedesktop.DBus.Properties'

    def __init__(self, proxy, interface_name=None):
        super(PropertiesProxy, self).__init__(proxy)
        self.interface_name = interface_name

    def GetAll(self, interface_name=None):
        return self.call('GetAll', '(s)',
                         interface_name or self.interface_name)


class ObjectProxy(object):

    """
    Simple proxy class for a DBus object.

    :param Gio.DBusConnection connection:
    :param str bus_name:
    :param str object_path:
    """

    def __init__(self, connection, bus_name, object_path):
        """
        Initialize member variables.

        :ivar Gio.DBusConnection connection:
        :ivar str bus_name:
        :ivar str object_path:

        This performs no IO at all.
        """
        self.connection = connection
        self.bus_name = bus_name
        self.object_path = object_path

    def _get_interface(self, name):
        """
        Get a Gio native interface proxy for this Dbus object.

        :param str name: interface name
        :returns: a proxy object for the other interface
        :rtype: Gio.DBusProxy
        """
        return DBusProxyNew(
            self.connection,
            Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES |
            Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS,
            info=None,
            name=self.bus_name,
            object_path=self.object_path,
            interface_name=name,
        )

    async def get_interface(self, name):
        """
        Get an interface proxy for this Dbus object.

        :param str name: interface name
        :returns: a proxy object for the other interface
        :rtype: InterfaceProxy
        """
        proxy = await self._get_interface(name)
        return InterfaceProxy(proxy)

    async def get_property_interface(self, interface_name=None):
        proxy = await self._get_interface(PropertiesProxy.Interface)
        return PropertiesProxy(proxy, interface_name)

    @property
    def bus(self):
        """
        Get a proxy object for the underlying bus.

        :rtype: BusProxy
        """
        return BusProxy(self.connection, self.bus_name)

    def connect(self, interface, event, handler):
        """
        Connect to a DBus signal.

        :param str interface: interface name
        :param str event: event name
        :param handler: callback
        :returns: subscription id
        :rtype: int
        """
        object_path = self.object_path
        return self.bus.connect(interface, event, object_path, handler)

    async def call(self, interface_name, method_name, signature='()', *args):
        proxy = await self.get_interface(interface_name)
        result = await proxy.call(method_name, signature, *args)
        return result


def DBusCallback(connection, sender_name, object_path,
                 interface_name, signal_name, parameters,
                 handler):
    """Call handler unpacked signal parameters."""
    return handler(*unpack_variant(parameters))


def DBusCallbackWithObjectPath(connection, sender_name, object_path,
                               interface_name, signal_name, parameters,
                               handler):
    """Call handler with object_path and unpacked signal parameters."""
    return handler(object_path, *unpack_variant(parameters))


class BusProxy(object):

    """
    Simple proxy class for a connected bus.

    :ivar Gio.DBusConnection connection:
    :ivar str bus_name:
    """

    def __init__(self, connection, bus_name):
        """
        Initialize member variables.

        :param Gio.DBusConnection connection:
        :param str bus_name:

        This performs IO at all.
        """
        self.connection = connection
        self.bus_name = bus_name

    def get_object(self, object_path):
        """
        Get a object representing the specified object.

        :param str object_path: object path
        :returns: a simple representative for the object
        :rtype: ObjectProxy
        """
        return ObjectProxy(self.connection, self.bus_name, object_path)

    def connect(self, interface, event, object_path, handler):
        """
        Connect to a DBus signal.

        :param str interface: interface name
        :param str event: event name
        :param str object_path: object path or ``None``
        :param handler: callback
        """
        callback = DBusCallback if object_path else DBusCallbackWithObjectPath
        return self.connection.signal_subscribe(
            self.bus_name,
            interface,
            event,
            object_path,
            None,
            Gio.DBusSignalFlags.NONE,
            callback,
            handler,
        )

    def disconnect(self, subscription_id):
        """
        Disconnect a DBus signal subscription.
        """
        self.connection.signal_unsubscribe(subscription_id)


def DBusProxyNew(connection, flags, info, name, object_path, interface_name):
    """
    Asynchronously call the specified method on a DBus proxy object.
    """
    future = Async()
    cancellable = None
    Gio.DBusProxy.new(
        connection,
        flags,
        info,
        name,
        object_path,
        interface_name,
        cancellable,
        _DBusProxyNew_callback,
        future,
    )
    return future


@gio_callback
def _DBusProxyNew_callback(proxy, result):
    value = Gio.DBusProxy.new_finish(result)
    if value is None:
        raise RuntimeError("Failed to connect DBus object!")
    return value


def DBusProxyNewForBus(bus_type, flags, info, name, object_path, interface_name):
    """
    Asynchronously call the specified method on a DBus proxy object.
    """
    future = Async()
    cancellable = None
    Gio.DBusProxy.new_for_bus(
        bus_type,
        flags,
        info,
        name,
        object_path,
        interface_name,
        cancellable,
        _DBusProxyNewForBus_callback,
        future,
    )
    return future


@gio_callback
def _DBusProxyNewForBus_callback(proxy, result):
    value = Gio.DBusProxy.new_for_bus_finish(result)
    if value is None:
        raise RuntimeError("Failed to connect DBus object!")
    return value


async def connect_service(bus_name, object_path, interface):
    """
    Connect to the service object on DBus.

    :returns: new proxy object for the service
    :rtype: InterfaceProxy
    :raises BusException: if unable to connect to service.
    """
    proxy = await DBusProxyNewForBus(
        Gio.BusType.SYSTEM,
        Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES |
        Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS,
        info=None,
        name=bus_name,
        object_path=object_path,
        interface_name=interface,
    )
    return InterfaceProxy(proxy)


class MethodsProxy(object):

    """Provide methods as attributes for one interface of a DBus object."""

    def __init__(self, object_proxy, interface_name):
        """Initialize from (ObjectProxy, str)."""
        self._object_proxy = object_proxy
        self._interface_name = interface_name

    def __getattr__(self, name):
        """Get a proxy for the specified method on this interface."""
        return partial(self._object_proxy.call, self._interface_name, name)
