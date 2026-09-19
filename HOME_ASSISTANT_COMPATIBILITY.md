# Home Assistant compatibility contract

This deployment deliberately automates parts of Home Assistant that do not
have stable public provisioning interfaces. A successful Helm render is not
proof that these integrations work. Every Home Assistant upgrade must be
treated as a compatibility migration and tested on a new disposable volume.

## Verified baseline

Last verified: **2026-09-04**

| Contract | Verified value |
| --- | --- |
| Home Assistant Core image | `2026.9.0` |
| cloudflared image | `2026.8.3` |
| Zigbee2MQTT image | `2.14.1` |
| Resulting HTTP storage | `.storage/http`, store `2.2` |
| Resulting core configuration storage | `.storage/core.config`, store `1.4` |
| Resulting config-entry storage | `.storage/core.config_entries`, store `1.5` |
| MQTT config flow result | entry `2.1`, protocol `5`, transport `tcp` |
| Cloudflare R2 config flow result | entry `1.1` |
| Resulting backup storage | `.storage/backup`, store `1.7` |
| Seeded automatic backup | daily, R2 only, seven copies; protection follows the optional password |
| Core settings command | `config/core/update` |
| Reconciled core settings | location name, external URL, internal URL |
| Config-entry lookup command | `config_entries/get` |
| Integration flow endpoint | `/api/config/config_entries/flow` |
| HTTP settings commands | `http/config`, `http/config/configure`, `http/config/promote` |
| Onboarding steps | `user`, `core_config`, `analytics`, `integration` |
| Custom integration delivery | HACS installed by default from a pinned release; checksum-pinned remote installation and writable-PVC restore verified |
| Zigbee2MQTT snapshot staging | hourly CronJob; validated latest ZIP under `/config/.domotic/zigbee2mqtt` |

The current default is `/config/.kube4ha/zigbee2mqtt`. The staging directory is
configurable with `homeassistant.zigbee2mqttBackup.directory`.

The version pin lives in the root and Home Assistant `Chart.yaml` files and in
the Home Assistant default values. `examples/values-production.yaml` also pins
the image explicitly, and the README badge identifies the audited version.
Keep all five locations aligned.

Private deployments may retain an older image in `config/values.yaml` while
updating their kube4ha source pin. `task homeassistant:update` copies the
verified Home Assistant `appVersion` from that source into the private values
file before running Helm. It deliberately does not accept an arbitrary image
tag; a different version first requires this compatibility audit and a new
kube4ha revision.

## Private and version-coupled behavior

The following behavior is not a supported declarative provisioning API. It may
change in any Home Assistant release, including a patch release.

| Implementation | Private assumption | Failure mode |
| --- | --- | --- |
| `templates/onboarding-job.yaml` | `/api/onboarding` and its step endpoints retain their paths, payloads, order, authentication requirements, and response shapes | The owner may not be created or onboarding may remain partially complete. |
| `templates/onboarding-job.yaml` | `/auth/login_flow` accepts the `homeassistant` handler and returns an authorization code in the current flow result shape | Retry of partially completed onboarding can fail. |
| `templates/onboarding-job.yaml` | `config/core/update` retains its schema and persists name and URLs without making them YAML-managed | Declared settings may not be applied or may become read-only in the UI. |
| `templates/onboarding-job.yaml` | `config_entries/get` and config-flow HTTP views retain their paths, result shapes, authorization, and duplicate semantics | Existing integrations may not be detected, or a missing integration may not be created. |
| `templates/onboarding-job.yaml` | The MQTT broker step accepts protocol `5` and the nested TCP/no-certificate `other_settings` payload | MQTT setup can stop at a form error or use different connection defaults. |
| `templates/onboarding-job.yaml` | The Matter user flow on Container installations returns a `manual` form accepting `url` and validates the server's WebSocket schema | Matter setup can fail to connect or reject an incompatible server. Existing entries must be detected first because the flow can reconfigure them. |
| `templates/onboarding-job.yaml` | The Cloudflare R2 user step accepts the current credential, bucket, endpoint, and prefix fields and validates them with `HeadBucket` | R2 setup can fail validation or store incompatible data. |
| `templates/onboarding-job.yaml` | `http/config/configure` stages a complete configuration, restarts Home Assistant, and `http/config/promote` confirms the active pending slot before automatic rollback | The hook can lose connectivity, leave an unpromoted trial, or restore defaults after the trial window. |
| `templates/onboarding-job.yaml` | `backup/config/info`, `backup/agents/info`, and `backup/config/update` retain their schemas and authorization behavior | The automatic R2 schedule may be absent, target the wrong agent, or use incorrect protection or retention. |
| `templates/onboarding-job.yaml` | The R2 backup agent ID starts with `cloudflare_r2.` and its display name equals the config-entry title/bucket | The hook cannot identify the intended R2 destination. |
| `templates/onboarding-job.yaml` | The Home Assistant image contains compatible `python3` and `aiohttp` runtimes | The Helm hook cannot run. |
| `templates/deployment.yaml` | The `.seed` adoption rules distinguish unchanged chart-managed YAML from user-modified files | An update could overwrite YAML edits or fail to restore a missing managed file. |
| Restore mode | Starting with only `default_config:` exposes Home Assistant's native onboarding backup upload flow and a restored `/config` takes over | A release may require different bootstrap configuration or restore steps. |
| Custom integration files | A user-selected integration remains compatible with the pinned Home Assistant image and can run from its chart-managed directory on the writable configuration volume | Home Assistant can reject or fail to load the integration even though artifact verification and installation succeeded. |
| `files/kube4ha_matter_backup/backup.py` | The documented pre/post-backup platform runs before configuration archiving and propagates snapshot failures | Backups may omit current Matter state or incorrectly succeed with stale state. |
| `files/matter/supervisor.mjs` and `initialize.py` | The pinned matter.js storage keeps key/value state as an append-only WAL (`wal/*.jsonl`, compressed once rotated) plus a `snapshot.json.gz` replaced by rename, never deletes segments while running, skips replayed commits already covered by a newer snapshot, tolerates a torn final log line, and reopens a copied tree without `matter.lock` or `matter.pid` | A live snapshot could be inconsistent or a restored fabric could fail to load. |
| `templates/matter-deployment.yaml` | The Matter integration reconnects to `ws://127.0.0.1:<port>/ws` on its own after the separate Matter pod restarts, and tolerates the server being absent while Home Assistant starts | The integration could stay failed after a controller restart or block Home Assistant startup. |
| `templates/zigbee2mqtt-backup-cronjob.yaml` | Home Assistant continues archiving non-excluded files beneath `/config`, including the staged Zigbee2MQTT ZIP | A native backup may omit the Zigbee2MQTT recovery snapshot even though the CronJob succeeds. |

The chart does not write private `.storage` files. In seed mode, the
authenticated hook creates MQTT only when no MQTT entry exists, creates Matter
only when enabled and no Matter entry exists, creates R2 only
when no entry matches the declared bucket title, and reconciles HTTP settings
through Home Assistant's trial-and-promotion workflow. It never updates
existing integration credentials. Restore mode must never run the hook or any
config flow, and keeps the Matter controller stopped until its native snapshot
has been recovered. OpenTofu seed mode supplies admin credentials so chart-derived
settings are never silently skipped. Restore mode remains the credential-free
path for manual onboarding or native backup recovery.

The token exchange and revocation endpoints are documented, but the automated
login-flow and onboarding calls around them remain implementation-coupled. Do
not broaden the claim of stability merely because they are HTTP endpoints.

Custom integrations are selected entirely by the deployment owner. The chart
verifies artifact identity and isolation, not source trust or Home Assistant
compatibility. Follow [CUSTOM_INTEGRATIONS.md](CUSTOM_INTEGRATIONS.md) whenever
changing their delivery contract.

## Sources to inspect for every upgrade

Replace `2026.9.0` in these links with the proposed exact tag and compare the
code, constants, schemas, migrations, and tests—not just release notes.

- [Onboarding views and payloads](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/onboarding/views.py)
- [Login-flow HTTP views](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/auth/login_flow.py)
- [Documented token and revocation API](https://developers.home-assistant.io/docs/auth_api/)
- [Core-settings WebSocket command](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/config/core.py)
- [Config-entry flow HTTP views and lookup command](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/config/config_entries.py)
- [HTTP user-configuration WebSocket commands](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/http/websocket_api.py)
- [HTTP storage schema](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/http/config.py)
- [Config-entry storage and migrations](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/config_entries.py)
- [MQTT config flow](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/mqtt/config_flow.py)
- [MQTT config-flow tests](https://github.com/home-assistant/core/blob/2026.9.0/tests/components/mqtt/test_config_flow.py)
- [MQTT constants](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/mqtt/const.py)
- [Matter config flow](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/matter/config_flow.py)
- [Matter config-flow tests](https://github.com/home-assistant/core/blob/2026.9.0/tests/components/matter/test_config_flow.py)
- [Matter client requirements](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/matter/manifest.json)
- [Matter integration setup](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/matter/__init__.py)
- [Documented pre/post-backup platform](https://developers.home-assistant.io/docs/core/platform/backup/)
- [Matter Server 1.4.0 CLI and container contract](https://github.com/matter-js/matterjs-server/blob/v1.4.0/docs/docker.md)
- [Cloudflare R2 config flow](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/cloudflare_r2/config_flow.py)
- [Backup WebSocket command schemas](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/backup/websocket.py)
- [Automatic schedule and retention model](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/backup/config.py)
- [Backup agent ID contract](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/backup/agent.py)
- [Cloudflare R2 backup agent](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/cloudflare_r2/backup.py)
- [Native onboarding backup views](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/backup/onboarding.py)
- [Startup-time backup restoration](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/backup_restore.py)
- [Native backup documentation](https://www.home-assistant.io/integrations/backup/)
- [Core backup archive construction and exclusions](https://github.com/home-assistant/core/blob/2026.9.0/homeassistant/components/backup/manager.py)
- [Zigbee2MQTT backup request](https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html#zigbee2mqttbridgerequestbackup)

Inspect the tests beside these source files as well. They often document
required payloads and migration behavior more precisely than user-facing docs.

## Mandatory upgrade procedure

1. Select an exact patch release. Never deploy the moving `stable` tag.
2. Audit every source above at that tag. Update the version table, API
   payloads, onboarding client, and tests before changing the image pin.
3. Audit every user-configured custom integration against the proposed image.
   Upgrade or remove incompatible integrations before testing the stack. Bump
   the default `hacs.version` and `hacs.sha256` to the latest HACS release
   supporting the proposed Home Assistant version, and exercise HACS in the
   Kind test.
4. Run `task check`.
5. Create a fresh Kind cluster and Home Assistant PVC. Bootstrap isolated R2
   state and backup buckets, then supply the required seed-mode credentials
   through encrypted OpenTofu state.
6. Deploy and verify onboarding, login, MQTT, Cloudflare R2, HTTP promotion,
   local routing, tunnel routing, and every configured custom integration.
7. Inspect storage without printing credentials:

   ```sh
   kubectl --context kind-ha -n kind-ha exec deploy/kind-ha-homeassistant -- \
     python3 -c 'import json; d=json.load(open("/config/.storage/core.config_entries")); print("store", d["version"], d["minor_version"]); [print(e["domain"], e["version"], e["minor_version"], sorted(e["data"])) for e in d["data"]["entries"] if e["domain"] in ("mqtt", "cloudflare_r2")]'

   kubectl --context kind-ha -n kind-ha exec deploy/kind-ha-homeassistant -- \
     python3 -c 'import json; d=json.load(open("/config/.storage/http")); print(d["version"], d["minor_version"], sorted(d["data"]), sorted(d["data"]["stable"]))'

   kubectl --context kind-ha -n kind-ha exec deploy/kind-ha-homeassistant -- \
     python3 -c 'import json; d=json.load(open("/config/.storage/core.config")); print(d["version"], d["minor_version"], sorted(d["data"]))'
   ```

   Confirm that `configuration.yaml` has no `homeassistant:` block and that the
   UI permits editing Home information and both URLs.
8. Restart the Home Assistant Deployment and repeat login, integration, route,
   and log checks. This catches schemas accepted only during migration and
   verifies that remote custom-integration installation is repeatable.
9. When automatic R2 backups are enabled, verify only the expected R2 agent is
   selected, recurrence and retention match the inputs, protection matches
   whether a password was supplied, and `next_automatic_backup` is populated.
   Re-run the hook and confirm it preserves stored settings.
10. Test restore mode separately with a disposable native backup. Confirm that
    neither integration config flows nor owner seeding runs and that restored
    configuration survives another restart. Include a configured custom
    integration: `/config` must not contain read-only submounts that prevent
    Home Assistant from clearing it during restoration.
11. Create and restore a test backup through the Cloudflare R2 integration.
12. Confirm the Zigbee2MQTT snapshot CronJob shares Home Assistant's node,
    produces a valid archive without replacing the last good file on failure,
    and that a native backup and restore preserve the staged ZIP. Exercise the
    separate empty-volume Zigbee2MQTT recovery procedure before relying on it.
13. Update the README badge and the verified date, versions, source links, and
    observations in this document. Record only what was actually exercised.
14. Exercise the pinned Matter Server with the proposed Home Assistant client:
    fresh config flow, loaded state, idempotent hook, container restart, disabled
    mode, and recovery onto an empty Matter volume. Verify existing external
    Matter entries are preserved. Inspect only entry schema/key names and
    redacted state. Test device commissioning and reconnection on the physical
    LAN; Kind's WebSocket connection alone cannot verify multicast/IPv6.

Do not test an upgrade first against the production PVC. Preserve a native
Home Assistant backup, encrypted OpenTofu state, and the Zigbee network
identity before changing production.

## Baseline observations

The `2026.9.0` baseline was exercised with a fresh Kind cluster and PVC,
automated owner creation, real login and token revocation, MQTT and Cloudflare
R2 reporting `loaded`, storage checks, a rollout restart, a second idempotent
hook run, and local HTTP and WebSocket route checks.

The source audit found no payload changes in the onboarding, core settings,
config-entry lookup, MQTT broker, Cloudflare R2, or backup commands used by the
hook. Login-flow forms now use the newer field serializer, and IndieAuth has
expanded client metadata handling; the hook's identical client and redirect
origins continue to take the direct validation path. HTTP configuration now
rejects forwarded headers unless at least one trusted proxy is present. The
chart already supplies both settings together and the fresh-volume trial and
promotion succeeded.

MQTT and Cloudflare R2 creation were re-exercised on a blank PVC through their
native config flows. The resulting entries had fresh generated IDs and MQTT
included the flow-applied `transport` field. An in-place upgrade over the older
direct-storage baseline detected and preserved both entries. No private
storage seed is rendered by the chart.

HTTP reconciliation was exercised with zero Job retries: one run staged,
restarted into, and promoted a changed trusted-proxy set; a second restored the
declared set. Core settings were exercised without a `homeassistant:` YAML
block and survived rollout restart while remaining UI-editable.

Automatic backup seeding was exercised against the real test R2 bucket. It
selected only the intended R2 agent, used daily recurrence, Home Assistant's
randomized time, seven-copy retention, and a non-empty protection password. A
second hook run preserved the complete backup configuration. Native backup
objects were confirmed in the configured R2 prefix without exposing secrets.

Cloudflare R2 currently emits blocking-call warnings while botocore loads its
service data and CA bundle. The integration still reaches `loaded`; recheck
these warnings on upgrade instead of hiding them or treating them as proof of
failure.

An encrypted native backup was created through Home Assistant's R2 agent,
downloaded from the private bucket, uploaded through the blank-instance
onboarding flow, decrypted, and restored into a fresh Kind PVC. The restored
owner login, recorder database, MQTT and R2 entries, automatic backup settings,
core URLs, and a dedicated marker all survived. Normal seed-mode
reconciliation then completed without creating another owner.

Cloudflare Tunnel routing was not attached to the isolated compatibility
cluster. It was subsequently exercised on the recreated development
deployment: the public route returned the Home Assistant frontend, upgraded a
WebSocket connection, and completed a fresh owner login. The local Gateway
route and its WebSocket upgrade were also verified.

cloudflared `2026.8.3` and Zigbee2MQTT `2.14.1` were pinned after exercising
those exact images in Kind. Both Deployments became ready, the tunnel returned
the frontend and upgraded a WebSocket connection, and a fresh Zigbee2MQTT
snapshot Job completed through the broker after transient MQTT startup errors.

The Zigbee2MQTT snapshot CronJob was exercised against the live Kind test
broker. Required pod affinity placed it on Home Assistant's node, and the
documented MQTT request produced a valid staged ZIP. A forced MQTT failure
preserved the last valid archive and removed its temporary files. Inclusion in
a native R2 backup and recovery of the valid staged ZIP were verified. Import
into a new Zigbee2MQTT volume has not yet been exercised.

The chart installs HACS by default from a checksum-pinned release archive;
user-selected integrations remain opt-in. A checksum-pinned
remote integration was installed at runtime and survived the R2 backup and
restore test. The first restore attempt also exposed why read-only submounts
below `/config` are unsafe: Home Assistant must clear that directory during
startup restoration. The installer now writes and reconciles only its managed
directories on the PVC, leaving unrelated integrations alone. Managed removal
and checksum-pinned reinstallation were both exercised after the restore.
Runtime compatibility remains an obligation of whichever integrations a
deployment chooses.

## Matter addition (2026-09-11)

Home Assistant remains pinned to `2026.9.0`. Its Matter manifest requires
`matter-python-client==1.4.0`. The chart adds the maintained OHF
`ghcr.io/matter-js/matterjs-server:1.4.0` image; the retired Python server is not
used. The tested image manifest digest is
`sha256:54232d0d3e7dff5a54759469d2753399270412b4c30c55b31750a4595e4cb236`.

The source audit at the exact tags confirmed the Container user flow leads
directly to a `manual` form accepting `url`. It calls `MatterClient.connect()`
to validate the server, then stores `url`, `use_addon=false`, and
`integration_created_addon=false`. The same flow can update an existing entry,
so the hook queries all Matter entries first and preserves any existing one.
The server's image uses UID/GID 1000, supports loopback binding and a selected
primary Matter interface, and supplies `/usr/local/bin/healthcheck.sh`.

A new disposable Kind cluster and fresh Home Assistant and Matter PVCs were
used for a real seed-mode deployment. The hook created exactly one Matter
entry, schema `1.1`, and the integration reported `loaded`. The server reported
WebSocket schema `13`, minimum supported schema `11`. A second hook run
preserved the entry; after reconfiguring it through Home Assistant to a
separate test server, another hook run also preserved that URL and entry ID.
Disabling Matter removed its container while retaining its PVC; re-enabling
reused that claim and preserved the original fabric identity.

A full pod rollout preserved the entry and fabric identity. The server ran as
UID/GID 1000, and its control API was reachable on loopback but not through the
node's network address.

The bundled `kube4ha_matter_backup` integration uses Home Assistant's documented
pre/post-backup platform. Its seed-mode config entry is created through the
same private config-flow endpoint used elsewhere. A Unix socket shared only
between the two containers requests a bounded, graceful stop, validated archive,
and restart from the Matter supervisor. The runtime script resolves Kubernetes
ConfigMap symlinks when detecting its entrypoint; the fresh-cluster test caught
and corrected that startup requirement.

A real native Home Assistant backup included the generated Matter archive under
`/config/.kube4ha/matter/latest.tar.gz`, with format and image metadata. A forced
snapshot validation failure failed the native backup and restarted Matter; unit
tests also confirm a forced process kill cannot replace a valid snapshot.

The native upload endpoint rejected the 19 MiB test backup at its 16 MiB HTTP
body limit. For the complete restore test, the unchanged native archive was
placed in the fresh instance's local backup directory, Home Assistant was
restarted to refresh its agent inventory, and the native onboarding restore API
was used. This is an existing limitation of the pinned HTTP/upload path, not a
Matter archive size limit; the Matter snapshot itself was about 300 KiB.

The full native restore recovered Home Assistant's configuration and embedded
Matter snapshot into a fresh Home Assistant PVC. Returning to seed mode created
an empty Matter PVC and automatically restored the snapshot before starting the
server. The original Matter entry ID and hashed fabric identity were preserved,
the integration reached `loaded`, and a subsequent native backup succeeded.

The full `task check` suite includes unsafe/corrupt archive rejection, existing
fabric preservation, snapshot serialization, graceful flush ordering, process
restart after failure, and the default/disabled/restore Helm renderings.
The live deployment exposed a WebSocket close handshake that exceeded the
original 20-second shutdown limit. The supervisor now allows 60 seconds, covering
the pinned `ws` library's 30-second close timeout before Matter flushes storage.
A regression test exercises a 31-second graceful stop; the native backup hook
and pod termination budgets also cover shutdown and archive validation.
Restore-mode rendering contains no Matter process, initializer, or onboarding
hook. The live Matter PVC remains separate, while native backups include the
cold snapshot. See [BACKUP.md](BACKUP.md#restore-onto-a-new-cluster) for recovery.

Physical device commissioning, Thread routes, and device reconnection are
not verified by this Kind test. They require testing on the production LAN;
the Colima/Kind bridge and VM topology does not establish those capabilities.

## Matter isolation (2026-09-19)

Home Assistant stays pinned to `2026.9.0` and the Matter image to `1.4.0`. The
controller moved from a sidecar into its own `<release>-homeassistant-matter`
Deployment after a production incident: eleven Home Assistant rollouts in one
day, each restarting the sidecar, drove a Thread door lock from 30-second
reconnects to a Matter stack that stopped answering session handshakes while
its Thread and IP layers stayed reachable. The Matter server logged
`peer-unresponsive` on every CASE attempt; the recorder history showed each
outage matching a rollout and recovery times growing with each restart.

The new pod keeps host networking, the loopback bind, UID/GID 1000, the
retained fabric claim, and the supervisor. It is pinned to Home Assistant's
node by a required pod affinity because it mounts the ReadWriteOnce
configuration claim for snapshots, and the control socket moved from a pod
`emptyDir` to a node-local `hostPath` directory (mode 0700, owned by the Matter
user) so the Home Assistant container can still reach the supervisor. The
initializer now waits for a previous controller to release the loopback port
before opening the fabric volume, which covers the one-time migration from the
sidecar while the old Home Assistant pod is still terminating.

Verified with `task check`: default, disabled, restore, and backup-disabled
renderings; the Home Assistant Deployment no longer carries the Matter
container, initializer, volumes, or rollout annotation; the Service selects
only Home Assistant pods; the initializer's port wait. The Matter integration's
reconnect behavior was audited in the pinned source: setup raises
`ConfigEntryNotReady` while the server is unreachable, and the entry reloads
itself when the client's listener disconnects. Production verification of the
migration is recorded in the private deployment repository.

## Online Matter snapshots (2026-09-19)

The pre-backup snapshot no longer stops the controller. The audit of the
pinned image (matter.js 0.17.9, `@matter/general` WAL storage) confirmed the
storage contract recorded above: `WalWriter` only appends to the newest
segment with `fsync`, rotated segments are compressed through a `.tmp` name and
then removed, `WalSnapshot.save` writes `snapshot.tmp.json.gz` and renames it,
and segment deletion runs only on close. `WalReader` skips malformed lines with
a warning and `WalStorageDriver` replays only commits after the loaded
snapshot's commit ID. The supervisor therefore copies subdirectories before
files, so `wal/` precedes `snapshot.json.gz`, excludes `matter.lock`,
`matter.pid`, and `*.tmp*`, retries the copy if a segment vanishes between
listing and copying, and archives the copy. The stale-lock handling in
`lock-utils.js` would also tolerate an included lock, but the copy omits it.

Verified with `task check`: the supervisor test suite exercises a live
snapshot with the child never receiving `SIGTERM`, rejection of concurrent
requests, failure preserving the previous archive while Matter keeps running,
transient-file exclusion, and graceful shutdown. Restoring an online snapshot
into an empty volume has not yet been exercised on the production LAN; record
that test in the private deployment repository when it happens.
