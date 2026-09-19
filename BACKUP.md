# Backups and disaster recovery

kube4ha uses two private Cloudflare R2 buckets for different purposes:

- `<prefix>-state` contains client-side encrypted OpenTofu state;
- `<prefix>-backups` contains Home Assistant native backups.

The buckets have different scoped credentials. Home Assistant cannot read or
modify infrastructure state.

There is no separate kube4ha repository-backup command. The private Git
repository, encrypted OpenTofu state, and native Home Assistant backups
(including the staged Matter snapshot) form the recovery set.

## What to keep

Keep these independently recoverable:

1. the private deployment repository, including `state/bootstrap.tfstate`;
2. the recovery passphrase used by OpenTofu;
3. the native Home Assistant backups in R2, including Matter snapshots.

Matter is enabled by default. Its live fabric credentials reside on a separate
retained PVC, and native backups include a fresh snapshot as described
[below](#matter-data-in-native-backups).

The encrypted bootstrap state contains the Cloudflare account token and the
two bucket-scoped credentials. The main state bucket contains generated Home
Assistant credentials, Zigbee keys, Cloudflare resource IDs, and the desired
Kubernetes objects. Home Assistant's backup contains its database and `/config`.

Losing only a Kubernetes cluster or server is recoverable with all of the above
backups. Without a Matter snapshot, Matter devices need pairing again.
Losing the recovery passphrase makes both OpenTofu states unreadable. Losing
the state bucket can orphan external Cloudflare resources even if the Home
Assistant backup survives.

## Bucket setup

The private deployment's `config/bootstrap.tfvars` selects the bucket prefix:

```hcl
cloudflare_account_id = "0123456789abcdef0123456789abcdef"
r2_bucket_prefix      = "my-home"
r2_location           = "weur" # optional
```

`task bootstrap` creates `my-home-state` and `my-home-backups`, plus one
object read/write token for each bucket. Use a different prefix for every
kube4ha installation sharing the Cloudflare account.

Both buckets are private and protected from ordinary OpenTofu destruction.
The state backend also uses OpenTofu's adjacent `.tflock` object to serialize
operations.

## Automatic Home Assistant backups

In seed mode, the Home Assistant onboarding Job creates a Cloudflare R2 backup
agent through Home Assistant's config flow. It then initializes automatic
backups with seven-copy retention and Home Assistant's default randomized
early-morning schedule.

Override those defaults in `config/infra/terraform.tfvars`:

```hcl
homeassistant_automatic_backups = {
  enabled          = true
  retention_copies = 14
  time             = "03:30:00"
}
```

The bootstrap settings are applied only when the R2 config entry or automatic
backup settings are missing. Later changes made in Home Assistant remain
authoritative.

Backups are unencrypted by default because the bucket is private. To enable
Home Assistant's native backup encryption:

```hcl
homeassistant_backup_encryption_enabled = true
```

OpenTofu generates and retains the password in encrypted state. Retrieve it
with `task credentials:show`, and download Home Assistant's emergency kit from
the backup settings page. Keep the kit outside the cluster and R2 account.

Verify backups in Home Assistant under **Settings → System → Backups**. An
actual restore test is stronger than checking that an object exists in R2.

## Matter data in native backups

Matter snapshots are enabled by default. The bundled `kube4ha_matter_backup`
integration uses Home Assistant's documented
[pre/post-backup platform](https://developers.home-assistant.io/docs/core/platform/backup/).
Seed-mode onboarding creates its config entry automatically. For manual
onboarding, add **kube4ha Matter Backup** under Settings → Devices & services.

Before each native backup, the integration contacts the Matter supervisor over
a Unix socket in a node-local directory shared only between the Home Assistant
and Matter pods (`matterServer.backup.controlPath`). The supervisor copies the
running server's storage tree, validates a compressed archive of that copy,
and atomically replaces:

```text
/config/.kube4ha/matter/latest.tar.gz
```

The archive records its format, Matter image, and creation time. Matter keeps
running throughout, so paired devices keep their sessions and no reconnection
follows a backup. The copy is consistent because matter.js stores state as an
append-only write-ahead log plus a snapshot that is replaced atomically: the
supervisor copies every log segment before the snapshot, skips the lock, PID,
and temporary files, and the server ignores commits already covered by a newer
snapshot as well as a torn final log line. An invalid or incomplete archive
fails the native backup and preserves the previous archive. Check failures in
Home Assistant's backup settings.

Home Assistant includes the snapshot in its native backup, using the same
destination, encryption, and retention settings. Keep backups outside the
cluster and out of Git: they contain fabric credentials needed to control paired
devices. This protects the bundled server only. An external Matter server needs
its own backup procedure, even if its integration entry is preserved by kube4ha.

To opt out, remove the helper's config entry in Home Assistant, then set:

```yaml
homeassistant:
  matterServer:
    backup:
      enabled: false
```

The next rollout removes the helper code and runs Matter without the supervisor.
Its volume then needs a separate backup while Matter is stopped. Disabling or
removing the integration in Home Assistant also disables snapshot protection.
See [below](#restore-onto-a-new-cluster) for automatic recovery.

## Zigbee2MQTT data in native backups

An hourly Kubernetes CronJob asks Zigbee2MQTT for its documented data-directory
backup over MQTT. It validates the response as a ZIP and atomically replaces:

```text
/config/.kube4ha/zigbee2mqtt/latest.zip
/config/.kube4ha/zigbee2mqtt/latest.timestamp
```

Home Assistant includes that directory in its native `/config` archive. Only
one Zigbee2MQTT snapshot is staged, so it does not create a second retention
system. With the default `17 * * * *` schedule, Zigbee data can be up to one
hour older than the Home Assistant backup.

The Job mounts only Home Assistant's claim. Required pod affinity places it on
the Home Assistant pod's node for `ReadWriteOnce` volumes; it never mounts the
live Zigbee2MQTT claim. A failed MQTT request or invalid ZIP leaves the last
valid snapshot intact.

Change the schedule or disable staging in `config/values.yaml`:

```yaml
homeassistant:
  zigbee2mqttBackup:
    schedule: "17 */6 * * *"
    # enabled: false
```

Inspect execution status without displaying archive contents:

```sh
kubectl -n kube4ha get cronjob kube4ha-homeassistant-z2m-backup
kubectl -n kube4ha get jobs \
  -l app.kubernetes.io/component=zigbee2mqtt-backup
```

A Home Assistant restore puts the staged ZIP back under `/config`. Restoring
that ZIP into a new Zigbee2MQTT volume is a separate maintenance operation;
never extract it over a running Zigbee2MQTT instance.

## Restore onto a new cluster

Clone the private repository and configure access to the replacement cluster:

```sh
git clone <private-repository-url> home-deployment
cd home-deployment
kubectl config current-context
```

Then prepare Home Assistant's native restore screen:

```sh
task restore:plan
task restore
```

Enter the OpenTofu recovery passphrase when prompted. The restore task reads
the existing encrypted main state from R2, recreates cluster secrets and
routes, and deploys only the minimal Home Assistant configuration needed for
the native upload flow.

Upload the chosen backup in Home Assistant and wait for the application to
restart. If the pinned Home Assistant upload endpoint rejects it with HTTP 413
(its body limit is 16 MiB), place the unchanged native `.tar` in the fresh Home
Assistant volume's `backups/` directory using your storage backend's transfer
tools. Restart Home Assistant to refresh its local backup inventory, then
restore through the native onboarding flow. Use the complete native archive;
extracting only the Matter snapshot is not a substitute. See the
[compatibility record](HOME_ASSISTANT_COMPATIBILITY.md#matter-addition-2026-09-11)
for the tested local-agent recovery path.

After the native restore succeeds, run:

```sh
task restore:complete
```

Supply an owner username and password from the restored system. The task
retains that credential in encrypted state and resumes MQTT, R2, URL, and
other chart-derived reconciliation. It also reinstalls each declared custom
integration from its checksum-pinned artifact; their directories remain on the
writable configuration volume so they cannot block Home Assistant from
replacing `/config` during restoration.

Restore mode omits Matter, its initializer, and integration seeding so no new
fabric is created during recovery. On `task restore:complete`, an empty Matter
PVC is populated from the validated snapshot before its controller starts.
Existing Matter data is never overwritten. An invalid snapshot or mismatched
Matter image fails initialization; recover with the snapshot's version before
performing a separately verified upgrade. Backups made before snapshot support
or with the helper disabled need a separately preserved Matter volume or device
pairing again.

For a deliberate Matter rollback, select a new empty PVC with
`homeassistant.matterServer.persistence.existingClaim`. Never extract over live
state or run two controllers with the same fabric identity simultaneously.

After recovery, confirm:

- the Matter volume has been restored and Matter devices reconnect;
- Home Assistant history and integrations are present;
- a new automatic backup reaches R2;
- MQTT and Zigbee2MQTT are connected;
- PAN ID and channel match the private configuration;
- the staged Zigbee2MQTT archive exists and is current;
- `task plan` reports no unexpected changes.

## Rotate recovery credentials

Replace the Cloudflare account token before revoking the old token:

```sh
task cloudflare-token:update
git add state/bootstrap.tfstate
git commit -m "chore: rotate Cloudflare token"
```

After the first deployment, change the OpenTofu recovery passphrase with:

```sh
task recovery-passphrase:update
git add state/bootstrap.tfstate
git commit -m "chore: rotate recovery passphrase"
```

That task rolls both encrypted states to the new key. If it is interrupted,
retry with the same new passphrase.

References:

- [Home Assistant backups](https://www.home-assistant.io/common-tasks/general/#backups)
- [Cloudflare R2 API tokens](https://developers.cloudflare.com/r2/api/tokens/)
- [OpenTofu state encryption](https://opentofu.org/docs/v1.12/language/state/encryption/)
- [OpenTofu S3 backend](https://opentofu.org/docs/language/settings/backends/s3/)
- [Zigbee2MQTT backup request](https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html#zigbee2mqttbridgerequestbackup)
