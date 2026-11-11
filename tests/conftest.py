import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """Build the synthetic plant once into a temp folder and DB."""
    from mqi import demo as d

    base = tmp_path_factory.mktemp("demo")
    folder, dbpath = base / "inbox", base / "mqi.sqlite"
    results = d.build(folder, dbpath)
    return {"folder": folder, "db": dbpath, "results": results}


@pytest.fixture(scope="session")
def bundle(demo):
    from mqi import engine

    return engine.run(demo["db"])
