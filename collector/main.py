"""Equipment data collector: OPC UA -> batches -> MQI REST API.

  * polls every mixer's BatchID, Step, ProcessTime_s and process tags
  * cuts the stream into batches (a batch ends when BatchID changes or the mixer goes IDLE)
  * posts each finished batch to POST /api/v1/equipment/runs; the server applies the FDC rules
  * store-and-forward: a batch that cannot be delivered is written to the spool folder and resent later,
    so an API restart or network break loses nothing
  * reconnects to the OPC UA server after a connection loss

Run:  python -m collector.main
Env:  OPCUA_ENDPOINT  opc.tcp://equipment:4840/mqi/
      EQUIPMENT       M1,M2
      MQI_API_URL     http://api:8000
      MQI_COLLECTOR_USER / MQI_COLLECTOR_PASSWORD   service account (role "service") used to sign in
      POLL_S          0.5
      SPOOL_DIR       ./spool
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from mqi.equipment_model import TAGS

NS_URI = "urn:mqi:equipment"
log = logging.getLogger("collector")


# ---------------------------------------------------------------- batching (protocol independent)

@dataclass
class Batch:
    lot_id: str
    equipment: str
    started: str
    samples: list[dict] = field(default_factory=list)
    last_t: float = -1.0
    complete: bool = True        # False when the collector joined after the batch had started

    def payload(self, ended: str | None = None) -> dict:
        return {"lot_id": self.lot_id, "equipment": self.equipment, "started": self.started,
                "ended": ended or datetime.now().isoformat(timespec="seconds"), "source": "opcua",
                "complete": self.complete, "samples": self.samples}


class BatchAssembler:
    """Turns polled snapshots of one mixer into finished batches."""

    def __init__(self, equipment: str, join_tolerance_s: float = 120.0):
        self.equipment = equipment
        self.join_tolerance_s = join_tolerance_s   # first sample later than this: we missed the start
        self.current: Batch | None = None

    def feed(self, batch_id: str, step: str, t: float, values: dict[str, float]) -> dict | None:
        """Add one snapshot. Returns a finished batch payload when one just ended."""
        finished = None
        running = bool(batch_id) and step != "IDLE"
        if self.current and (not running or batch_id != self.current.lot_id or t < self.current.last_t):
            finished = self._close()
        if running:
            if self.current is None:
                start = datetime.now() - timedelta(seconds=t)
                self.current = Batch(batch_id, self.equipment, start.isoformat(timespec="seconds"),
                                     complete=t <= self.join_tolerance_s)
                if not self.current.complete:
                    log.warning("%s: joined batch %s at %.0f s; it will be reported as incomplete",
                                self.equipment, batch_id, t)
            if t > self.current.last_t:
                self.current.samples += [{"t_s": t, "step": step, "tag": k, "value": v} for k, v in values.items()]
                self.current.last_t = t
        return finished

    def _close(self) -> dict | None:
        b, self.current = self.current, None
        return b.payload() if b and b.samples else None


# ---------------------------------------------------------------- delivery (store-and-forward)

class Sender:
    """Deliver payloads; spool to disk when delivery fails and retry later."""

    def __init__(self, post: Callable[[dict], Awaitable[None]], spool: Path):
        self.post, self.spool = post, Path(spool)
        self.spool.mkdir(parents=True, exist_ok=True)
        self.sent = self.spooled = 0

    def pending(self) -> int:
        return len(list(self.spool.glob("*.json")))

    def _spool(self, payload: dict) -> Path:
        f = self.spool / f"{time.time_ns()}_{uuid.uuid4().hex[:6]}.json"
        f.write_text(json.dumps(payload))
        self.spooled += 1
        return f

    async def deliver(self, payload: dict) -> bool:
        """Send in order: older spooled batches go first; if they still cannot go, queue this one behind them."""
        await self.flush()
        if self.pending():
            self._spool(payload)
            return False
        return await self.send(payload)

    async def send(self, payload: dict) -> bool:
        try:
            await self.post(payload)
            self.sent += 1
            log.info("sent %s %s (%d samples)", payload["equipment"], payload["lot_id"], len(payload["samples"]))
            return True
        except Exception as e:  # noqa: BLE001 - any failure: keep the data
            f = self._spool(payload)
            log.warning("delivery failed (%s); spooled %s", e, f.name)
            return False

    async def flush(self) -> int:
        """Resend spooled batches in order. Returns how many were delivered."""
        n = 0
        for f in sorted(self.spool.glob("*.json")):
            try:
                await self.post(json.loads(f.read_text()))
            except Exception:  # noqa: BLE001 - still down: try again later
                break
            f.unlink()
            n += 1
        if n:
            self.sent += n
            log.info("delivered %d spooled batch(es)", n)
        return n


def http_poster(api: str, timeout: float = 30, username: str | None = None,
                password: str | None = None) -> Callable[[dict], Awaitable[None]]:
    """POST runs with the collector's service account. Signs in on first use and again when the token expires;
    while sign-in fails the batches stay in the spool."""
    import httpx

    client = httpx.AsyncClient(base_url=api, timeout=timeout)
    token: dict = {}

    async def sign_in() -> None:
        r = await client.post("/api/v1/auth/token", json={"username": username, "password": password})
        if r.status_code != 200:
            raise RuntimeError(f"sign-in as {username} refused ({r.status_code})")
        token["h"] = {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def post(payload: dict) -> None:
        if username and "h" not in token:
            await sign_in()
        r = await client.post("/api/v1/equipment/runs", json=payload, headers=token.get("h"))
        if r.status_code == 401 and username:          # token expired or revoked
            await sign_in()
            r = await client.post("/api/v1/equipment/runs", json=payload, headers=token.get("h"))
        if r.status_code >= 500 or r.status_code in (408, 429):
            r.raise_for_status()             # retry later
        if r.status_code >= 400:             # the API refused the data itself: keep it, but say why
            raise RuntimeError(f"rejected {r.status_code}: {r.text[:200]}")
        res = r.json()
        if res.get("status") == "alarm":
            log.warning("FDC alarm %s %s: %s", payload["equipment"], payload["lot_id"],
                        "; ".join(a["message_en"] for a in res.get("alarms", [])))

    return post


# ---------------------------------------------------------------- OPC UA

async def poll_opcua(endpoint: str, equipment: list[str], on_batch: Callable[[dict], Awaitable[object]],
                     poll_s: float = 0.5, stop: asyncio.Event | None = None) -> None:
    """Poll the mixers until `stop` is set, reconnecting after a connection loss."""
    from asyncua import Client

    stop = stop or asyncio.Event()
    asm = {e: BatchAssembler(e) for e in equipment}
    names = ["BatchID", "Step", "ProcessTime_s", *TAGS]
    while not stop.is_set():
        try:
            async with Client(url=endpoint, timeout=10) as client:
                ns = await client.get_namespace_index(NS_URI)
                nodes = {e: [await client.nodes.objects.get_child([f"{ns}:{e}", f"{ns}:{n}"]) for n in names]
                         for e in equipment}
                log.info("connected to %s (%s)", endpoint, ", ".join(equipment))
                while not stop.is_set():
                    for e in equipment:
                        v = await client.read_values(nodes[e])
                        done = asm[e].feed(v[0], v[1], float(v[2]), dict(zip(TAGS, map(float, v[3:]))))
                        if done:
                            await on_batch(done)
                    await asyncio.sleep(poll_s)
        except Exception as e:  # noqa: BLE001 - equipment network: never give up
            if stop.is_set():
                break
            log.warning("OPC UA connection lost (%s); retrying in 5 s", e)
            await asyncio.sleep(5)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    endpoint = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://localhost:4840/mqi/")
    equipment = [e.strip() for e in os.environ.get("EQUIPMENT", "M1,M2").split(",") if e.strip()]
    api = os.environ.get("MQI_API_URL", "http://localhost:8000")
    sender = Sender(http_poster(api, username=os.environ.get("MQI_COLLECTOR_USER", "collector"),
                                password=os.environ.get("MQI_COLLECTOR_PASSWORD", "demo1234")),
                    Path(os.environ.get("SPOOL_DIR", "spool")))

    async def retry_loop():
        while True:
            await sender.flush()
            await asyncio.sleep(10)

    log.info("collector: %s -> %s", endpoint, api)
    await asyncio.gather(retry_loop(), poll_opcua(endpoint, equipment, sender.deliver,
                                                  float(os.environ.get("POLL_S", "0.5"))))


if __name__ == "__main__":
    asyncio.run(main())
