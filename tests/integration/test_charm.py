# Copyright 2025 Canonical
# See LICENSE file for licensing details.

import jubilant
import requests

from . import APPNAME, address


def test_service_state_after_deploy(juju: jubilant.Juju, ubuntu_autosync_charm, lpuser_secret):
    """Deploy the charm via jubilant and wait until application is active."""
    juju.deploy(ubuntu_autosync_charm, app=APPNAME)

    if lpuser_secret:
        juju.config(APPNAME, {"lpuser_secret_id": lpuser_secret})

    juju.wait(jubilant.all_active, timeout=1200)


def test_log_index_is_served(juju: jubilant.Juju):
    """Check that the log publication endpoint is served by nginx."""
    response = requests.get(f"http://{address(juju)}:80/autosync/", timeout=60)
    assert response.status_code == 200
