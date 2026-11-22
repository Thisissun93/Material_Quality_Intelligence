"""The demo plant must build safely when several sessions open the app at once (Streamlit Cloud)."""
import shutil
import threading
import time

from mqi import demo


def test_replace_dir_swaps_contents(tmp_path):
    folder = tmp_path / "demo_inbox"
    folder.mkdir()
    (folder / "old.csv").write_text("old")
    new = tmp_path / ".demo_inbox-new-x"
    new.mkdir()
    (new / "new.csv").write_text("new")

    demo._replace_dir(new, folder)

    assert [p.name for p in folder.iterdir()] == ["new.csv"]
    assert not new.exists()
    assert not list(tmp_path.glob(".demo_inbox-old-*"))


def test_replace_dir_survives_a_folder_it_cannot_delete(tmp_path, monkeypatch):
    """The Cloud traceback: rmtree raised OSError. The new files must still land in place."""
    folder = tmp_path / "demo_inbox"
    folder.mkdir()
    (folder / "old.csv").write_text("old")
    new = tmp_path / ".demo_inbox-new-x"
    new.mkdir()
    (new / "new.csv").write_text("new")

    real = shutil.rmtree

    def stubborn(path, ignore_errors=False, *a, **k):
        if ignore_errors:
            return None          # pretend a file is still held open: nothing removed, no error
        return real(path, *a, **k)

    monkeypatch.setattr(demo.shutil, "rmtree", stubborn)
    demo._replace_dir(new, folder)

    assert [p.name for p in folder.iterdir()] == ["new.csv"]
    assert len(list(tmp_path.glob(".demo_inbox-old-*"))) == 1   # left aside, retried next build


def test_concurrent_sessions_build_once(tmp_path, monkeypatch):
    built = []
    state = {"exists": False}

    def slow_build(folder, dbpath, ingest):
        time.sleep(0.2)
        built.append(folder)
        state["exists"] = True
        return []

    monkeypatch.setattr(demo, "_build", slow_build)
    monkeypatch.setattr(demo.db, "exists", lambda path=None: state["exists"])
    monkeypatch.setattr(demo, "is_stale", lambda path=None: False)

    results = []
    threads = [threading.Thread(target=lambda: results.append(demo.build_if_needed())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(built) == 1
    assert sorted(results) == [False, False, False, True]


def test_build_writes_files_without_ingest(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "stale.csv").write_text("left from an older demo")

    assert demo.build(folder, tmp_path / "x.sqlite", ingest=False) == []

    names = {p.name for p in folder.iterdir()}
    assert "stale.csv" not in names and names
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".inbox-")]


def _clone(demo_store, tmp_path):
    from conftest import clone_store

    return clone_store(demo_store["db"], tmp_path)


def test_complete_demo_is_not_stale(demo, tmp_path):
    store = _clone(demo, tmp_path)
    assert demo_mod().is_stale(store) is False


def test_interrupted_build_marker_is_stale(demo, tmp_path):
    from mqi import db

    store = _clone(demo, tmp_path)
    db.set_meta("demo_version", "building", store)
    assert demo_mod().is_stale(store) is True


def test_demo_lots_without_marker_are_stale(demo, tmp_path):
    """What the Cloud app was left with: demo lots, no marker -> sign-in wall instead of the demo."""
    from mqi import db

    store = _clone(demo, tmp_path)
    with db.connect(store) as con:
        con.execute("DELETE FROM meta WHERE key=?", ("demo_version",))
    assert db.get_meta("demo_version", store) is None
    assert demo_mod().is_stale(store) is True


def test_plant_data_is_never_stale(demo, tmp_path):
    """A store with any lot outside the demo plant is plant data: never rebuilt over."""
    from mqi import db

    store = _clone(demo, tmp_path)
    with db.connect(store) as con:
        con.execute("DELETE FROM meta WHERE key=?", ("demo_version",))
        con.execute("INSERT INTO lots (lot_id, material) VALUES (?, ?)", ("PLANT-0001", "product"))
    assert demo_mod().is_stale(store) is False


def demo_mod():
    from mqi import demo as d

    return d
