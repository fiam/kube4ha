# Home Assistant custom integrations

The chart installs HACS from a pinned, checksum-verified release archive by
default. It also bundles `kube4ha_matter_backup`, a small chart-owned integration
that connects Home Assistant's native pre-backup hook to the Matter supervisor.
Its Python source is packaged in the chart and copied from a ConfigMap to the
writable configuration PVC, never mounted read-only below `/config`. The
onboarding hook creates its credential-free config entry in seed mode.

`homeassistant.matterServer.backup.enabled: false` opts out of that integration;
remove its config entry in Home Assistant before disabling it. The reconciler
tracks and removes only its managed directory. Its domain is reserved while
the feature is enabled. When Matter itself is disabled or restore mode is
active, the installed backup helper is inactive. See
[Matter backups](BACKUP.md#matter-data-in-native-backups).

Other custom integrations are opted into explicitly through OpenTofu or private
Helm values. Their artifact verification and delivery contract follows below.

## Recommended: OpenTofu-managed archive

Set `homeassistant_remote_custom_components` in the ignored
`config/infra/terraform.tfvars` file in a private deployment. Each entry
installs exactly one integration
domain:

```hcl
homeassistant_remote_custom_components = [{
  name         = "example_integration"
  url          = "https://github.com/example/integration/archive/0123456789abcdef0123456789abcdef01234567.tar.gz"
  sha256       = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  archive_path = "integration-0123456789abcdef0123456789abcdef01234567/custom_components/example_integration"
}]
```

`name` is the Home Assistant integration domain and therefore permits
underscores. `archive_path` is the directory inside the archive that directly
contains `manifest.json`.

Use an immutable release artifact or a full commit archive. Download it once
to inspect its layout and calculate its digest before adding it:

```sh
archive="$(mktemp)"
curl --fail --location --output "$archive" \
  https://github.com/example/integration/archive/0123456789abcdef0123456789abcdef01234567.tar.gz
tar -tzf "$archive" | grep '/custom_components/.*/manifest.json$'
shasum -a 256 "$archive"
rm "$archive"
```

OpenTofu writes only the URL, digest, domain, and archive path to generated
non-secret Helm values. An init container downloads the archive on every Home
Assistant pod creation, verifies the SHA-256, rejects unsafe archive paths,
checks for `manifest.json`, and atomically replaces only the declared
integration directory under `/config/custom_components`. These directories
stay writable so Home Assistant's native restore can replace `/config`.

The chart records the integration domains it manages. Removing a domain from
the deployment deletes its managed directory on the next rollout while leaving
unmanaged custom integrations alone. A native restore replaces that record
along with the rest of `/config`; deploy the chart again after restoration to
reconcile the configured artifacts.

URLs must use HTTPS and must not contain credentials, private access tokens, or
expiring signed parameters. The current mechanism supports public archives. A
private repository should publish a separately accessible immutable artifact
or be supplied through the repository-local Helm method below; do not put a
repository token in OpenTofu's non-secret Helm output.

Apply and deploy after changing the list:

```sh
task plan
task deploy
```

## Direct Helm values

Deployments that do not use OpenTofu can provide the equivalent values in an
ignored `values.yaml`:

```yaml
homeassistant:
  customComponents:
    remote:
      - name: example_integration
        url: https://github.com/example/integration/archive/0123456789abcdef0123456789abcdef01234567.tar.gz
        sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
        archivePath: integration-0123456789abcdef0123456789abcdef01234567/custom_components/example_integration
```

For source already packaged below the Home Assistant chart's `files/`
directory, use a local entry instead:

```yaml
homeassistant:
  customComponents:
    local:
      - name: example_integration
        source: files/repositories/example/custom_components/example_integration
```

The local directory can be ordinary vendored source or a user-managed Git
submodule. It must be present before Helm renders the chart and must contain
the integration's `manifest.json`. No local repository is configured or
required by this project itself.

## Iterating against Kind

For local development, copy the current working tree of one or more custom
integrations directly into the Home Assistant volume:

```sh
task dev:integrations:sync SOURCE=../my-integration
```

`SOURCE` may be an integration repository containing `custom_components`, the
`custom_components` directory itself, or one integration directory containing
`manifest.json`. The task validates every domain, refuses symbolic links and
read-only chart-managed mounts, stages the new files on the Home Assistant
volume, preserves the replaced files, and restarts Home Assistant once.
It discovers the only Home Assistant deployment in the Kind cluster; set
`HA_NAMESPACE` and `HA_INSTANCE` to disambiguate if the cluster contains more
than one.

The synced files are intentionally outside OpenTofu and Helm. They survive pod
restarts but not deletion of the Kind cluster. This workflow is for testing a
local working tree only; use an immutable, checksum-pinned artifact for a
long-lived deployment.

## HACS

The chart installs [HACS](https://hacs.xyz) by default through a dedicated
init container. Delivery follows the same rules as `customComponents.remote`:
the `hacs.version` release archive is fetched from GitHub, verified against
`hacs.sha256`, and staged atomically into `/config/custom_components/hacs` on
the writable PVC. Both archive installers clear their
extraction scratch directory before each attempt: Kubernetes can rerun init
containers after a node reboot while retaining their `emptyDir` files. This
prevents overwrite prompts and stale files from blocking or contaminating a
retry.

The default version is maintained in lockstep with the
chart's default Home Assistant image: whenever the Home Assistant version pin
changes, bump `hacs.version` to the latest HACS release supporting it and
recompute the digest:

```sh
curl -fsSLO https://github.com/hacs/integration/releases/download/<version>/hacs.zip
shasum -a 256 hacs.zip
```

Setup is manual: HACS authenticates through GitHub's interactive device flow,
which the onboarding hook cannot drive, so finish configuration once in the UI
(Settings → Devices & services → Add integration → HACS).

Integrations that HACS itself installs into `custom_components/` are
unmanaged and unpinned; the chart's reconciler never touches them, and the
immutability guarantees above do not apply. Prefer migrating long-lived
integrations to `customComponents.remote` entries. Setting
`hacs.enabled: false` removes only the managed `hacs` directory on the next
rollout — HACS-installed integrations and HACS `.storage` data remain, so
remove them through the UI first.

## Upgrades and removal

Custom integrations execute inside Home Assistant and are coupled to its
internal Python APIs. Before upgrading Home Assistant, verify every configured
integration against the proposed exact Home Assistant version. The deployment
mechanism verifies artifact identity; it cannot establish compatibility or
trustworthiness.

To upgrade an integration, change its immutable URL, digest, and archive path
together, then run the normal OpenTofu and Helm workflow. The pod-template
checksum changes and forces a Home Assistant rollout.

Before removing an integration from the list, remove its config entries from
Home Assistant when possible. Otherwise Home Assistant retains those entries
and reports that their integration code is missing after the next rollout.
