import errno
import importlib.util
import io
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "src" / "script" / "autosync"


class FakeStdout:
    def detach(self):
        return self

    def writable(self):
        return True

    def seekable(self):
        return False

    def readable(self):
        return False

    @property
    def closed(self):
        return False

    def flush(self):
        return None

    def close(self):
        return None


class FakeLogFile:
    def __init__(self):
        self.closed = False

    def write(self, data):
        return len(data)

    def flush(self):
        return None

    def close(self):
        self.closed = True


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

    class FakeLaunchpad:
        @staticmethod
        def login_with(*args, **kwargs):
            return None

    launchpadlib_launchpad.Launchpad = FakeLaunchpad
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

    lputils = ModuleType("lputils")
    lputils.setup_location = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "lputils", lputils)

    loader = SourceFileLoader("script_autosync", str(SCRIPT_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def configure_script_main(monkeypatch, script_autosync, fake_log_file):
    owner = object()
    launchpad = SimpleNamespace(me=owner, people={"katie": object()})

    def fake_setup_location(location, default_pocket=None):
        if default_pocket is not None:
            location.series = SimpleNamespace(name="questing")
            location.archive = SimpleNamespace(owner=owner, participants=[])

    monkeypatch.setattr(script_autosync.sys, "stdout", FakeStdout())
    monkeypatch.setattr(script_autosync.sys, "argv", ["autosync", "--log-directory", "/logs"])
    monkeypatch.setattr(script_autosync.apt_pkg, "init", lambda: None)
    monkeypatch.setattr(
        script_autosync.Launchpad,
        "login_with",
        lambda *args, **kwargs: launchpad,
    )
    monkeypatch.setattr(script_autosync.lputils, "setup_location", fake_setup_location)
    monkeypatch.setattr(script_autosync, "sync_differences", lambda options: None)
    monkeypatch.setattr(script_autosync.time, "gmtime", lambda: object())
    monkeypatch.setattr(
        script_autosync.time,
        "strftime",
        lambda fmt, now=None: {"%F": "2026-09-25", "%T": "07:34:30"}[fmt],
    )
    monkeypatch.setattr(script_autosync.os.path, "isdir", lambda path: True)
    monkeypatch.setattr(script_autosync.os, "makedirs", lambda path: None)
    monkeypatch.setattr(
        script_autosync,
        "open",
        lambda *args, **kwargs: fake_log_file,
        raising=False,
    )


def test_main_updates_top_level_current_log_symlink(monkeypatch):
    """Regression test for LP: #2129941 after PR #75 moved logs under series."""
    script_autosync = load_script_module(monkeypatch)
    fake_log_file = FakeLogFile()

    configure_script_main(monkeypatch, script_autosync, fake_log_file)

    symlink_calls = []
    monkeypatch.setattr(
        script_autosync.os,
        "unlink",
        lambda path: (_ for _ in ()).throw(OSError(errno.ENOENT, "missing")),
    )
    monkeypatch.setattr(
        script_autosync.os,
        "symlink",
        lambda target, link_name: symlink_calls.append((target, link_name)),
    )

    script_autosync.main()

    assert fake_log_file.closed
    assert symlink_calls == [
        (
            "questing/2026-09-25/07:34:30.log",
            "/logs/current.log",
        )
    ]


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
