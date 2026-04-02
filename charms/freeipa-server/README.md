# FreeIPA Server Charm

A [Juju](https://juju.is) machine charm that deploys [FreeIPA](https://www.freeipa.org)
using the official `freeipa/freeipa-server` container image via Docker. FreeIPA provides
centralized identity, authentication (Kerberos), authorization (LDAP), DNS, and
certificate management for Linux/UNIX environments.

## How It Works

This charm installs Docker on the host machine, pulls the FreeIPA server OCI image
(AlmaLinux-based), and manages the FreeIPA container lifecycle. On install, it:

1. Installs Docker CE and pulls the configured OCI image
2. Stops `systemd-resolved` to free port 53 for FreeIPA's integrated DNS
3. Patches the FreeIPA init script for systemd 257+ compatibility
4. Starts the FreeIPA container with the configured domain, realm, and admin password
5. Opens ports: 80, 443 (HTTP/HTTPS), 389, 636 (LDAP/LDAPS), 88, 464 (Kerberos), 53 (DNS)
6. Publishes LDAP and FreeIPA relation data for downstream charms

The initial FreeIPA server installation takes approximately 10-15 minutes.

## Prerequisites

- [Juju](https://juju.is/docs/juju/install-juju) 3.5+ with a LXD cloud
- [LXD](https://documentation.ubuntu.com/lxd/) initialized with nesting enabled
- [charmcraft](https://snapcraft.io/charmcraft) for building
- [just](https://just.systems/) (optional, for using the project task runner)

## Build

From the project root:

```bash
just build freeipa-server
```

Or manually:

```bash
cd charms/freeipa-server
charmcraft pack
```

This produces `freeipa-server_amd64.charm`.

## Deploy

### Step 1: Prepare the Juju model

Ensure LXD nesting is enabled (required for Docker-in-LXD):

```bash
juju add-model freeipa-dev
PROFILE=$(lxc profile list -f csv | grep juju- | head -1 | cut -d, -f1)
lxc profile set "$PROFILE" security.nesting=true
```

### Step 2: Create a Juju secret for the admin password

```bash
juju add-secret freeipa-admin password=ChangeMeNow123
# => secret:abc123...

juju grant-secret freeipa-admin freeipa-server
```

### Step 3: Deploy the charm

```bash
juju deploy ./freeipa-server_amd64.charm \
  --config domain=freeipa.local \
  --config realm=FREEIPA.LOCAL \
  --config admin-password=secret:abc123...
```

### Step 4: Wait for installation to complete

```bash
juju status --watch 5s
```

The charm will show `maintenance` during installation (~15 minutes) and transition
to `active` once FreeIPA is fully configured.

### Step 5: Verify

```bash
# Check the FreeIPA container logs
juju ssh freeipa-server/0 -- "sudo docker logs freeipa-server 2>&1 | tail -20"

# Verify FreeIPA is running
juju ssh freeipa-server/0 -- "sudo docker exec freeipa-server ipa --version"
```

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `domain` | string | *(required)* | Primary DNS domain (e.g. `freeipa.local`) |
| `realm` | string | *(from domain)* | Kerberos realm (e.g. `FREEIPA.LOCAL`). Derived from domain if not set |
| `admin-password` | secret | *(required)* | Juju secret URI containing the IPA admin password |
| `setup-dns` | boolean | `true` | Enable the integrated DNS server |
| `dns-forwarders` | string | `8.8.8.8` | Comma-separated DNS forwarder IPs. Empty string for `--no-forwarders` |
| `no-ntp` | boolean | `true` | Disable NTP configuration (recommended for containers) |
| `extra-install-opts` | string | | Additional `ipa-server-install` flags (space-separated) |
| `image` | string | `docker.io/freeipa/freeipa-server:almalinux-10` | FreeIPA server OCI image |
| `container-name` | string | `freeipa-server` | Docker container name |

### Changing configuration after deploy

```bash
juju config freeipa-server dns-forwarders="8.8.8.8,1.1.1.1"
```

### Rotating the admin password

```bash
juju secret-set freeipa-admin password=NewSecurePassword
# All charms using this secret pick up the change automatically
```

## Relations

### Provides

| Relation | Interface | Description |
|----------|-----------|-------------|
| `ldap` | `ldap` | LDAP connection details (URL, base DN, ports) |
| `freeipa` | `freeipa` | FreeIPA enrollment info (hostname, domain, realm, admin secret ID) |

### Integrating with other charms

```bash
# Connect Keycloak for LDAP user federation
juju integrate keycloak:freeipa freeipa-server:freeipa

# Connect any charm needing LDAP
juju integrate <app>:ldap freeipa-server:ldap
```

## Troubleshooting

### Server stuck in maintenance

The initial install takes 10-15 minutes. Check progress:

```bash
juju ssh freeipa-server/0 -- "sudo docker logs freeipa-server 2>&1 | tail -30"
```

### Port 53 conflict

The charm stops `systemd-resolved` to free port 53. If another service is using
port 53, the container will fail to start. Check with:

```bash
juju ssh freeipa-server/0 -- "sudo ss -tlnp | grep :53"
```

### Container not starting

```bash
# Check Docker status
juju ssh freeipa-server/0 -- "sudo docker ps -a"

# Check container logs
juju ssh freeipa-server/0 -- "sudo docker logs freeipa-server 2>&1"
```

### Secret not found

Ensure the secret is granted to the application:

```bash
juju grant-secret freeipa-admin freeipa-server
```
