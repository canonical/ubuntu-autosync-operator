import importlib.util
import io
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "src" / "script" / "autosync"


def load_script_module(monkeypatch):
    apt_pkg = ModuleType("apt_pkg")
    apt_pkg.init = lambda: None
    apt_pkg.TagFile = object
    apt_pkg.parse_depends = lambda value: []
    apt_pkg.version_compare = lambda left, right: 0
    monkeypatch.setitem(sys.modules, "apt_pkg", apt_pkg)

    debian = ModuleType("debian")
    debian.deb822 = object()
    monkeypatch.setitem(sys.modules, "debian", debian)

    launchpadlib = ModuleType("launchpadlib")
    launchpadlib_launchpad = ModuleType("launchpadlib.launchpad")
    launchpadlib_launchpad.Launchpad = type("Launchpad", (), {})
    monkeypatch.setitem(sys.modules, "launchpadlib", launchpadlib)
    monkeypatch.setitem(sys.modules, "launchpadlib.launchpad", launchpadlib_launchpad)

    lazr = ModuleType("lazr")
    restfulclient = ModuleType("lazr.restfulclient")
    restfulclient_errors = ModuleType("lazr.restfulclient.errors")
    restfulclient_errors.ServerError = type("ServerError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "lazr", lazr)
    monkeypatch.setitem(sys.modules, "lazr.restfulclient", restfulclient)
    monkeypatch.setitem(sys.modules, "lazr.restfulclient.errors", restfulclient_errors)

    ubuntutools = ModuleType("ubuntutools")
    ubuntutools_archive = ModuleType("ubuntutools.archive")
    ubuntutools_archive.DownloadError = type("DownloadError", (Exception,), {})
    ubuntutools_archive.SourcePackage = object
    monkeypatch.setitem(sys.modules, "ubuntutools", ubuntutools)
    monkeypatch.setitem(sys.modules, "ubuntutools.archive", ubuntutools_archive)

    monkeypatch.setitem(sys.modules, "lputils", ModuleType("lputils"))

    loader = SourceFileLoader("script_autosync", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_run_sync_differences_logs_traceback_to_stdout_and_flushes(monkeypatch):
    script_autosync = load_script_module(monkeypatch)

    class TrackingStdout(io.StringIO):
        def __init__(self):
            super().__init__()
            self.flushed = False

        def flush(self):
            self.flushed = True
            super().flush()

    output = TrackingStdout()
    options = SimpleNamespace(log_directory="/tmp/autosync-logs")

    def raise_boom(_):
        raise ValueError("boom")

    monkeypatch.setattr(script_autosync, "sync_differences", raise_boom)
    monkeypatch.setattr(script_autosync.sys, "stdout", output)

    with pytest.raises(ValueError, match="boom"):
        script_autosync.run_sync_differences(options)

    logged_output = output.getvalue()
    assert "Sync run failed with an unhandled exception:" in logged_output
    assert "Traceback (most recent call last):" in logged_output
    assert "ValueError: boom" in logged_output
    assert output.flushed is True
