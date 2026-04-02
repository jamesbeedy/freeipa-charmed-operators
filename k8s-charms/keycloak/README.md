# Keycloak Charm (Kubernetes)

A [Juju](https://juju.is) Kubernetes charm that deploys [Keycloak](https://www.keycloak.org/)
via Pebble sidecar and configures it with LDAP user federation against a
[FreeIPA](https://www.freeipa.org) server. Provides OpenID Connect (OIDC), OAuth 2.0,
and SAML 2.0 identity brokering.

## How It Works

This is the Kubernetes equivalent of the [machine keycloak charm](../../charms/keycloak/).
Instead of managing Docker directly, it uses Juju's Pebble sidecar pattern:

1. Waits for the Pebble sidecar to become ready
2. Renders a `realm.json` template with FreeIPA LDAP federation settings
3. Pushes the realm config into the running container
4. Starts Keycloak with `start-dev --import-realm`
5. Provides the `create-user` action for user management via FreeIPA's JSON-RPC API

## Prerequisites

- [Juju](https://juju.is/docs/juju/install-juju) 3.5+ with a Kubernetes cloud
- A running FreeIPA server (machine or K8s charm)
- Network connectivity from the Keycloak pod to the FreeIPA server

## Build

From the project root:

```bash
just build k8s:keycloak
```

Or manually:

```bash
cd k8s-charms/keycloak
charmcraft pack
```

This produces `keycloak-k8s_amd64.charm`.

## Deploy

### Step 1: Create Juju secrets

```bash
# Keycloak admin password
juju add-secret keycloak-admin password=Keycloak2025!
# => secret:def456...

# FreeIPA admin password
juju add-secret freeipa-admin password=ChangeMeNow123
# => secret:abc123...

juju grant-secret keycloak-admin keycloak-k8s
juju grant-secret freeipa-admin keycloak-k8s
```

### Step 2: Deploy the charm

**Option A: Config-based**

```bash
juju deploy ./keycloak-k8s_amd64.charm \
  --config admin-password=secret:def456... \
  --config freeipa-server=freeipa-server.freeipa.local \
  --config freeipa-domain=freeipa.local \
  --config freeipa-admin-password=secret:abc123... \
  --resource keycloak-image=quay.io/keycloak/keycloak:latest
```

**Option B: Relation-based**

```bash
juju deploy ./keycloak-k8s_amd64.charm \
  --config admin-password=secret:def456... \
  --config freeipa-admin-password=secret:abc123... \
  --resource keycloak-image=quay.io/keycloak/keycloak:latest

juju integrate keycloak-k8s:freeipa freeipa-server-k8s:freeipa
```

### Step 3: Verify

```bash
juju status --watch 5s
# Wait for keycloak-k8s to show "active"
```

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `admin-username` | string | `admin` | Keycloak bootstrap admin username |
| `admin-password` | secret | *(required)* | Juju secret URI with Keycloak admin password |
| `realm-name` | string | `freeipa` | Keycloak realm name |
| `freeipa-server` | string | | FreeIPA server FQDN or IP (or via `freeipa` relation) |
| `freeipa-domain` | string | | FreeIPA domain (or via relation) |
| `freeipa-admin-password` | secret | *(required)* | Juju secret URI with FreeIPA admin password |
| `http-port` | int | `8080` | HTTP port |
| `https-port` | int | `8443` | HTTPS port |

## Resources

| Resource | Type | Upstream | Description |
|----------|------|----------|-------------|
| `keycloak-image` | oci-image | `quay.io/keycloak/keycloak:latest` | Keycloak container image |

## Actions

### create-user

Create a user in FreeIPA via its JSON-RPC API, then trigger a Keycloak LDAP sync.

```bash
juju run keycloak-k8s/0 create-user \
  username=jdoe \
  first-name=John \
  last-name=Doe \
  email=jdoe@example.com \
  password=SecurePass123
```

| Parameter | Required | Description |
|-----------|----------|-------------|
| `username` | yes | Username for the new user |
| `first-name` | yes | First name |
| `last-name` | yes | Last name |
| `email` | no | Email address |
| `password` | yes | Initial password |

## Relations

### Provides

| Relation | Interface | Description |
|----------|-----------|-------------|
| `oidc` | `oidc` | OIDC/OAuth2 federation endpoint |

### Requires

| Relation | Interface | Description |
|----------|-----------|-------------|
| `freeipa` | `freeipa` | FreeIPA server connection info (optional; can use config) |

## Differences from Machine Charm

| Feature | Machine Charm | K8s Charm |
|---------|--------------|-----------|
| Container runtime | Docker (self-managed) | Pebble sidecar (Juju-managed) |
| Realm config | Mounted as Docker volume | Pushed via Pebble `push` |
| Image config | `image` config option | `keycloak-image` OCI resource |
| Actions | `create-user`, `set-user-ssh-key` | `create-user` |

## Troubleshooting

### Pod not starting

```bash
kubectl -n <model-name> describe pod keycloak-k8s-0
kubectl -n <model-name> logs keycloak-k8s-0 -c keycloak
```

### Waiting for freeipa config

Set config or add relation:

```bash
juju config keycloak-k8s freeipa-server=<freeipa-ip>
juju config keycloak-k8s freeipa-domain=freeipa.local
```

### Secret not found

```bash
juju grant-secret keycloak-admin keycloak-k8s
juju grant-secret freeipa-admin keycloak-k8s
```
