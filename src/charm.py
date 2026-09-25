#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Charmed Operator for Ubuntu Auto Sync."""

import logging
import shutil
import socket
from subprocess import CalledProcessError, SubprocessError

import ops
from charmlibs.apt import PackageError, PackageNotFoundError
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer as IngressRequirer

from autosync import DEFAULT_SYNC_HOURS, AutoSync

logger = logging.getLogger(__name__)

PORT = 80


class UbuntuAutoSyncCharm(ops.CharmBase):
    """Charmed Operator for Ubuntu Auto Sync."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self.ingress = IngressRequirer(self, port=PORT, strip_prefix=True, relation_name="ingress")

        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.upgrade_charm, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.sync_now_action, self._on_sync_now)

        framework.observe(self.ingress.on.ready, self._on_config_changed)
        framework.observe(self.ingress.on.revoked, self._on_config_changed)

        self._autosync = AutoSync()

    @property
    def _lpuser_secret(self) -> ops.model.Secret | None:
        secret_id: str = ""

        try:
            secret_id = str(self.config["lpuser_secret_id"])
        except KeyError:
            logger.warning("lpuser_secret_id config not available, unable to extract keys.")
            return None

        try:
            return self.model.get_secret(id=secret_id)
        except (ops.SecretNotFoundError, ops.model.ModelError):
            logger.warning("Failed to get lpuser secret with id %s", secret_id)

        return None

    @property
    def _lpuser_lp_oauthkey(self) -> str | None:
        secret = self._lpuser_secret

        if secret is not None:
            logger.debug("config - got secret id %s, returning key lpoauthkey", secret)
            try:
                return secret.get_content(refresh=True)["lpoauthkey"]
            except KeyError:
                logger.warning("lpoauthkey not found in lpuser secret.")

        return None

    def _on_install(self, event: ops.EventBase):
        """Handle install and upgrade events."""
        self.unit.status = ops.MaintenanceStatus("Setting up environment")
        try:
            self._autosync.install()
            self._autosync.setup_systemd_units()
            self._autosync.configure_run(bool(self.config.get("dry_run", False)))
            self._autosync.configure_schedule(
                str(self.config.get("sync_hours", "")).strip() or DEFAULT_SYNC_HOURS
            )
        except (
            CalledProcessError,
            SubprocessError,
            PackageError,
            PackageNotFoundError,
            ValueError,
            IOError,
            OSError,
            shutil.Error,
        ) as e:
            logger.warning("Failed to set up the environment: %s", e)
            self.unit.status = ops.BlockedStatus(
                "Failed to set up the environment. Check `juju debug-log` for details."
            )
            return
        self.unit.status = ops.ActiveStatus()

    def _on_start(self, event: ops.StartEvent):
        """Start nginx and trigger an initial autosync run."""
        self.unit.status = ops.MaintenanceStatus("Starting Auto Sync")
        try:
            self._autosync.start()
        except CalledProcessError:
            self.unit.status = ops.BlockedStatus(
                "Failed to start services. Check `juju debug-log` for details."
            )
            return
        self.unit.set_ports(PORT)
        self.unit.status = ops.ActiveStatus()

    def _on_config_changed(self, event):
        """Update configuration."""
        logger.debug("config changed event")
        self.unit.status = ops.MaintenanceStatus("Updating configuration")
        try:
            self._autosync.configure_url(self._get_external_url())
        except ValueError:
            self.unit.status = ops.BlockedStatus(
                "Invalid configuration. Check `juju debug-log` for details."
            )
            return False
        logger.debug("config change done - url set")

        lp_key_data = self._lpuser_lp_oauthkey
        if lp_key_data is None:
            logger.warning("Launchpad credentials unavailable, unable to autosync.")
            self.unit.status = ops.BlockedStatus("Launchpad oauth token config missing.")
            return False
        else:
            logger.debug("config - got lpoauthkey (length %d)", len(lp_key_data))
            if not self._autosync.configure_lpoauthkey(lp_key_data):
                self.unit.status = ops.BlockedStatus("Failed to update Launchpad oauth token.")
                return False
        logger.debug("config change done - lp oauth key set")

        try:
            self._autosync.configure_run(bool(self.config.get("dry_run", False)))
            self._autosync.configure_schedule(
                str(self.config.get("sync_hours", "")).strip() or DEFAULT_SYNC_HOURS
            )
        except ValueError:
            self.unit.status = ops.BlockedStatus(
                "Invalid sync hours. Use comma-separated integers in [0,23], e.g. '5,11,17,23'."
            )
            return False
        except IOError:
            self.unit.status = ops.BlockedStatus(
                "Failed to write autosync run configuration. Check `juju debug-log` for details."
            )
            return False

        self.unit.status = ops.ActiveStatus()

    def _on_sync_now(self, event: ops.ActionEvent):
        """Trigger an immediate autosync execution."""
        self.unit.status = ops.MaintenanceStatus("Running autosync")

        dry_run = bool(self.config.get("dry_run", False))
        try:
            self._autosync.configure_run(dry_run)
            event.log(f"Running autosync (dry_run={dry_run})")
            self._autosync.run_sync()
            event.log("Autosync run triggered")
        except (CalledProcessError, IOError, OSError):
            event.log("Autosync run failed")
            self.unit.status = ops.ActiveStatus(
                "Failed to run autosync. Check `juju debug-log` for details."
            )
            return
        self.unit.status = ops.ActiveStatus()

    def _get_external_url(self) -> str:
        """Report URL to access Ubuntu Auto Sync logs."""
        external_url = f"http://{socket.getfqdn()}:{PORT}"
        if binding := self.model.get_binding("juju-info"):
            unit_ip = str(binding.network.bind_address)
            external_url = f"http://{unit_ip}:{PORT}"
        if self.ingress.url:
            external_url = self.ingress.url
        return external_url


if __name__ == "__main__":  # pragma: nocover
    ops.main(UbuntuAutoSyncCharm)
