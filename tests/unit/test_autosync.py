# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Unit tests for `src/autosync.py`."""

from subprocess import CalledProcessError
from unittest.mock import Mock

import pytest

import autosync
from autosync import AutoSync


def test_install_packages_calls_apt_update_before_adding_packages(monkeypatch):
    called = []

    monkeypatch.setattr(autosync.apt, "update", lambda: called.append("update"))
    monkeypatch.setattr(autosync.apt, "add_package", lambda pkg: called.append(pkg))
    worker = AutoSync()

    worker._install_packages()

    assert called[0] == "update"
    assert set(called[1:]) == set(autosync.PACKAGES)


def test_install_creates_directories_and_copies_scripts(monkeypatch):
    monkeypatch.setattr(AutoSync, "_install_packages", lambda self: None)

    ops = []
    monkeypatch.setattr(
        autosync.os, "makedirs", lambda dir_path, exist_ok=True: ops.append(("makedirs", dir_path))
    )
    monkeypatch.setattr(
        autosync.shutil, "chown", lambda path, u, g: ops.append(("chown", path, u, g))
    )
    monkeypatch.setattr(autosync.shutil, "copy", lambda src, dst: ops.append(("copy", src, dst)))
    monkeypatch.setattr(autosync.Path, "unlink", lambda self, missing_ok=True: None)
    monkeypatch.setattr(autosync.os, "chmod", lambda path, mode: ops.append(("chmod", str(path))))

    worker = AutoSync()
    worker.install()

    assert ("copy", "src/script/autosync", "/usr/bin/autosync") in ops
    assert ("copy", "src/script/lputils.py", "/usr/bin/lputils.py") in ops
    assert ("copy", "src/script/run-autosync", autosync.AUTOSYNC_RUNNER_PATH) in ops
    assert ("copy", "src/nginx/autosync.conf", autosync.NGINX_SITE_CONFIG_PATH) in ops


def test_start_restarts_nginx_and_starts_autosync_service(monkeypatch):
    calls = []
    monkeypatch.setattr(
        autosync.systemd,
        "service_restart",
        lambda *args: calls.append(("restart",) + args),
    )
    monkeypatch.setattr(
        autosync.systemd,
        "service_start",
        lambda *args: calls.append(("start",) + args),
    )

    worker = AutoSync()
    worker.start()

    assert ("restart", "nginx") in calls
    assert ("start", "autosync.service", "--no-block") in calls


def test_run_sync_starts_service(monkeypatch):
    starts = []
    monkeypatch.setattr(autosync.systemd, "service_start", lambda *args: starts.append(args))

    worker = AutoSync()
    worker.run_sync()

    assert ("autosync.service",) in starts


def test_configure_run_writes_script_with_dry_run(monkeypatch):
    monkeypatch.setattr(
        autosync.Path,
        "read_text",
        lambda self, encoding=None: "#!/bin/sh\nexec autosync __AUTOSYNC_ARGS__\n",
    )

    written = {}

    def fake_write_text(self, text, encoding=None):
        written[str(self)] = text

    monkeypatch.setattr(autosync.Path, "write_text", fake_write_text)
    monkeypatch.setattr(autosync.os, "chmod", lambda path, mode: None)

    worker = AutoSync()
    worker.configure_run(True)

    path = str(autosync.AUTOSYNC_RUNNER_PATH)
    assert "--dry-run" in written[path]
    assert "--log-directory /srv/autosync/www/autosync --batch" in written[path]


def test_setup_systemd_unit_writes_service_and_timer_with_proxy_environment(monkeypatch):
    monkeypatch.setenv("JUJU_CHARM_HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("JUJU_CHARM_HTTPS_PROXY", "https://secure.example:8443")

    worker = AutoSync()

    def fake_read_text(self, encoding=None):
        return "[Service]\nExecStart=/bin/true" if self.suffix == ".service" else "[Timer]"

    written = {}

    def fake_write_text(self, text, encoding=None):
        written[str(self)] = text

    monkeypatch.setattr(autosync.Path, "read_text", fake_read_text)
    monkeypatch.setattr(autosync.Path, "write_text", fake_write_text)
    monkeypatch.setattr(autosync.Path, "mkdir", lambda self, parents=False, exist_ok=False: None)
    monkeypatch.setattr(autosync.systemd, "service_enable", lambda *args, **kwargs: None)
    monkeypatch.setattr(autosync.systemd, "daemon_reload", lambda *args, **kwargs: None)
    monkeypatch.setattr(autosync.systemd, "service_restart", lambda *args, **kwargs: None)

    worker.setup_systemd_unit()

    svc_path = "/etc/systemd/system/autosync.service"
    assert svc_path in written
    assert "Environment=HTTP_PROXY=http://proxy.example:8080" in written[svc_path]
    assert "Environment=HTTPS_PROXY=https://secure.example:8443" in written[svc_path]


def test_configure_schedule_writes_timer_and_restarts_timer(monkeypatch):
    written = {}
    calls = []

    def fake_write_text(self, text, encoding=None):
        written[str(self)] = text

    monkeypatch.setattr(autosync.Path, "write_text", fake_write_text)
    monkeypatch.setattr(
        autosync.systemd,
        "daemon_reload",
        lambda *args: calls.append(("reload",) + args),
    )
    monkeypatch.setattr(
        autosync.systemd, "service_restart", lambda *args: calls.append(("restart",) + args)
    )

    worker = AutoSync()
    worker.configure_schedule("6,18")

    timer_path = "/etc/systemd/system/autosync.timer"
    assert timer_path in written
    assert "OnCalendar=*-*-* 06:00:00" in written[timer_path]
    assert "OnCalendar=*-*-* 18:00:00" in written[timer_path]
    assert ("reload",) in calls
    assert ("restart", "autosync.timer") in calls


def test_configure_schedule_raises_for_empty_schedule(monkeypatch):
    worker = AutoSync()

    with pytest.raises(ValueError):
        worker.configure_schedule(" , ")


def test_configure_schedule_raises_for_out_of_range_hour(monkeypatch):
    worker = AutoSync()

    with pytest.raises(ValueError):
        worker.configure_schedule("5,25")


def test_setup_systemd_unit_raises_when_enable_fails(monkeypatch):
    monkeypatch.setattr(autosync.Path, "read_text", lambda self, encoding=None: "[Service]")
    monkeypatch.setattr(autosync.Path, "write_text", lambda self, t, encoding=None: None)
    monkeypatch.setattr(autosync.Path, "mkdir", lambda self, parents=True, exist_ok=True: None)

    def bad_enable(*args, **kwargs):
        raise CalledProcessError(3, "systemctl")

    monkeypatch.setattr(autosync.systemd, "service_enable", bad_enable)
    worker = AutoSync()

    with pytest.raises(CalledProcessError):
        worker.setup_systemd_unit()


def test_install_packages_raises_when_package_not_found(monkeypatch):
    monkeypatch.setattr(autosync.apt, "update", lambda: None)

    def bad_add(_):
        raise autosync.PackageNotFoundError("missing")

    monkeypatch.setattr(autosync.apt, "add_package", bad_add)
    worker = AutoSync()

    with pytest.raises(autosync.PackageNotFoundError):
        worker._install_packages()


def test_install_packages_raises_when_package_installation_fails(monkeypatch):
    monkeypatch.setattr(autosync.apt, "update", lambda: None)

    def bad_add(_):
        raise autosync.PackageError("install failed")

    monkeypatch.setattr(autosync.apt, "add_package", bad_add)
    worker = AutoSync()

    with pytest.raises(autosync.PackageError):
        worker._install_packages()


def test_start_raises_when_systemd_start_fails(monkeypatch):
    monkeypatch.setattr(autosync.systemd, "service_restart", lambda *args: None)
    monkeypatch.setattr(
        autosync.systemd,
        "service_start",
        Mock(side_effect=CalledProcessError(1, "systemctl")),
    )

    worker = AutoSync()

    with pytest.raises(CalledProcessError):
        worker.start()
