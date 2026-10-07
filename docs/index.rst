solarflow-ble
=============

Async Python library for Zendure SolarFlow devices over Bluetooth Low
Energy. Requires Python 3.14 or newer.

The library is unofficial and reverse-engineered. It is not endorsed by
Zendure and can break when Zendure changes their firmware.

Install
-------

.. code-block:: bash

   uv add solarflow-ble

Usage
-----

:class:`~solarflow_ble.SolarFlowClient` takes a
:class:`~solarflow_ble.BleTransport`. :class:`~solarflow_ble.BleakTransport`
wraps a Bleak ``BLEDevice``. Tests can pass a fake transport instead.

.. code-block:: python

   import asyncio

   from bleak import BleakScanner

   from solarflow_ble import BleakTransport, SolarFlowClient


   async def main() -> None:
       device = await BleakScanner.find_device_by_address("AA:BB:CC:DD:EE:FF")
       if device is None:
           raise SystemExit("SolarFlow not found")
       async with SolarFlowClient(BleakTransport(device)) as client:
           print(client.device_id)
           print(client.state)


   asyncio.run(main())

The ``async with`` block connects on entry and disconnects on exit, also
when the body raises. Call ``connect()`` and ``disconnect()`` yourself to
reconnect. The same client can connect again after ``disconnect()``.

``connect()`` returns once the session is
:attr:`~solarflow_ble.ConnectionStatus.READY`. Every later report updates
``client.state`` and is passed to the optional ``update_callback`` as a
:class:`~solarflow_ble.SolarFlowUpdate`.

Controls
--------

Control methods raise :class:`~solarflow_ble.SolarFlowNotReadyError`
unless the client was built with ``allow_control=True`` and the session is
``READY``.

.. code-block:: python

   from solarflow_ble import MODEL_SOLARFLOW_2400AC, AcMode

   async with SolarFlowClient(
       BleakTransport(device), allow_control=True, model=MODEL_SOLARFLOW_2400AC
   ) as client:
       await client.set_output_limit(800)
       await client.set_ac_mode(AcMode.DISCHARGING)

========================  ===================================  =============
Method                    Value                                Default range
========================  ===================================  =============
``set_input_limit``       watts                                ``0..2400``
``set_output_limit``      watts                                ``0..2400``
``set_min_soc``           percent                              ``0..50``
``set_soc``               target percent                       ``70..100``
``set_ac_mode``           ``AcMode.CHARGING`` or               ``1`` or ``2``
                          ``AcMode.DISCHARGING``
========================  ===================================  =============

Out-of-range values raise :class:`~solarflow_ble.SolarFlowValidationError`
before anything is sent. A rejected write raises
:class:`~solarflow_ble.SolarFlowCommandError`. The SOC setters take
percent, but ``state.min_soc`` and ``state.soc_set`` hold the raw
per-mille wire value, so a reported ``500`` means 50%.

Model limits
------------

The default ranges are the values verified for the SolarFlow 2400AC.
Pass ``model=`` or ``limits=SolarFlowLimits(...)`` to the constructor.
The client resolves bounds in this order: explicit ``limits``, the
:data:`~solarflow_ble.limits.MODEL_LIMITS` entry for ``model`` or for the
``productKey`` the device reports, then
:data:`~solarflow_ble.limits.DEFAULT_LIMITS` with a one-time warning. Only bounds
verified against real hardware are registered.

Connection loss
---------------

The keepalive sends a read every 30 seconds by default, so an abrupt
disconnect shows up as a failed write within one interval. Invalid JSON
and device ID mismatches also fail the session. When a session fails:

- pending calls raise :class:`~solarflow_ble.SolarFlowConnectionError`
  without waiting for the response timeout
- ``client.status`` becomes ``DISCONNECTED``
- the optional ``connection_lost_callback`` receives the exception

``client.last_error`` holds a :class:`~solarflow_ble.SolarFlowDeviceError`
for the most recent ``error`` message from the device, if any.
``connect()`` clears it. Call ``connect()`` again to start a new session.

.. toctree::
   :maxdepth: 2

   api
