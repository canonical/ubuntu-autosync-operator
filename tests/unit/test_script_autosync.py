import errno
import importlib.util
import sys
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path


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


def load_script_autosync():
    apt_pkg = types.ModuleType("apt_pkg")
    apt_pkg.init = lambda: None
    sys.modules["apt_pkg"] = apt_pkg

    debian = types.ModuleType("debian")
    debian.deb822 = object()
    sys.modules["debian"] = debian

    launchpad_module = types.ModuleType("launchpadlib.launchpad")

    class FakeLaunchpad:
        @staticmethod
        def login_with(*args, **kwargs):
            return None

    launchpad_module.Launchpad = FakeLaunchpad
    sys.modules["launchpadlib"] = types.ModuleType("launchpadlib")
    sys.modules["launchpadlib.launchpad"] = launchpad_module

    restful_errors = types.ModuleType("lazr.restfulclient.errors")

    class ServerError(Exception):
        pass

    restful_errors.ServerError = ServerError
    sys.modules["lazr"] = types.ModuleType("lazr")
    sys.modules["lazr.restfulclient"] = types.ModuleType("lazr.restfulclient")
    sys.modules["lazr.restfulclient.errors"] = restful_errors

    archive = types.ModuleType("ubuntutools.archive")
    archive.DownloadError = type("DownloadError", (Exception,), {})
    archive.SourcePackage = object
    sys.modules["ubuntutools"] = types.ModuleType("ubuntutools")
    sys.modules["ubuntutools.archive"] = archive

    lputils = types.ModuleType("lputils")
    lputils.setup_location = lambda *args, **kwargs: None
    sys.modules["lputils"] = lputils

    module_path = Path(__file__).resolve().parents[2] / "src" / "script" / "autosync"
    spec = importlib.util.spec_from_loader(
        "script_autosync", SourceFileLoader("script_autosync", str(module_path))
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def configure_script_main(monkeypatch, script_autosync, fake_log_file):
    owner = object()
    launchpad = types.SimpleNamespace(me=owner, people={"katie": object()})

    def fake_setup_location(location, default_pocket=None):
        if default_pocket is not None:
            location.series = types.SimpleNamespace(name="questing")
            location.archive = types.SimpleNamespace(owner=owner)

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
    script_autosync = load_script_autosync()
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
