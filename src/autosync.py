# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Representation of a single Ubuntu autosync worker and published logs."""

import logging
import os
import shutil
from pathlib import Path

from charmlibs import apt, pathops, systemd
from charmlibs.apt import PackageError, PackageNotFoundError

logger = logging.getLogger(__name__)

PACKAGES = [
    "nginx-light",
    "python3-apt",
    "python3-debian",
    "python3-keyring",
    "python3-launchpadlib",
    "ubuntu-dev-tools",
]

SRV_DIRS = [
    (Path("/srv/autosync/www"), "ubuntu", "ubuntu"),
    (Path("/srv/autosync/www/autosync"), "ubuntu", "ubuntu"),
]

NGINX_SITE_CONFIG_PATH = Path("/etc/nginx/conf.d/autosync.conf")
AUTOSYNC_SERVICE = "autosync"
AUTOSYNC_LOG_DIR = "/srv/autosync/www/autosync"
AUTOSYNC_RUNNER_PATH = Path("/usr/bin/run-autosync")
LP_OAUTH_KEY_PATH = "/home/ubuntu/.config/lp-ubuntu-archive-autosync-bot.oauth"
DEFAULT_SYNC_HOURS = "5,11,17,23"


class AutoSync:
    """Represent an instance running and publishing ubuntu autosync logs."""

    def __init__(self):
        logger.debug("AutoSync class init")
        self.env = os.environ.copy()
        self.proxies = {}
        juju_http_proxy = self.env.get("JUJU_CHARM_HTTP_PROXY")
        juju_https_proxy = self.env.get("JUJU_CHARM_HTTPS_PROXY")
        if juju_http_proxy:
            logger.debug("Setting HTTP_PROXY env to %s", juju_http_proxy)
            self.env["HTTP_PROXY"] = juju_http_proxy
            self.env["http_proxy"] = juju_http_proxy
            self.proxies["http"] = juju_http_proxy
        if juju_https_proxy:
            logger.debug("Setting HTTPS_PROXY env to %s", juju_https_proxy)
            self.env["HTTPS_PROXY"] = juju_https_proxy
            self.env["https_proxy"] = juju_https_proxy
            self.proxies["https"] = juju_https_proxy

    def _install_packages(self):
        """Install required apt packages."""
        apt.update()
        logger.debug("Apt index refreshed.")

        for package in PACKAGES:
            try:
                apt.add_package(package)
                logger.debug("Package %s installed", package)
            except PackageNotFoundError:
                logger.error("Failed to find package %s in package cache", package)
                raise
            except PackageError as e:
                logger.error("Failed to install %s: %s", package, e)
                raise

    def install(self):
        """Set up environment required for autosync and log publishing."""
        self._install_packages()

        for dir_path, dir_user, dir_group in SRV_DIRS:
            os.makedirs(dir_path, exist_ok=True)
            if dir_user is not None:
                shutil.chown(dir_path, dir_user, dir_group)

        shutil.copy("src/script/autosync", "/usr/bin/autosync")
        shutil.copy("src/script/lputils.py", "/usr/bin/lputils.py")
        shutil.copy("src/script/run-autosync", AUTOSYNC_RUNNER_PATH)
        shutil.copy("src/nginx/autosync.conf", NGINX_SITE_CONFIG_PATH)
        os.chmod("/usr/bin/autosync", 0o755)
        os.chmod(AUTOSYNC_RUNNER_PATH, 0o755)

        Path("/etc/nginx/sites-enabled/default").unlink(missing_ok=True)

    def start(self):
        """Start nginx and trigger autosync asynchronously once."""
        systemd.service_restart("nginx")
        systemd.service_start(f"{AUTOSYNC_SERVICE}.service", "--no-block")

    def configure_url(self, url: str):
        """URL is defined externally - this is a no-op for now."""
        logger.debug("configure_url: The url in use is %s", url)

    def configure_lpoauthkey(self, lp_key_data: str):
        """Create or refresh the credentials file for launchpad access."""
        lp_key_file = pathops.LocalPath(LP_OAUTH_KEY_PATH)
        parent_dir = lp_key_file.parent
        os.makedirs(parent_dir, exist_ok=True)

        key_success = False
        try:
            lp_key_file.write_text(
                lp_key_data,
                mode=0o600,
                user="ubuntu",
                group="ubuntu",
            )
            key_success = True
        except (FileNotFoundError, NotADirectoryError) as e:
            logger.error(
                "Failed to create lp credentials entry due to directory issues: %s",
                str(e),
            )
        except LookupError as e:
            logger.error(
                "Failed to create lp credentials entry due to issues with root user: %s",
                str(e),
            )
        except PermissionError as e:
            logger.error(
                "Failed to create lp credentials entry due to permission issues: %s",
                str(e),
            )
        logger.debug(
            "configure_lpoauthkey: written lp oauth key (length %d) to %s",
            len(lp_key_data),
            lp_key_file,
        )
        return key_success

    def configure_run(self, dry_run: bool):
        """Write run wrapper with current dry-run setting."""
        args = f"--log-directory {AUTOSYNC_LOG_DIR} --batch"
        if dry_run:
            args += " --dry-run"

        template = Path("src/script/run-autosync").read_text(encoding="utf-8")
        script = template.replace("__AUTOSYNC_ARGS__", args)
        AUTOSYNC_RUNNER_PATH.write_text(script, encoding="utf-8")
        os.chmod(AUTOSYNC_RUNNER_PATH, 0o755)

    def _parse_sync_hours(self, sync_hours: str) -> list[int]:
        """Parse comma-separated hour values in the range [0, 23]."""
        raw_values = [value.strip() for value in sync_hours.split(",") if value.strip()]
        if not raw_values:
            raise ValueError("sync hours cannot be empty")

        parsed_hours = []
        for value in raw_values:
            hour = int(value)
            if hour < 0 or hour > 23:
                raise ValueError("sync hours must be between 0 and 23")
            if hour not in parsed_hours:
                parsed_hours.append(hour)

        return parsed_hours

    def configure_schedule(self, sync_hours: str):
        """Write timer unit from configured hours and restart the timer."""
        hours = self._parse_sync_hours(sync_hours)

        timer_lines = [
            "[Unit]",
            "Description=Ubuntu Auto Sync - Scheduled runs",
            "",
            "[Timer]",
        ]
        timer_lines.extend(f"OnCalendar=*-*-* {hour:02d}:00:00" for hour in hours)
        timer_lines.extend(
            [
                "Persistent=true",
                "RandomizedDelaySec=60",
                "",
                "[Install]",
                "WantedBy=timers.target",
                "",
            ]
        )

        timer_path = Path(f"/etc/systemd/system/{AUTOSYNC_SERVICE}.timer")
        timer_path.write_text("\n".join(timer_lines), encoding="utf-8")
        systemd.daemon_reload()
        systemd.service_restart(f"{AUTOSYNC_SERVICE}.timer")

    def run_sync(self):
        """Trigger a blocking execution of the autosync service."""
        systemd.service_start(f"{AUTOSYNC_SERVICE}.service")

    def setup_systemd_unit(self):
        """Set up autosync service and timer with proxy configuration."""
        systemd_unit_location = Path("/etc/systemd/system")
        systemd_unit_location.mkdir(parents=True, exist_ok=True)

        service_content = Path(f"src/systemd/{AUTOSYNC_SERVICE}.service").read_text(
            encoding="utf-8"
        )
        timer_content = Path(f"src/systemd/{AUTOSYNC_SERVICE}.timer").read_text(encoding="utf-8")

        proxy_env_vars = ""
        if "http" in self.proxies:
            proxy_env_vars += "\nEnvironment=http_proxy=" + self.proxies["http"]
            proxy_env_vars += "\nEnvironment=HTTP_PROXY=" + self.proxies["http"]
        if "https" in self.proxies:
            proxy_env_vars += "\nEnvironment=https_proxy=" + self.proxies["https"]
            proxy_env_vars += "\nEnvironment=HTTPS_PROXY=" + self.proxies["https"]

        service_content += proxy_env_vars
        (systemd_unit_location / f"{AUTOSYNC_SERVICE}.service").write_text(
            service_content, encoding="utf-8"
        )
        (systemd_unit_location / f"{AUTOSYNC_SERVICE}.timer").write_text(
            timer_content, encoding="utf-8"
        )

        systemd.service_enable("--now", f"{AUTOSYNC_SERVICE}.timer")

    def setup_systemd_units(self):
        """Set up the autosync service and timer."""
        self.setup_systemd_unit()
        self.configure_schedule(DEFAULT_SYNC_HOURS)
