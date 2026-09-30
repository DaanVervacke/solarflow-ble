API reference
=============

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

.. autodata:: solarflow_ble.DEFAULT_LIMITS

.. autodata:: solarflow_ble.MODEL_LIMITS

.. autodata:: solarflow_ble.MODEL_SOLARFLOW_2400AC

.. autodata:: solarflow_ble.MODEL_SOLARFLOW_2400AC_PRODUCT_KEY

Callbacks
---------

.. autodata:: solarflow_ble.NotificationCallback

.. autodata:: solarflow_ble.UpdateCallback

.. autodata:: solarflow_ble.ConnectionLostCallback

Protocol helpers
----------------

.. autofunction:: solarflow_ble.parse_advertisement

Exceptions
----------

.. automodule:: solarflow_ble.exceptions
   :members:
   :show-inheritance:
