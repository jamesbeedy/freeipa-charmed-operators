# CephFS Share Charm

A [Juju](https://juju.is) machine charm that installs [MicroCeph](https://canonical-microceph.readthedocs-hosted.com/)
via snap, creates a [CephFS](https://docs.ceph.com/en/latest/cephfs/) filesystem, and
exposes it via the `filesystem_info` interface. Designed for providing shared home
directories backed by CephFS.

## How It Works

This charm sets up a single-node Ceph cluster with CephFS:

1. Installs the MicroCeph snap (configurable channel)
2. Bootstraps a single-node MicroCeph cluster
3. Creates loop-backed OSD devices (configurable count and size)
4. Creates CephFS metadata and data pools, then creates the CephFS filesystem
5. Authorizes a client user for CephFS access
6. Publishes CephFS connection info (as a URI) on the `filesystem` relation

The CephFS URI format is:
```
cephfs://user@(mon1,mon2,...)/path?fsid=X&name=Y&auth=plain:KEY
```

Downstream consumers (e.g. the `filesystem-client` charm) use this URI to mount
the CephFS on client machines.

## Prerequisites

- [Juju](https://juju.is/docs/juju/install-juju) 3.5+
- A machine with sufficient disk space for loop-backed OSDs (default: 3 x 4G = 12G)

## Build

From the project root:

```bash
just build cephfs-share
```

Or manually:

```bash
cd charms/cephfs-share
charmcraft pack
```

This produces `cephfs-share_amd64.charm`.

## Deploy

### Step 1: Deploy the charm

No secrets or special config required for basic usage:

```bash
juju deploy ./cephfs-share_amd64.charm
```

With custom OSD configuration:

```bash
juju deploy ./cephfs-share_amd64.charm \
  --config osd-count=3 \
  --config osd-size=8G \
  --config fs-name=shared-home
```

### Step 2: Wait for CephFS to be ready

```bash
juju status --watch 5s
# Wait for cephfs-share to show "active: cephfs ready"
```

### Step 3: Integrate with filesystem-client

Deploy the `filesystem-client` subordinate charm to mount CephFS on client machines:

```bash
# Deploy filesystem-client
juju deploy filesystem-client --channel latest/edge \
  --config mountpoint=/home

# Attach to a principal machine charm (must be a VM for kernel mount support)
juju integrate filesystem-client:juju-info ubuntu:juju-info

# Connect to cephfs-share
juju integrate filesystem-client:filesystem cephfs-share:filesystem
```

### Step 4: Verify the mount

```bash
juju ssh ubuntu/0 -- "df -h /home"
# Should show CephFS mounted at /home

juju ssh ubuntu/0 -- "mount | grep ceph"
```

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `fs-name` | string | `cephfs` | Name of the CephFS filesystem to create |
| `client-user` | string | `fs-client` | Ceph username for client access |
| `share-path` | string | `/` | Path within CephFS to export |
| `osd-size` | string | `4G` | Size of each loop-backed OSD device |
| `osd-count` | int | `3` | Number of loop-backed OSD devices |
| `microceph-channel` | string | `squid/stable` | Snap channel for MicroCeph |

### Adjusting OSD size

For production or larger deployments, increase the OSD size:

```bash
juju config cephfs-share osd-size=20G osd-count=3
```

## Relations

### Provides

| Relation | Interface | Description |
|----------|-----------|-------------|
| `filesystem` | `filesystem_info` | CephFS connection URI for consumers |

### Peers

| Relation | Interface | Description |
|----------|-----------|-------------|
| `cephfs-peers` | `cephfs_peers` | Peer communication (for future multi-node clustering) |

### Integrating with filesystem-client

```bash
juju integrate filesystem-client:filesystem cephfs-share:filesystem
```

## Important Notes

### VM requirement for clients

The `filesystem-client` charm that mounts CephFS requires kernel module access.
Ubuntu units must be deployed as **VMs**, not LXD containers:

```bash
juju deploy ubuntu --base ubuntu@24.04 \
  --constraints virt-type=virtual-machine
```

### Single-node deployment

This charm is designed for single-node Ceph deployments using loop-backed devices.
It is suitable for development, testing, and small-scale production use. For
large-scale production, consider using a dedicated Ceph cluster with the
[cephfs-share-k8s](../../k8s-charms/cephfs-share/) proxy charm.

## Troubleshooting

### CephFS not becoming ready

Check MicroCeph status:

```bash
juju ssh cephfs-share/0 -- "sudo microceph status"
juju ssh cephfs-share/0 -- "sudo microceph.ceph -s"
```

### OSD creation failed

Check available disk space (each OSD needs a loop device):

```bash
juju ssh cephfs-share/0 -- "df -h /"
```

### filesystem-client blocked on LXD containers

Ubuntu units must be VMs:

```bash
juju deploy ubuntu --base ubuntu@24.04 \
  --constraints virt-type=virtual-machine
```

### Mount not working

Check the filesystem-client logs and the CephFS URI:

```bash
juju ssh ubuntu/0 -- "sudo journalctl -u jujud-unit-filesystem-client-0 | tail -20"
```
