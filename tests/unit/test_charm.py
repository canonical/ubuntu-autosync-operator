# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Unit tests for the charm."""

from subprocess import CalledProcessError
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, call, patch

import ops
import pytest
from charmlibs.apt import PackageError, PackageNotFoundError
from ops.testing import (
    ActiveStatus,
    Address,
    BindAddress,
    BlockedStatus,
    Context,
    Network,
    Relation,
    State,
    TCPPort,
)

from charm import UbuntuAutoSyncCharm


@pytest.fixture
def ctx():
    return Context(UbuntuAutoSyncCharm)


@pytest.fixture
def base_state():
    return State(leader=True)


@patch("charm.AutoSync.install")
@patch("charm.AutoSync.setup_systemd_units")
@patch("charm.AutoSync.configure_run")
@patch("charm.AutoSync.configure_schedule")
def test_install_event_sets_active_status_on_success(
    configure_schedule_mock, configure_run_mock, setup_units_mock, install_mock, ctx, base_state
):
    state_in = State(
        leader=True,
        config={
            "dry_run": False,
            "sync_hours": "5,11",
        },
    )

    out = ctx.run(ctx.on.install(), state_in)

    assert out.unit_status == ActiveStatus()
    install_mock.assert_called_once()
    setup_units_mock.assert_called_once()
    configure_run_mock.assert_called_once_with(False)
    configure_schedule_mock.assert_called_once_with("5,11")


@patch("charm.AutoSync.install")
@pytest.mark.parametrize(
    "exception",
    [
        PackageError,
        PackageNotFoundError,
        CalledProcessError(1, "foo"),
    ],
)
def test_install_event_blocks_charm_on_environment_setup_failure(
    install_mock, exception, ctx, base_state
):
    install_mock.side_effect = exception

    out = ctx.run(ctx.on.install(), base_state)

    assert out.unit_status == BlockedStatus(
        "Failed to set up the environment. Check `juju debug-log` for details."
    )


@patch("charm.AutoSync.start")
def test_start_event_opens_port_80_and_sets_active_status(start_mock, ctx, base_state):
    out = ctx.run(ctx.on.start(), base_state)

    assert out.unit_status == ActiveStatus()
    start_mock.assert_called_once()
    assert out.opened_ports == {TCPPort(port=80, protocol="tcp")}


@patch("charm.AutoSync.start")
def test_start_event_blocks_charm_when_service_start_fails(start_mock, ctx, base_state):
    start_mock.side_effect = CalledProcessError(1, "foo")

    out = ctx.run(ctx.on.start(), base_state)

    assert out.unit_status == BlockedStatus(
        "Failed to start services. Check `juju debug-log` for details."
    )
    assert out.opened_ports == frozenset()


@patch(
    "charm.UbuntuAutoSyncCharm._lpuser_lp_oauthkey",
    new_callable=PropertyMock,
)
@patch("charm.AutoSync.configure_lpoauthkey")
@patch("charm.AutoSync.configure_url")
@patch("charm.AutoSync.configure_run")
@patch("charm.AutoSync.configure_schedule")
def test_config_changed_event_configures_url_oauth_and_run_mode(
    configure_schedule_mock,
    configure_run_mock,
    configure_url_mock,
    configure_lpoauth_mock,
    lp_oauth_prop_mock,
    ctx,
):
    state_in = State(
        leader=True,
        config={"dry_run": True, "sync_hours": "6"},
    )
    lp_oauth_prop_mock.return_value = "fake-token"
    configure_lpoauth_mock.return_value = True

    out = ctx.run(ctx.on.config_changed(), state_in)

    assert out.unit_status == ActiveStatus()
    configure_url_mock.assert_called_once()
    configure_lpoauth_mock.assert_called_once_with("fake-token")
    configure_run_mock.assert_called_once_with(True)
    configure_schedule_mock.assert_called_once_with("6")


@patch(
    "charm.UbuntuAutoSyncCharm._lpuser_lp_oauthkey",
    new_callable=PropertyMock,
)
@patch("charm.AutoSync.configure_lpoauthkey")
@patch("charm.AutoSync.configure_url")
@patch("charm.AutoSync.configure_run")
@patch("charm.AutoSync.configure_schedule")
def test_config_changed_event_blocks_charm_on_invalid_schedule(
    configure_schedule_mock,
    configure_run_mock,
    configure_url_mock,
    configure_lpoauth_mock,
    lp_oauth_prop_mock,
    ctx,
):
    state_in = State(
        leader=True,
        config={"dry_run": False, "sync_hours": "25"},
    )
    lp_oauth_prop_mock.return_value = "fake-token"
    configure_lpoauth_mock.return_value = True
    configure_schedule_mock.side_effect = ValueError("invalid")

    out = ctx.run(ctx.on.config_changed(), state_in)

    assert out.unit_status == BlockedStatus(
        "Invalid sync hours. Use comma-separated integers in [0,23], e.g. '5,11,17,23'."
    )


def test_config_changed_event_blocks_charm_when_lp_secret_not_configured(ctx, base_state):
    out = ctx.run(ctx.on.config_changed(), base_state)

    assert out.unit_status == BlockedStatus("Launchpad oauth token config missing.")


@patch("charm.AutoSync.run_sync")
@patch("charm.AutoSync.configure_run")
@pytest.mark.parametrize("dry_run", [True, False])
def test_sync_now_action_reconfigures_dry_run_before_triggering_run(
    configure_run_mock, run_sync_mock, dry_run, ctx
):
    state_in = State(leader=True, config={"dry_run": dry_run})
    calls = MagicMock()
    calls.attach_mock(configure_run_mock, "configure_run")
    calls.attach_mock(run_sync_mock, "run_sync")

    out = ctx.run(ctx.on.action("sync-now"), state_in)

    assert ctx.action_logs == [
        f"Running autosync (dry_run={dry_run})",
        "Autosync run triggered",
    ]
    assert out.unit_status == ActiveStatus()
    calls.assert_has_calls([call.configure_run(dry_run), call.run_sync()])


@patch("charm.AutoSync.run_sync")
@patch("charm.AutoSync.configure_run")
def test_sync_now_action_sets_status_message_when_run_fails(
    configure_run_mock, run_sync_mock, ctx, base_state
):
    state_in = State(leader=True, config={"dry_run": False})
    run_sync_mock.side_effect = CalledProcessError(1, "sync")

    out = ctx.run(ctx.on.action("sync-now"), state_in)

    assert out.unit_status == ActiveStatus(
        "Failed to run autosync. Check `juju debug-log` for details."
    )
    configure_run_mock.assert_called_once_with(False)


@patch("charm.AutoSync.run_sync")
@patch("charm.AutoSync.configure_run")
def test_sync_now_action_sets_status_message_when_run_configuration_fails(
    configure_run_mock, run_sync_mock, ctx, base_state
):
    state_in = State(leader=True, config={"dry_run": False})
    configure_run_mock.side_effect = OSError("write failed")

    out = ctx.run(ctx.on.action("sync-now"), state_in)

    assert ctx.action_logs == ["Autosync run failed"]
    assert out.unit_status == ActiveStatus(
        "Failed to run autosync. Check `juju debug-log` for details."
    )
    run_sync_mock.assert_not_called()


@patch(
    "charm.UbuntuAutoSyncCharm._lpuser_lp_oauthkey",
    new_callable=PropertyMock,
)
@patch("charm.AutoSync.configure_lpoauthkey")
@patch("charm.AutoSync.configure_url")
@patch("charm.AutoSync.configure_run")
@patch("charm.AutoSync.configure_schedule")
@patch("charm.socket.getfqdn")
@patch("ops.model.Model.get_binding")
def test_get_external_url_uses_fqdn_when_no_network_binding_or_ingress(
    get_binding_mock,
    getfqdn_mock,
    configure_schedule_mock,
    configure_run_mock,
    configure_url_mock,
    configure_lpoauth_mock,
    lp_oauth_prop_mock,
    ctx,
):
    get_binding_mock.return_value = None
    getfqdn_mock.return_value = "test-host.example.com"
    lp_oauth_prop_mock.return_value = "fake-token"
    configure_lpoauth_mock.return_value = True

    out = ctx.run(ctx.on.config_changed(), State(leader=True))

    assert out.unit_status == ActiveStatus()
    configure_url_mock.assert_called_once_with("http://test-host.example.com:80")


@patch(
    "charm.UbuntuAutoSyncCharm._lpuser_lp_oauthkey",
    new_callable=PropertyMock,
)
@patch("charm.AutoSync.configure_lpoauthkey")
@patch("charm.AutoSync.configure_url")
@patch("charm.AutoSync.configure_run")
@patch("charm.AutoSync.configure_schedule")
def test_get_external_url_uses_juju_info_binding_ip_when_available(
    configure_schedule_mock,
    configure_run_mock,
    configure_url_mock,
    configure_lpoauth_mock,
    lp_oauth_prop_mock,
    ctx,
):
    state = State(
        leader=True,
        networks={
            Network(
                "juju-info",
                bind_addresses=[BindAddress(addresses=[Address("192.168.1.10")])],
            ),
        },
    )
    lp_oauth_prop_mock.return_value = "fake-token"
    configure_lpoauth_mock.return_value = True

    out = ctx.run(ctx.on.config_changed(), state)

    assert out.unit_status == ActiveStatus()
    configure_url_mock.assert_called_once_with("http://192.168.1.10:80")


@patch(
    "charm.UbuntuAutoSyncCharm._lpuser_lp_oauthkey",
    new_callable=PropertyMock,
)
@patch("charm.AutoSync.configure_lpoauthkey")
@patch("charm.AutoSync.configure_url")
@patch("charm.AutoSync.configure_run")
@patch("charm.AutoSync.configure_schedule")
def test_get_external_url_prioritizes_ingress_url_over_binding(
    configure_schedule_mock,
    configure_run_mock,
    configure_url_mock,
    configure_lpoauth_mock,
    lp_oauth_prop_mock,
    ctx,
):
    ingress_relation = Relation(
        endpoint="ingress",
        interface="ingress",
        remote_app_name="traefik",
        remote_app_data={"ingress": '{"url": "https://ingress.example.com/"}'},
    )
    state = State(
        leader=True,
        networks={
            Network(
                "juju-info",
                bind_addresses=[BindAddress(addresses=[Address("192.168.1.10")])],
            ),
        },
        relations={ingress_relation},
    )
    lp_oauth_prop_mock.return_value = "fake-token"
    configure_lpoauth_mock.return_value = True

    out = ctx.run(ctx.on.config_changed(), state)

    assert out.unit_status == ActiveStatus()
    configure_url_mock.assert_called_once_with("https://ingress.example.com/")


def test_lpuser_secret_property_returns_none_when_secret_not_found():
    dummy = SimpleNamespace()
    dummy.config = {"lpuser_secret_id": "missing"}
    dummy.model = MagicMock()
    dummy.model.get_secret.side_effect = ops.SecretNotFoundError

    result = UbuntuAutoSyncCharm._lpuser_secret.fget(dummy)

    assert result is None


def test_lpuser_lp_oauthkey_property_returns_none_when_key_missing_from_secret():
    dummy = SimpleNamespace()
    fake_secret = MagicMock()
    fake_secret.get_content.return_value = {}
    dummy._lpuser_secret = fake_secret

    result = UbuntuAutoSyncCharm._lpuser_lp_oauthkey.fget(dummy)

    assert result is None
