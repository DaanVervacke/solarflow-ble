"""Model-aware control limits.

Only the SolarFlow 2400AC bounds are verified (confirmed by the library
owner against real hardware). Bounds that exceed a smaller model's real
capability are a safety hazard, so never add a registry entry with
guessed numbers. To add an entry:

1. Verify the model's input/output power ceilings and SOC bounds against
   real hardware or a trustworthy source.
2. Add a lowercase key to ``MODEL_LIMITS``. Keys are matched
   case-insensitively against the ``model`` constructor argument and,
   when that is not given, against the ``productKey`` the device reports
   in its messages. The 2400AC product key is registered next to its
   model key because the verified hardware reports it, and ioBroker
   maps it to the 2400 AC as well.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class SolarFlowLimits:
    """Inclusive validation bounds for SolarFlow control writes."""

    max_input_power_w: int
    max_output_power_w: int
    max_min_soc: int
    min_target_soc: int


DEFAULT_LIMITS = SolarFlowLimits(
    max_input_power_w=2400,
    max_output_power_w=2400,
    max_min_soc=50,
    min_target_soc=70,
)

MODEL_SOLARFLOW_2400AC = "solarflow-2400ac"

MODEL_SOLARFLOW_2400AC_PRODUCT_KEY = "bc8b7f"

MODEL_LIMITS: Mapping[str, SolarFlowLimits] = MappingProxyType(
    {
        MODEL_SOLARFLOW_2400AC: DEFAULT_LIMITS,
        MODEL_SOLARFLOW_2400AC_PRODUCT_KEY: DEFAULT_LIMITS,
    }
)
