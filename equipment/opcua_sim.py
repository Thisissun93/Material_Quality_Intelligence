"""OPC UA server that publishes planetary mixers running batches (accelerated time).

Address space (namespace urn:mqi:equipment):

    Objects/<mixer>/BatchID         String   lot in the mixer ("" when idle)
    Objects/<mixer>/Step            String   CHARGE | MIX | DEGAS | DISCHARGE | IDLE
    Objects/<mixer>/ProcessTime_s   Double   seconds since the batch started
    Objects/<mixer>/<tag>           Double   jacket_tc1_c, jacket_tc2_c, product_temp_c, vacuum_mbar, ...

Run:  python -m equipment.opcua_sim
Env:  SIM_ENDPOINT   opc.tcp://0.0.0.0:4840/mqi/
      SIM_MIXERS     M1,M2
      SIM_ACCEL      60        (process seconds per real second)
      SIM_LOTS       auto | UF-2611-006,UF-2611-007   (auto = newest lots from MQI_API_URL)
      SIM_FAULT_LOTS lots to run with the TC1 drift fault (default: the 2nd lot on M1)
      SIM_START_DELAY_S 10     (wait before the first batch so collectors can connect)
      MQI_API_URL    http://api:8000
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os

from asyncua import Server, ua

from mqi.equipment_model import TAGS, Fault, Mixer, Recipe

NS_URI = "urn:mqi:equipment"
log = logging.getLogger("equipment")


class MixerNode:
    def __init__(self, name: str, nodes: dict):
        self.name, self.nodes = name, nodes

    async def publish(self, batch: str, step: str, t: float, values: dict[str, float]) -> None:
        await self.nodes["BatchID"].write_value(batch)
        await self.nodes["Step"].write_value(step)
        await self.nodes["ProcessTime_s"].write_value(float(t))
        for k, v in values.items():
            await self.nodes[k].write_value(float(v))


async def build_server(endpoint: str, mixers: list[str]) -> tuple[Server, dict[str, MixerNode]]:
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("MQI equipment simulator")
    ns = await server.register_namespace(NS_URI)
    out = {}
    for m in mixers:
        obj = await server.nodes.objects.add_object(ns, m)
        nodes = {
            "BatchID": await obj.add_variable(ns, "BatchID", "", varianttype=ua.VariantType.String),
            "Step": await obj.add_variable(ns, "Step", "IDLE", varianttype=ua.VariantType.String),
            "ProcessTime_s": await obj.add_variable(ns, "ProcessTime_s", 0.0, varianttype=ua.VariantType.Double),
        }
        for tag in TAGS:
            nodes[tag] = await obj.add_variable(ns, tag, 0.0, varianttype=ua.VariantType.Double)
        out[m] = MixerNode(m, nodes)
    return server, out


async def run_mixer(node: MixerNode, lots, faults: set[str], accel: float, tick: float = 0.2,
                    idle_s: float = 2.0, recipe: Recipe | None = None, batches: int | None = None) -> None:
    """Run batches back to back: publish every tick, advancing the model by tick * accel process seconds."""
    done = 0
    for lot in lots:
        m = Mixer(lot, node.name, recipe or Recipe(), Fault() if lot in faults else None)
        log.info("%s: start batch %s%s", node.name, lot, " (TC1 drift)" if lot in faults else "")
        while not m.done:
            await node.publish(lot, m.step, m.t, m.values())
            m.advance(tick * accel)
            await asyncio.sleep(tick)
        await node.publish("", "IDLE", m.t, {k: 0.0 for k in TAGS})
        log.info("%s: end batch %s", node.name, lot)
        done += 1
        if batches and done >= batches:
            return
        await asyncio.sleep(idle_s)


def lot_stream(first: list[str], prefix: str):
    """The given lots once, then simulator-only lot numbers (so real lots do not collect endless runs)."""
    yield from first
    for i in itertools.count(1):
        yield f"{prefix}-{i:04d}"


def _auto_lots(api: str, n: int = 6) -> list[str]:
    try:
        import httpx

        r = httpx.get(f"{api}/api/v1/lots", params={"limit": n}, timeout=10)
        r.raise_for_status()
        return [x["lot_id"] for x in r.json()]
    except Exception as e:  # noqa: BLE001 - simulator keeps running with its own lot numbers
        log.warning("could not read lots from %s (%s); using SIM lot numbers", api, e)
        return []


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    endpoint = os.environ.get("SIM_ENDPOINT", "opc.tcp://0.0.0.0:4840/mqi/")
    mixers = [m.strip() for m in os.environ.get("SIM_MIXERS", "M1,M2").split(",") if m.strip()]
    accel = float(os.environ.get("SIM_ACCEL", "60"))
    spec = os.environ.get("SIM_LOTS", "auto")
    lots = _auto_lots(os.environ.get("MQI_API_URL", "http://localhost:8000")) if spec == "auto" else \
        [x.strip() for x in spec.split(",") if x.strip()]
    lots = lots or [f"SIM-{i:04d}" for i in range(1, 7)]
    per = {m: lots[i::len(mixers)] for i, m in enumerate(mixers)}
    fault_env = os.environ.get("SIM_FAULT_LOTS")
    faults = {x.strip() for x in fault_env.split(",")} if fault_env is not None else \
        set(per[mixers[0]][1:2])
    server, nodes = await build_server(endpoint, mixers)
    log.info("OPC UA server %s, mixers %s, accel x%.0f, lots %s, fault %s", endpoint, mixers, accel, per, faults)
    async with server:
        await asyncio.sleep(float(os.environ.get("SIM_START_DELAY_S", "10")))
        await asyncio.gather(*(run_mixer(nodes[m], lot_stream(per[m], f"SIM-{m}"), faults, accel) for m in mixers))


if __name__ == "__main__":
    asyncio.run(main())
