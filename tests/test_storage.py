from datetime import datetime, time

from clarm import storage
from clarm import models as m
from clarm.timeparse import parse_repeat

NOW = datetime(2026, 6, 4, 10, 0)


def test_empty_store_loads_empty(clarm_home):
    assert storage.load() == []


def test_add_allocates_sequential_ids(clarm_home):
    a = storage.add(lambda i: m.make_once(i, datetime(2026, 6, 4, 23, 0), NOW))
    b = storage.add(lambda i: m.make_once(i, datetime(2026, 6, 4, 23, 5), NOW))
    assert (a.id, b.id) == (1, 2)
    assert [x.id for x in storage.load()] == [1, 2]


def test_roundtrip_persists_to_disk(clarm_home):
    storage.add(lambda i: m.make_recurring(i, time(7, 0), parse_repeat("mon-fri"), NOW, "gym"))
    loaded = storage.load()
    assert len(loaded) == 1
    assert loaded[0].label == "gym"
    assert loaded[0].days == parse_repeat("mon-fri")
    # The on-disk file really exists and is valid JSON.
    assert (clarm_home / "alarms.json").exists()


def test_update_mutates_and_saves(clarm_home):
    storage.add(lambda i: m.make_once(i, datetime(2026, 6, 4, 23, 0), NOW, "x"))

    def disable_all(alarms):
        for a in alarms:
            a.enabled = False
        return len(alarms)

    n = storage.update(disable_all)
    assert n == 1
    assert storage.load()[0].enabled is False


def test_remove_via_update(clarm_home):
    storage.add(lambda i: m.make_once(i, datetime(2026, 6, 4, 23, 0), NOW))
    storage.add(lambda i: m.make_once(i, datetime(2026, 6, 4, 23, 5), NOW))

    def remove_id(alarms):
        alarms[:] = [a for a in alarms if a.id != 1]

    storage.update(remove_id)
    assert [a.id for a in storage.load()] == [2]


def test_corrupt_file_is_treated_as_empty(clarm_home):
    (clarm_home / "alarms.json").write_text("{ this is not valid json")
    assert storage.load() == []


def test_id_reuse_after_delete_picks_max_plus_one(clarm_home):
    storage.add(lambda i: m.make_once(i, datetime(2026, 6, 4, 23, 0), NOW))
    storage.add(lambda i: m.make_once(i, datetime(2026, 6, 4, 23, 5), NOW))
    storage.update(lambda alarms: alarms.__setitem__(slice(None), [a for a in alarms if a.id != 2]))
    # max id remaining is 1 -> next is 2 again (simple, predictable scheme).
    c = storage.add(lambda i: m.make_once(i, datetime(2026, 6, 4, 23, 9), NOW))
    assert c.id == 2
