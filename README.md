# Ubuntu Autosync Operator

**Ubuntu Autosync Operator** is a [charm](https://juju.is/charms-architecture)
that runs `autosync` on a schedule and publishes generated logs on a public
HTTP endpoint.

The service is modeled after the existing archive cron workflow:

## Behavior

- Scheduled runs at 05:00, 11:00, 17:00, and 23:00 UTC via `autosync.timer`.
- Public log index at `http://<unit-ip>/autosync/`.
- Manual trigger action `sync-now`.
- Optional `dry_run` config to append `--dry-run` to the runtime command.

## Basic usage

```bash
juju deploy ubuntu-autosync
```

Once the charm is deployed, you can check the status with Juju status:

```bash
❯ $ juju status
Model        Controller  Cloud/Region         Version  SLA          Timestamp
welcome-lxd  concierge-lxd  localhost/localhost  3.6.21   unsupported  11:47:41Z

App              Version  Status  Scale  Charm            Channel  Rev  Exposed  Message
ubuntu-autosync           active      1  ubuntu-autosync             1  no

Unit                Workload  Agent  Machine  Public address  Ports   Message
ubuntu-autosync/2*  active    idle   1        10.173.62.226   80/tcp

Machine  State    Address        Inst id        Base          AZ            Message
1        started  10.173.62.226  juju-e37332-2  ubuntu@24.04  juju-sandbox  Running
```

On first start up, the charm will install the application and install a systemd timer unit to trigger tracker updates on a regular basis.

Configure the Launchpad OAuth secret:

```bash
juju config ubuntu-autosync lpuser_secret_id=secret:<uuid>
```

Disable dry-run mode when needed:

```bash
juju config ubuntu-autosync dry_run=false
```

Trigger a manual run:

```bash
juju run ubuntu-autosync/0 sync-now
```

## Service inspection

```bash
systemctl list-timers --all autosync.timer
systemctl status autosync.service
journalctl -u autosync.service
```

## Testing

For information on tests and development workflows, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Ubuntu Autosync Operator is released under the [GPL-3.0 license](LICENSE).