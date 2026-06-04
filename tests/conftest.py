import pytest


@pytest.fixture
def clarm_home(tmp_path, monkeypatch):
    """Point clarm at an isolated CLARM_HOME for the duration of a test.

    All path helpers in :mod:`clarm.paths` read ``CLARM_HOME`` live on each
    call, so setting the env var is enough to fully sandbox the store, pidfile
    and logs under ``tmp_path``.
    """
    monkeypatch.setenv("CLARM_HOME", str(tmp_path))
    return tmp_path
