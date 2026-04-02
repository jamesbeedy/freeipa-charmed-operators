# Keycloak Charm

A [Juju](https://juju.is) machine charm that deploys [Keycloak](https://www.keycloak.org/)
via Docker and configures it with LDAP user federation against a
[FreeIPA](https://www.freeipa.org) server. Keycloak provides OpenID Connect (OIDC),
OAuth 2.0, and SAML 2.0 identity brokering, backed by FreeIPA's LDAP directory for
user storage.

## How It Works

This charm installs Docker on the host, pulls the Keycloak OCI image, and manages
the Keycloak container with a pre-configured realm for FreeIPA LDAP federation:

1. Installs Docker CE and pulls the configured Keycloak image
2. Renders a `realm.json` template with FreeIPA LDAP connection settings
3. Starts Keycloak with `start-dev --import-realm`, mounting the realm config
4. Configures LDAP user federation with attribute mapping (uid, givenName, sn, mail)
5. Provides `create-user` and `set-user-ssh-key` actions for user management
6. Publishes OIDC endpoint info via the `oidc` relation

The LDAP federation is configured in **WRITABLE** mode with sync registrations enabled,
meaning users created through Keycloak are written through to FreeIPA's LDAP directory.

## Prerequisites

- [Juju](https://juju.is/docs/juju/install-juju) 3.5+ with a LXD cloud
- [LXD](https://documentation.ubuntu.com/lxd/) initialized with nesting enabled
- A running FreeIPA server (see the [freeipa-server charm](../freeipa-server/))
- Network connectivity to the FreeIPA server

## Build

From the project root:

```bash
just build keycloak
```

Or manually:

```bash
cd charms/keycloak
charmcraft pack
```

This produces `keycloak_amd64.charm`.

## Deploy

### Step 1: Create Juju secrets

```bash
# Keycloak admin password
juju add-secret keycloak-admin password=Keycloak2025!
# => secret:def456...

# FreeIPA admin password (use the same secret as freeipa-server, or create a new one)
juju add-secret freeipa-admin password=ChangeMeNow123
# => secret:abc123...

# Grant secrets to keycloak
juju grant-secret keycloak-admin keycloak
juju grant-secret freeipa-admin keycloak
```

### Step 2: Deploy the charm

You can provide FreeIPA config either via config options or via a relation.

**Option A: Config-based (no relation required)**

```bash
juju deploy ./keycloak_amd64.charm \
  --config admin-password=secret:def456... \
  --config freeipa-server=freeipa-server.freeipa.local \
  --config freeipa-domain=freeipa.local \
  --config freeipa-admin-password=secret:abc123...
```

**Option B: Relation-based (automatic config from freeipa-server)**

```bash
juju deploy ./keycloak_amd64.charm \
  --config admin-password=secret:def456... \
  --config freeipa-admin-password=secret:abc123...

juju integrate keycloak:freeipa freeipa-server:freeipa
```

### Step 3: Ensure DNS resolution

The Keycloak machine must resolve the FreeIPA server hostname:

```bash
SERVER_IP=$(juju status freeipa-server/0 --format json | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['applications']['freeipa-server']['units']['freeipa-server/0']['public-address'])")

juju ssh keycloak/0 -- "echo '$SERVER_IP freeipa-server.freeipa.local' | sudo tee -a /etc/hosts"
```

### Step 4: Verify

```bash
juju status --watch 5s
# Wait for keycloak to show "active"

# Access the Keycloak admin console
KEYCLOAK_IP=$(juju status keycloak/0 --format json | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['applications']['keycloak']['units']['keycloak/0']['public-address'])")

echo "Keycloak admin console: http://$KEYCLOAK_IP:8080"
```

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `admin-username` | string | `admin` | Keycloak bootstrap admin username |
| `admin-password` | secret | *(required)* | Juju secret URI with Keycloak admin password |
| `realm-name` | string | `freeipa` | Keycloak realm name |
| `freeipa-server` | string | | FreeIPA server FQDN or IP (or via `freeipa` relation) |
| `freeipa-domain` | string | | FreeIPA domain, e.g. `freeipa.local` (or via relation) |
| `freeipa-admin-password` | secret | *(required)* | Juju secret URI with FreeIPA admin password for LDAP bind |
| `image` | string | `quay.io/keycloak/keycloak:latest` | Keycloak OCI image |
| `container-name` | string | `keycloak` | Docker container name |
| `http-port` | int | `8080` | HTTP port |
| `https-port` | int | `8443` | HTTPS port |

## Actions

### create-user

Create a user in FreeIPA via its JSON-RPC API, then trigger a Keycloak LDAP sync.
The user gets a `/bin/bash` shell and is immediately available on all enrolled
FreeIPA client machines.

```bash
juju run keycloak/0 create-user \
  username=jdoe \
  first-name=John \
  last-name=Doe \
  email=jdoe@example.com \
  password=SecurePass123
```

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `username` | yes | Username for the new user |
| `first-name` | yes | First name |
| `last-name` | yes | Last name |
| `email` | no | Email address |
| `password` | yes | Initial password |

Verify the user on a client machine:

```bash
juju ssh ubuntu/0 -- id jdoe
# uid=66800003(jdoe) gid=66800003(jdoe) groups=66800003(jdoe)
```

### set-user-ssh-key

Add an SSH public key to a FreeIPA user. The key is stored in FreeIPA's LDAP
(`ipasshpubkey` attribute) and automatically served to `sshd` on all enrolled
clients via SSSD's `sss_ssh_authorizedkeys`.

```bash
juju run keycloak/0 set-user-ssh-key \
  username=jdoe \
  ssh-key="ssh-ed25519 AAAA...xyz user@laptop"
```

**Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `username` | yes | FreeIPA username |
| `ssh-key` | yes | SSH public key (e.g. `ssh-ed25519 AAAA... user@host`) |

After a brief SSSD cache refresh, SSH key auth works:

```bash
ssh -i ~/.ssh/id_ed25519 jdoe@<ubuntu-unit-ip>
```

## Relations

### Provides

| Relation | Interface | Description |
|----------|-----------|-------------|
| `oidc` | `oidc` | OIDC/OAuth2 federation endpoint |

### Requires

| Relation | Interface | Description |
|----------|-----------|-------------|
| `freeipa` | `freeipa` | FreeIPA server connection info (optional; can use config instead) |

### Integrating with other charms

```bash
# Get FreeIPA config automatically via relation
juju integrate keycloak:freeipa freeipa-server:freeipa

# Provide OIDC to a consuming charm
juju integrate <app>:oidc keycloak:oidc
```

## Troubleshooting

### Keycloak shows "waiting for freeipa config"

Either set the config manually:

```bash
juju config keycloak freeipa-server=freeipa-server.freeipa.local
juju config keycloak freeipa-domain=freeipa.local
juju config keycloak freeipa-admin-password=secret:<id>
```

Or use the relation:

```bash
juju integrate keycloak:freeipa freeipa-server:freeipa
```

### LDAP federation not syncing

Trigger a manual sync via the `create-user` action, or check the Keycloak logs:

```bash
juju ssh keycloak/0 -- "sudo docker logs keycloak 2>&1 | tail -30"
```

### Secret not found

```bash
juju grant-secret keycloak-admin keycloak
juju grant-secret freeipa-admin keycloak
```

### Cannot reach FreeIPA server

Ensure the hostname resolves from the Keycloak machine:

```bash
juju ssh keycloak/0 -- "getent hosts freeipa-server.freeipa.local"
```
