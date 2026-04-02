# CephFS Share Proxy Charm (Kubernetes)

A [Juju](https://juju.is) Kubernetes charm that acts as a proxy for an externally
managed [CephFS](https://docs.ceph.com/en/latest/cephfs/) filesystem. Configure it
with the Ceph cluster connection details and it publishes them via the `filesystem_info`
interface for downstream consumers.

## How It Works

Unlike the [machine cephfs-share charm](../../charms/cephfs-share/) which runs its own
MicroCeph cluster, this charm is a **pure configuration proxy** with no workload container.
It takes Ceph connection parameters as config and publishes them as a CephFS URI on
the `filesystem` relation:

```
cephfs://user@(mon1,mon2,...)/path?fsid=X&name=Y&auth=plain:KEY
```

This is designed for Kubernetes environments where Ceph is managed externally
(e.g. Ceph Rook, a standalone Ceph cluster, or a MicroCeph deployment outside Juju).

## Prerequisites

- [Juju](https://juju.is/docs/juju/install-juju) 3.5+ with a Kubernetes cloud
- An existing Ceph cluster with CephFS configured
- Ceph client credentials (cephx key) authorized for CephFS access

## Build

From the project root:

```bash
just build k8s:cephfs-share
```

Or manually:

```bash
cd k8s-charms/cephfs-share
charmcraft pack
```

This produces `cephfs-share-k8s_amd64.charm`.

## Deploy

### Step 1: Gather Ceph cluster info

From your existing Ceph cluster, collect the following:

```bash
# Get the cluster FSID
ceph fsid
# => a1b2c3d4-e5f6-7890-abcd-ef1234567890

# Get monitor addresses
ceph mon dump -f json | jq -r '.mons[] | "\(.public_addr)"'
# => 10.0.0.1:6789
# => 10.0.0.2:6789

# Get the client key
ceph auth get-key client.fs-client
# => AQBx...base64key...==

# Get the filesystem name
ceph fs ls
# => name: cephfs, ...
```

### Step 2: Deploy the charm

```bash
juju deploy ./cephfs-share-k8s_amd64.charm \
  --config fsid=a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  --config monitor-hosts="10.0.0.1:6789 10.0.0.2:6789" \
  --config client-key="AQBx...base64key...==" \
  --config fs-name=cephfs \
  --config client-user=fs-client \
  --config share-path=/
```

### Step 3: Verify

```bash
juju status --watch 5s
# Should show "active" once all required config is provided
```

### Step 4: Integrate with consumers

```bash
juju integrate filesystem-client:filesystem cephfs-share-k8s:filesystem
```

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `fsid` | string | *(required)* | Ceph cluster FSID (UUID) |
| `monitor-hosts` | string | *(required)* | Space-separated list of monitor `host:port` addresses |
| `client-key` | string | *(required)* | Base64 cephx key for the authorized client user |
| `fs-name` | string | `cephfs` | CephFS filesystem name |
| `share-path` | string | `/` | Path within CephFS to export |
| `client-user` | string | `fs-client` | Ceph username authorized to access the filesystem |

All three required fields (`fsid`, `monitor-hosts`, `client-key`) must be set before
the charm becomes active. Until they are provided, the charm will show `blocked` status.

## Relations

### Provides

| Relation | Interface | Description |
|----------|-----------|-------------|
| `filesystem` | `filesystem_info` | CephFS connection URI for consumers |

### Peers

| Relation | Interface | Description |
|----------|-----------|-------------|
| `cephfs-peers` | `cephfs_peers` | Peer communication |

## Differences from Machine Charm

| Feature | Machine Charm | K8s Charm |
|---------|--------------|-----------|
| Ceph management | Runs MicroCeph locally | Proxy to external Ceph |
| OSD devices | Creates loop-backed OSDs | None (no local storage) |
| Configuration | Minimal (auto-discovers cluster info) | Requires FSID, monitors, key |
| Use case | Self-contained single-node Ceph | External/managed Ceph clusters |

## Troubleshooting

### Charm shows "blocked: missing required config"

Set all required config options:

```bash
juju config cephfs-share-k8s \
  fsid=<your-fsid> \
  monitor-hosts="<mon1:port> <mon2:port>" \
  client-key="<base64-cephx-key>"
```

### Consumers can't mount

Verify the CephFS URI is being published correctly by checking the relation data:

```bash
juju show-unit cephfs-share-k8s/0 --format json | python3 -c "
import sys, json
data = json.load(sys.stdin)
for rel in data.get('cephfs-share-k8s/0', {}).get('relation-info', []):
    if rel.get('endpoint') == 'filesystem':
        print(json.dumps(rel, indent=2))
"
```

### Monitor hosts unreachable

Ensure the Kubernetes pods can reach the Ceph monitor addresses. Check network
policies and firewall rules.
