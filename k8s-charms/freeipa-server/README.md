# FreeIPA Server Charm (Kubernetes)

A [Juju](https://juju.is) Kubernetes charm that deploys [FreeIPA](https://www.freeipa.org)
using the official `freeipa/freeipa-server` container image via Pebble sidecar.
Provides centralized identity, authentication (Kerberos), authorization (LDAP), DNS,
and certificate management.

## How It Works

This is the Kubernetes equivalent of the [machine freeipa-server charm](../../charms/freeipa-server/).
Instead of managing Docker directly, it uses Juju's Pebble sidecar pattern:

1. Waits for the Pebble sidecar to become ready
2. Patches the FreeIPA init script for systemd 257+ compatibility
3. Adds a Pebble layer to configure and start the FreeIPA service
4. Uses persistent storage mounted at `/data` for FreeIPA data, config, and logs
5. Publishes LDAP and FreeIPA relation data for downstream charms

## Prerequisites

- [Juju](https://juju.is/docs/juju/install-juju) 3.5+ with a Kubernetes cloud
- A Kubernetes cluster (MicroK8s, EKS, GKE, etc.)
- Sufficient storage (minimum 2G) for FreeIPA data

## Build

From the project root:

```bash
just build k8s:freeipa-server
```

Or manually:

```bash
cd k8s-charms/freeipa-server
charmcraft pack
```

This produces `freeipa-server-k8s_amd64.charm`.

## Deploy

### Step 1: Set up a Kubernetes Juju model

```bash
juju add-model freeipa-k8s
```

### Step 2: Create a Juju secret for the admin password

```bash
juju add-secret freeipa-admin password=ChangeMeNow123
# => secret:abc123...

juju grant-secret freeipa-admin freeipa-server-k8s
```

### Step 3: Deploy the charm

```bash
juju deploy ./freeipa-server-k8s_amd64.charm \
  --config domain=freeipa.local \
  --config realm=FREEIPA.LOCAL \
  --config admin-password=secret:abc123... \
  --resource freeipa-image=freeipa/freeipa-server:almalinux-9
```

### Step 4: Wait for installation

```bash
juju status --watch 5s
```

The initial FreeIPA installation takes approximately 10-15 minutes.

### Step 5: Verify

```bash
juju ssh --container freeipa freeipa-server-k8s/0 -- ipa --version
```

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `domain` | string | *(required)* | Primary DNS domain (e.g. `freeipa.local`) |
| `realm` | string | *(from domain)* | Kerberos realm (e.g. `FREEIPA.LOCAL`) |
| `admin-password` | secret | *(required)* | Juju secret URI with the IPA admin password |
| `setup-dns` | boolean | `true` | Enable the integrated DNS server |
| `dns-forwarders` | string | `8.8.8.8` | Comma-separated DNS forwarder IPs |
| `no-ntp` | boolean | `true` | Disable NTP configuration |
| `extra-install-opts` | string | | Additional `ipa-server-install` flags |

## Resources

| Resource | Type | Upstream | Description |
|----------|------|----------|-------------|
| `freeipa-image` | oci-image | `freeipa/freeipa-server:almalinux-9` | FreeIPA server container image |

## Storage

| Storage | Type | Minimum | Mount | Description |
|---------|------|---------|-------|-------------|
| `data` | filesystem | 2G | `/data` | Persistent storage for FreeIPA data, config, and logs |

## Relations

### Provides

| Relation | Interface | Description |
|----------|-----------|-------------|
| `ldap` | `ldap` | LDAP connection details (URL, base DN, ports) |
| `freeipa` | `freeipa` | FreeIPA enrollment info (hostname, domain, realm) |

### Integrating with other charms

```bash
juju integrate keycloak-k8s:freeipa freeipa-server-k8s:freeipa
```

## Differences from Machine Charm

| Feature | Machine Charm | K8s Charm |
|---------|--------------|-----------|
| Container runtime | Docker (self-managed) | Pebble sidecar (Juju-managed) |
| Data location | `/srv/freeipa-data` | `/data` (persistent storage) |
| Image config | `image` config option | `freeipa-image` OCI resource |
| Port management | `open-port` calls | Kubernetes service |
| Host prep | Stops systemd-resolved | N/A (no host conflict) |

## Troubleshooting

### Pod not starting

Check Kubernetes events:

```bash
kubectl -n <model-name> describe pod freeipa-server-k8s-0
kubectl -n <model-name> logs freeipa-server-k8s-0 -c freeipa
```

### Storage issues

Ensure your Kubernetes cluster has a default StorageClass:

```bash
kubectl get storageclass
```

### Secret not found

```bash
juju grant-secret freeipa-admin freeipa-server-k8s
```
