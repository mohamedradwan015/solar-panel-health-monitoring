"""Configuration objects. Every tunable value lives here and is saved with the artifacts."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict


@dataclass
class DetectorConfig:
    # --- KNN ---
    k: int = 10
    threshold_percentile: float = 99.0      # of out-of-group scores on trusted-normal reference
    scaler: str = "robust"                  # "robust" | "standard"
    level: str = "channel"                  # "channel" | "satellite"
    # --- features ---
    feature_set: str = "rel"                # "abs" (own V,I only) | "rel" (adds sibling-relative + temporal)
    window_seconds: float = 180.0           # causal rolling window (spin period is ~120 s at 3 deg/s)
    current_softening_mA: float = 4.66      # one ADC count of the panel current sensor (observed in data)
    use_temperature: bool = False           # paper (Sec 2.4) found temperature weakly correlated; off by default
    use_battery: bool = False
    # --- reference handling ---
    max_reference: int | None = None        # None = keep every trusted-normal observation
    random_state: int = 0
    min_reference: int = 200

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class RuleLabelConfig:
    """Rule-derived labels reproduced from Jara Cespedes et al. 2022 (Appl. Sci. 12, 8634), Sec. 3.1.
    These are *published rules*, not independently verified fault labels."""
    type1_voltage_below_mV: float = 1200.0
    type2_voltage_low_mV: float = 2000.0
    type2_voltage_high_mV: float = 3000.0
    type2_current_below_mA: float = 50.0
    type2_min_consecutive: int = 3
