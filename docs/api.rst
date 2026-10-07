API reference
=============

Import every name on this page from ``solarflow_ble``.

Client
------

.. autoclass:: solarflow_ble.SolarFlowClient
   :members:
   :special-members: __init__

Transports
----------

.. autoclass:: solarflow_ble.BleTransport
   :members:

.. autoclass:: solarflow_ble.BleakTransport
   :members:
   :special-members: __init__

Models
------

.. autoclass:: solarflow_ble.AcMode
   :members:

.. autoclass:: solarflow_ble.ConnectionStatus
   :members:

.. autoclass:: solarflow_ble.BatteryPack
   :members:

.. autoclass:: solarflow_ble.Advertisement
   :members:

.. autoclass:: solarflow_ble.SolarFlowState
   :members:

.. autoclass:: solarflow_ble.SolarFlowUpdate
   :members:

Control limits
--------------

.. autoclass:: solarflow_ble.SolarFlowLimits
   :members:

.. autodata:: solarflow_ble.limits.DEFAULT_LIMITS

.. autodata:: solarflow_ble.limits.MODEL_LIMITS

.. autodata:: solarflow_ble.limits.MODEL_SOLARFLOW_2400AC

.. autodata:: solarflow_ble.limits.MODEL_SOLARFLOW_2400AC_PRODUCT_KEY

Callbacks
---------

.. autodata:: solarflow_ble.client.NotificationCallback

.. autodata:: solarflow_ble.client.UpdateCallback

.. autodata:: solarflow_ble.client.ConnectionLostCallback

Protocol helpers
----------------

.. autofunction:: solarflow_ble.parse_advertisement

Exceptions
----------

.. autoclass:: solarflow_ble.SolarFlowError
   :members:
   :show-inheritance:

.. autoclass:: solarflow_ble.SolarFlowCommandError
   :members:
   :show-inheritance:

.. autoclass:: solarflow_ble.SolarFlowConnectionError
   :members:
   :show-inheritance:

.. autoclass:: solarflow_ble.SolarFlowDeviceError
   :members:
   :show-inheritance:

.. autoclass:: solarflow_ble.SolarFlowNotReadyError
   :members:
   :show-inheritance:

.. autoclass:: solarflow_ble.SolarFlowProtocolError
   :members:
   :show-inheritance:

.. autoclass:: solarflow_ble.SolarFlowTimeoutError
   :members:
   :show-inheritance:

.. autoclass:: solarflow_ble.SolarFlowValidationError
   :members:
   :show-inheritance:
