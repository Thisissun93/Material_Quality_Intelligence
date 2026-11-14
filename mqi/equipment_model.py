"""Planetary mixer batch model: what the equipment would report, second by second.

Used by the OPC UA simulator (live, accelerated) and by the demo (replayed traces), so both produce
the same physics. Recipe: CHARGE -> MIX -> DEGAS (heated, under vacuum) -> DISCHARGE.

The jacket temperature is controlled on TC1. A second thermocouple (TC2) only monitors. With the
"tc1_drift" fault TC1 reads `drift` degC low, so the controller holds TC1 on set point while the real
jacket, TC2 and the product run `drift` degC hot. Every single-sensor chart looks normal.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TAGS = ["jacket_tc1_c", "jacket_tc2_c", "product_temp_c", "vacuum_mbar", "agitator_rpm", "motor_current_a"]


@dataclass
class Recipe:
    mix_min: float = 45.0
    degas_min: float = 25.0
    degas_temp: float = 60.0
    vacuum: float = 5.0
    batch_kg: float = 200.0
    charge_min: float = 10.0
    discharge_min: float = 10.0

    def steps(self) -> list[tuple[str, float]]:
        return [("CHARGE", self.charge_min), ("MIX", self.mix_min), ("DEGAS", self.degas_min),
                ("DISCHARGE", self.discharge_min)]

    @property
    def total_s(self) -> float:
        return 60 * sum(m for _, m in self.steps())


@dataclass
class Fault:
    kind: str = "tc1_drift"
    drift: float = 5.0


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


@dataclass
class Mixer:
    """State machine that advances in process seconds; values() gives the current tag readings."""
    lot_id: str
    equipment: str = "M1"
    recipe: Recipe = field(default_factory=Recipe)
    fault: Fault | None = None
    t: float = 0.0

    def __post_init__(self):
        self.rng = np.random.default_rng(_seed(self.lot_id, self.equipment))
        self.jacket = 25.0      # true jacket temperature
        self.tc1 = 25.0         # controlled reading
        self.product = 24.0
        self.vac = 1013.0

    @property
    def step(self) -> str:
        acc = 0.0
        for name, m in self.recipe.steps():
            acc += 60 * m
            if self.t < acc:
                return name
        return "IDLE"

    @property
    def done(self) -> bool:
        return self.t >= self.recipe.total_s

    def advance(self, dt: float) -> None:
        r, step = self.recipe, self.step
        bias = self.fault.drift if (self.fault and self.fault.kind == "tc1_drift") else 0.0
        if step == "DEGAS":
            sp = r.degas_temp
            # controller drives the reading (TC1 = jacket - bias) to set point
            target_jacket = sp + bias
            self.jacket += (target_jacket - self.jacket) * (1 - np.exp(-dt / 150))
            self.product += (self.jacket - self.product) * (1 - np.exp(-dt / 300))
            self.vac += (r.vacuum - self.vac) * (1 - np.exp(-dt / 90))
        elif step == "MIX":
            self.jacket += (30 - self.jacket) * (1 - np.exp(-dt / 300))
            self.product += (35 - self.product) * (1 - np.exp(-dt / 900))
            self.vac += (1013 - self.vac) * (1 - np.exp(-dt / 30))
        elif step == "DISCHARGE":
            self.jacket += (40 - self.jacket) * (1 - np.exp(-dt / 600))
            self.product += (45 - self.product) * (1 - np.exp(-dt / 900))
            self.vac += (1013 - self.vac) * (1 - np.exp(-dt / 20))
        else:  # CHARGE
            self.product += (27 - self.product) * (1 - np.exp(-dt / 600))
        self.tc1 = self.jacket - bias
        self.t += dt

    def values(self) -> dict[str, float]:
        n, step, r = self.rng.normal, self.step, self.recipe
        rpm = {"CHARGE": 20, "MIX": 60, "DEGAS": 15, "DISCHARGE": 0}.get(step, 0)
        cur = {"CHARGE": 4, "MIX": 11 + 2 * r.batch_kg / 400, "DEGAS": 6, "DISCHARGE": 1}.get(step, 0)
        return {
            "jacket_tc1_c": round(self.tc1 + n(0, 0.15), 2),
            "jacket_tc2_c": round(self.jacket + n(0, 0.15), 2),
            "product_temp_c": round(self.product + n(0, 0.2), 2),
            "vacuum_mbar": round(max(self.vac + n(0, 0.08 if self.vac < 50 else 2), 0.5), 2),
            "agitator_rpm": round(max(rpm + n(0, 0.5), 0), 1) if rpm else 0.0,
            "motor_current_a": round(max(cur + n(0, 0.4), 0), 2) if cur else 0.0,
        }


def batch_trace(lot_id: str, equipment: str = "M1", recipe: Recipe | None = None, fault: Fault | None = None,
                dt: float = 30.0) -> pd.DataFrame:
    """Whole batch as a long table: t_s, step, tag, value (one row per tag per sample)."""
    m = Mixer(lot_id, equipment, recipe or Recipe(), fault)
    rows = []
    while not m.done:
        step, vals = m.step, m.values()
        rows += [(m.t, step, k, v) for k, v in vals.items()]
        m.advance(dt)
    return pd.DataFrame(rows, columns=["t_s", "step", "tag", "value"])
