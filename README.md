# kube4ha — Kubernetes for Home Assistant

[![Kubernetes v1.23+](https://img.shields.io/badge/Kubernetes-v1.23%2B-326CE5?style=flat-square&logo=kubernetes&logoColor=white)](https://kubernetes.io/releases/)
[![Home Assistant 2026.9.0](https://img.shields.io/badge/Home%20Assistant-2026.9.0-18BCF2?style=flat-square&logo=home-assistant&logoColor=white)](https://www.home-assistant.io/)
[![OpenTofu v1.12+](https://img.shields.io/badge/OpenTofu-v1.12%2B-FFDA18?style=flat-square&logo=opentofu&logoColor=black)](https://opentofu.org/)
[![Helm 3](https://img.shields.io/badge/Helm-3-0F1689?style=flat-square&logo=helm&logoColor=white)](https://helm.sh/)

Home Assistant, Matter Server, Zigbee2MQTT, Mosquitto, and Cloudflare Tunnel on Kubernetes.

kube4ha works with any Kubernetes environment that ships Gateway API. The
[k3s guide](DEPLOYMENT.md) covers a common single-server home setup, but k3s is
not required.

Zigbee2MQTT enables Home Assistant MQTT discovery and
`homeassistant.experimental_event_entities` for all deployments. Devices that
expose actions get Home Assistant event entities. Their event types and
attributes are experimental and may change between Zigbee2MQTT releases.

## Install

Install `kubectl`, OpenTofu 1.12 or newer, Helm 3, Git, `jq`, and
[Task](https://taskfile.dev/docs/installation). If you use
[tenv](https://tofuutils.github.io/tenv/), the included `.opentofu-version`
selects the tested OpenTofu release automatically. Contributor checks also need
Python 3.12+ and Node.js 22.13+ for the Matter backup tests.

Create an independent private repository for your home's configuration:

```sh
mkdir home-deployment
cd home-deployment

task --taskfile \
  'https://github.com/fiam/kube4ha.git//Taskfile.remote.yml?ref=main' \
  init
```

Task asks you to approve the remote Taskfile the first time. Check the URL
before accepting it. `main` can be replaced with a tag, branch, or commit; the
generated Taskfile records the resolved commit.

### Configure Cloudflare and R2

Create an account API token in Cloudflare with these permissions:

- Entire account: Account API Tokens Read and Write
- Entire account: Workers R2 Storage Read and Write
- Entire account: Cloudflare One Connector `cloudflared` Read and Write
- The selected zone: Zone Read and DNS Read and Write

The token creates two narrower R2 credentials. One can access only OpenTofu
state; the other can access only Home Assistant backups.

Edit `config/bootstrap.tfvars`:

```hcl
cloudflare_account_id = "0123456789abcdef0123456789abcdef"
r2_bucket_prefix      = "my-home"
# r2_location         = "weur"
```

The prefix must be unique within the Cloudflare account. It creates these
private buckets:

```text
my-home-state
my-home-backups
```

A second installation can use the same account with another prefix.

Run the bootstrap task and enter a recovery passphrase and the Cloudflare
token when prompted:

```sh
task bootstrap
```

The resulting state file is encrypted by OpenTofu. Keep the recovery passphrase
in a password manager; it is not stored in the repository or the cluster. For
automation, provide it as `KUBE4HA_RECOVERY_PASSPHRASE`.

### Configure and deploy

Edit:

- `config/infra/terraform.tfvars` for the domain, routes, and Zigbee network;
- `config/values.yaml` for storage, the Zigbee adapter, and workload settings.

Stage the private configuration so the safety check can verify exactly what
Git will retain. Deployment commands use the current kubeconfig context and
honor `KUBECONFIG`:

```sh
git add .
kubectl config current-context
task check
task plan

git commit -m "chore: configure home deployment"
git remote add origin <private-repository-url>
git push -u origin main

task deploy
task status
```

OpenTofu generates the initial Home Assistant owner password. Retrieve it only
when needed:

```sh
task credentials:show
```

The default username is `admin`. Change the non-secret owner profile with
`homeassistant_owner` in `config/infra/terraform.tfvars`.

With the documented k3s setup, the local routes are:

- `http://homeassistant.local`
- `http://zigbee2mqtt.local`

Home Assistant is also available at the Cloudflare hostname in the OpenTofu
configuration.

## Matter devices

Matter Server is enabled by default in its own pod beside Home Assistant, so
Home Assistant rollouts never restart the controller or force paired devices
through a new session handshake. Seed-mode deployments automatically add a
missing Matter integration. Its URL is
`ws://127.0.0.1:5580/ws`; use the Home Assistant Companion app to pair devices.
Thread devices additionally require a Thread border router and working LAN
IPv6/multicast. See [Matter setup](DEPLOYMENT.md#matter-devices) for configuration
and requirements.

## Backups and recovery

Before each native backup, kube4ha copies the running Matter server's fabric
state into Home Assistant's configuration without stopping it. The same backup then
protects Matter pairings, and normal recovery restores an empty Matter volume
automatically. See [Matter backups](BACKUP.md#matter-data-in-native-backups).

Home Assistant configures daily native backups in `<prefix>-backups` and keeps
seven copies by default. Backups are unencrypted unless
`homeassistant_backup_encryption_enabled = true`; when enabled, OpenTofu
generates the password and `task credentials:show` reveals it.

An hourly CronJob stages a validated Zigbee2MQTT data-directory ZIP inside the
Home Assistant configuration volume. The next native backup includes it, so
the R2 backup covers Home Assistant data and the Zigbee2MQTT identity and
database.

OpenTofu state lives in `<prefix>-state` and is encrypted before upload. It
retains generated passwords, Cloudflare resource IDs, and Zigbee network keys.
Deleting a Kind cluster or losing a server does not delete that state.

To restore Home Assistant onto a new cluster:

```sh
task restore:plan
task restore
```

Upload the native backup in Home Assistant. When it has restarted, run
`task restore:complete`; it records an owner credential from the restored
system in encrypted state and resumes normal reconciliation.

See [BACKUP.md](BACKUP.md) for recovery details.

## Common commands

| Command | Purpose |
| --- | --- |
| `task bootstrap` | Create or update the persistent R2 foundation. |
| `task check` | Validate code and private configuration. |
| `task plan` | Preview OpenTofu changes. |
| `task deploy` | Apply OpenTofu and deploy the Helm release. |
| `task status` | Show workloads and routes. |
| `task credentials:show` | Reveal generated recovery credentials. |
| `task credentials:update` | Record a Home Assistant password changed in the UI. |
| `task zigbee:import SOURCE=…` | Import an existing Zigbee identity. |
| `task restore`, `task restore:complete` | Restore a native Home Assistant backup. |
| `task kube4ha:update REF=main` | Update the pinned kube4ha source. |
| `task homeassistant:update` | Deploy the Home Assistant version verified by that source. |
| `task cloudflare-token:update` | Replace the account token in encrypted bootstrap state. |
| `task recovery-passphrase:update` | Re-encrypt both state files with a new passphrase. |

`task destroy` removes the Helm release and ordinary managed infrastructure.
It preserves both R2 buckets and the scoped credentials that make recovery
possible.

## Development

Clone this repository when changing the OpenTofu configuration, charts, or
scripts. Run the full static suite with:

```sh
task check
```

The development stack uses Kind. On macOS, Colima's shared network mode is
needed for pods to make unicast connections to devices on the physical LAN.
See [DEVELOPMENT_NETWORKING.md](DEVELOPMENT_NETWORKING.md) before recreating
Colima or a Kind cluster.

More detail:

- [Private deployment workflow](PRIVATE_DEPLOYMENT.md)
- [k3s installation](DEPLOYMENT.md)
- [Backups and disaster recovery](BACKUP.md)
- [OpenTofu infrastructure](infra/README.md)
- [Home Assistant compatibility contract](HOME_ASSISTANT_COMPATIBILITY.md)
- [Custom integrations](CUSTOM_INTEGRATIONS.md)
