# FreeIPA Charmed Operators

A suite of [Juju](https://juju.is) charms that deploy a complete identity
management and shared storage stack: [FreeIPA](https://www.freeipa.org)
for centralized identity, [Keycloak](https://www.keycloak.org/) for OIDC
federation, and [CephFS](https://docs.ceph.com/en/latest/cephfs/) for
shared home directories.

The project provides both **machine charms** (Docker-based, for LXD/bare metal)
and **Kubernetes charms** (Pebble sidecar-based) for flexible deployment across
environments.

## Architecture

```
                     ┌─────────────────────────┐
                     │     freeipa-server       │  Machine charm (Docker)
                     │  FreeIPA IdM + LDAP + KDC│  Ports: 80,443,389,636,88,464,53
                     │  (provides: freeipa,ldap)│
                     └────────┬────────┬────────┘
                              │        │
              freeipa relation│        │freeipa relation
              (hostname,      │        │(hostname,
               domain, realm) │        │ domain, realm)
                              │        │
              ┌───────────────┘        └───────────────┐
              │                                        │
   ┌──────────▼──────────┐                  ┌──────────▼──────────┐
   │      keycloak       │                  │   ubuntu (VM)       │
   │  Keycloak IdP       │                  │   Principal charm   │
   │  LDAP federation    │                  ├─────────────────────┤
   │  OIDC/SAML provider │                  │  freeipa-client     │ Subordinate
   │  (provides: oidc)   │                  │  (enrolls host via  │ (juju-info)
   │                     │                  │   ipa-client-install)│
   │  Actions:           │                  ├─────────────────────┤
   │  - create-user      │                  │  filesystem-client  │ Subordinate
   │  - set-user-ssh-key │                  │  (mounts CephFS     │ (juju-info)
   └─────────────────────┘                  │   at /home)         │
                                            └──────────▲──────────┘
                                                       │
                                            filesystem_info relation
                                                       │
                                            ┌──────────┴──────────┐
                                            │    cephfs-share     │
                                            │  MicroCeph + CephFS │
                                            │  (provides:         │
                                            │   filesystem)       │
                                            └─────────────────────┘
```

**Data flow:** Users are created in FreeIPA (via Keycloak action) and synced
to Keycloak's LDAP federation. Enrolled machines resolve users via SSSD.
SSH public keys are stored in FreeIPA LDAP and served to `sshd` via
`sss_ssh_authorizedkeys`. Home directories live on CephFS, shared across
all enrolled machines.

## Charms

### Machine charms (`charms/`)

| Charm | Type | Description |
|-------|------|-------------|
| [freeipa-server](charms/freeipa-server/) | Principal | FreeIPA server via Docker (AlmaLinux container) |
| [freeipa-client](charms/freeipa-client/) | Subordinate | Enrolls machines with FreeIPA, hardens SSH |
| [keycloak](charms/keycloak/) | Principal | Keycloak IdP with FreeIPA LDAP user federation |
| [cephfs-share](charms/cephfs-share/) | Principal | MicroCeph single-node CephFS for shared `/home` |

### Kubernetes charms (`k8s-charms/`)

| Charm | Type | Description |
|-------|------|-------------|
| [freeipa-server-k8s](k8s-charms/freeipa-server/) | Principal | FreeIPA server via Pebble sidecar |
| [keycloak-k8s](k8s-charms/keycloak/) | Principal | Keycloak via Pebble sidecar |
| [cephfs-share-k8s](k8s-charms/cephfs-share/) | Principal | CephFS proxy (external Ceph cluster config) |

## Prerequisites

- [Juju](https://juju.is/docs/juju/install-juju) 3.5+ with a LXD cloud
- [LXD](https://documentation.ubuntu.com/lxd/) initialized (`lxd init --auto`)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [just](https://just.systems/) (task runner)
- [charmcraft](https://snapcraft.io/charmcraft) (`snap install charmcraft --classic`)

## Quick Start

The fastest way to deploy the full stack:

```bash
git clone https://github.com/jamesbeedy/freeipa-charmed-operators
cd freeipa-charmed-operators

# Build all charms and deploy everything
just deploy
```

This single command will:
1. Pack all 4 machine charms
2. Create a Juju model with LXD nesting enabled (for Docker-in-LXD)
3. Create Juju secrets for FreeIPA and Keycloak admin passwords
4. Deploy freeipa-server, keycloak, cephfs-share, ubuntu (VM), freeipa-client, and filesystem-client
5. Wire up all integrations

You can customize the deployment with environment variables:

```bash
FREEIPA_DOMAIN=example.com \
FREEIPA_REALM=EXAMPLE.COM \
FREEIPA_PASSWORD=MySecurePass \
KEYCLOAK_PASSWORD=KeycloakPass \
JUJU_MODEL=my-model \
just deploy
```

### Post-deploy setup

FreeIPA server takes ~15 minutes for initial installation. Monitor with:

```bash
juju status --watch 5s
```

Once `freeipa-server` shows `active`, add DNS entries on the client and
keycloak machines so they can reach the FreeIPA server by hostname:

```bash
# Get the FreeIPA server IP
SERVER_IP=$(juju status freeipa-server/0 --format json | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['applications']['freeipa-server']['units']['freeipa-server/0']['public-address'])")

# Add /etc/hosts on all machines that need to reach it
for unit in ubuntu/0 keycloak/0; do
  juju ssh $unit -- "echo '$SERVER_IP freeipa-server.freeipa.local' | sudo tee -a /etc/hosts"
done
```

If the freeipa-client is blocked, trigger re-enrollment:

```bash
juju config freeipa-client force-join=true
```

### Expected final state

```
App                Status  Message
cephfs-share       active  cephfs ready
filesystem-client  active  Mounted filesystem at `/home`
freeipa-client     active  enrolled in FREEIPA.LOCAL
freeipa-server     active
keycloak           active
ubuntu             active
```

## Step-by-Step Deployment (Manual)

If you prefer to deploy each component manually instead of using `just deploy`,
follow these steps.

### Step 1: Bootstrap Juju and create a model

```bash
# Bootstrap Juju with a LXD cloud (skip if already bootstrapped)
juju bootstrap localhost

# Create a model for the deployment
juju add-model freeipa-dev

# Enable nesting for Docker-in-LXD support
PROFILE=$(lxc profile list -f csv | grep juju-freeipa | head -1 | cut -d, -f1)
lxc profile set "$PROFILE" security.nesting=true
```

### Step 2: Build the charms

```bash
# Build all machine charms
just build

# Or build specific charms
just build freeipa-server keycloak cephfs-share freeipa-client
```

This produces `.charm` files in the project root:
- `freeipa-server_amd64.charm`
- `freeipa-client_amd64.charm`
- `keycloak_amd64.charm`
- `cephfs-share_amd64.charm`

### Step 3: Create Juju secrets

All passwords are stored as Juju secrets -- never as plaintext config.

```bash
# Create the FreeIPA admin password secret
juju add-secret freeipa-admin password=ChangeMeNow123
# => secret:abc123...  (note this URI)

# Create the Keycloak admin password secret
juju add-secret keycloak-admin password=Keycloak2025!
# => secret:def456...  (note this URI)
```

Grant secrets to the charms that need them:

```bash
juju grant-secret freeipa-admin freeipa-server
juju grant-secret freeipa-admin freeipa-client
juju grant-secret freeipa-admin keycloak
juju grant-secret keycloak-admin keycloak
```

### Step 4: Deploy the FreeIPA server

```bash
juju deploy ./freeipa-server_amd64.charm \
  --config domain=freeipa.local \
  --config realm=FREEIPA.LOCAL \
  --config admin-password=secret:abc123...
```

Wait for it to become active (~15 minutes):

```bash
juju status --watch 5s
```

Check progress with:

```bash
juju ssh freeipa-server/0 -- "sudo docker logs freeipa-server 2>&1 | tail -20"
```

### Step 5: Deploy Keycloak

Keycloak provides OIDC/SAML identity brokering with LDAP user federation to FreeIPA.

```bash
juju deploy ./keycloak_amd64.charm \
  --config admin-password=secret:def456... \
  --config freeipa-server=freeipa-server.freeipa.local \
  --config freeipa-domain=freeipa.local \
  --config freeipa-admin-password=secret:abc123...
```

Integrate Keycloak with the FreeIPA server:

```bash
juju integrate keycloak:freeipa freeipa-server:freeipa
```

Add DNS resolution on the Keycloak machine:

```bash
SERVER_IP=$(juju status freeipa-server/0 --format json | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['applications']['freeipa-server']['units']['freeipa-server/0']['public-address'])")

juju ssh keycloak/0 -- "echo '$SERVER_IP freeipa-server.freeipa.local' | sudo tee -a /etc/hosts"
```

### Step 6: Deploy CephFS shared storage

```bash
juju deploy ./cephfs-share_amd64.charm
```

Wait for it to show `active: cephfs ready`:

```bash
juju status --watch 5s
```

### Step 7: Deploy Ubuntu VM with subordinates

Ubuntu units must be **VMs** (not LXD containers) because CephFS requires
kernel module access for mounting:

```bash
juju deploy ubuntu --base ubuntu@24.04 \
  --constraints virt-type=virtual-machine
```

Deploy and attach the **freeipa-client** subordinate:

```bash
juju deploy ./freeipa-client_amd64.charm \
  --config freeipa-server=freeipa-server.freeipa.local \
  --config domain=freeipa.local \
  --config realm=FREEIPA.LOCAL \
  --config admin-password=secret:abc123...

juju integrate freeipa-client:juju-info ubuntu:juju-info
```

Deploy and attach the **filesystem-client** subordinate:

```bash
juju deploy filesystem-client --channel latest/edge \
  --config mountpoint=/home

juju integrate filesystem-client:juju-info ubuntu:juju-info
juju integrate filesystem-client:filesystem cephfs-share:filesystem
```

Add DNS resolution on the Ubuntu machine:

```bash
juju ssh ubuntu/0 -- "echo '$SERVER_IP freeipa-server.freeipa.local' | sudo tee -a /etc/hosts"
```

If freeipa-client is blocked, trigger re-enrollment:

```bash
juju config freeipa-client force-join=true
```

### Step 8: Verify the deployment

```bash
# Check overall status
juju status

# Verify FreeIPA enrollment
juju ssh ubuntu/0 -- "id admin"
# uid=...(admin) gid=...(admins) groups=...(admins)

# Verify CephFS mount
juju ssh ubuntu/0 -- "df -h /home"
# Should show CephFS mounted at /home

# Verify Keycloak is running
KEYCLOAK_IP=$(juju status keycloak/0 --format json | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['applications']['keycloak']['units']['keycloak/0']['public-address'])")
echo "Keycloak admin console: http://$KEYCLOAK_IP:8080"
```

### Step 9: Scale out

Add more ubuntu units -- they automatically get both subordinates:

```bash
juju add-unit ubuntu
# The new unit will:
#  - Enroll with FreeIPA (freeipa-client subordinate)
#  - Mount CephFS at /home (filesystem-client subordinate)
```

## Using the Stack

### Creating users

Create a user in FreeIPA (via Keycloak). The user gets a `/bin/bash`
shell and is immediately available on all enrolled client machines.

```bash
juju run keycloak/0 create-user \
  username=jdoe \
  first-name=John \
  last-name=Doe \
  email=jdoe@example.com \
  password=SecurePass123
```

Verify on a client machine:

```bash
juju ssh ubuntu/0 -- id jdoe
# uid=66800003(jdoe) gid=66800003(jdoe) groups=66800003(jdoe)
```

### Adding SSH keys

Add an SSH public key to a FreeIPA user. The key is stored in FreeIPA's
LDAP (`ipasshpubkey` attribute) and automatically served to `sshd` on
all enrolled clients via SSSD's `sss_ssh_authorizedkeys`.

```bash
juju run keycloak/0 set-user-ssh-key \
  username=jdoe \
  ssh-key="ssh-ed25519 AAAA...xyz user@laptop"
```

### SSH into a client machine as the new user

After a brief SSSD cache refresh, SSH key auth works:

```bash
ssh -i ~/.ssh/id_ed25519 jdoe@<ubuntu-unit-ip>
# Logs in via SSH key, no password prompt
# Home directory is on CephFS, shared across all machines
```

**Note:** SSSD caches SSH keys. If the key doesn't work immediately,
flush the cache:

```bash
juju ssh ubuntu/0 -- "sudo rm -rf /var/lib/sss/db/* && sudo systemctl restart sssd"
```

### Rotating passwords

Juju secrets support rotation -- all charms pick up changes automatically:

```bash
# Rotate the FreeIPA admin password
juju secret-set freeipa-admin password=NewSecurePassword

# Rotate the Keycloak admin password
juju secret-set keycloak-admin password=NewKeycloakPass
```

## Kubernetes Deployment

The Kubernetes charms use Pebble sidecars instead of Docker. The deployment
flow is similar but targets a Kubernetes cloud.

### Step 1: Set up a Kubernetes model

```bash
juju add-model freeipa-k8s
```

### Step 2: Build K8s charms

```bash
just build k8s:freeipa-server k8s:keycloak k8s:cephfs-share
```

### Step 3: Create secrets

```bash
juju add-secret freeipa-admin password=ChangeMeNow123
juju add-secret keycloak-admin password=Keycloak2025!

juju grant-secret freeipa-admin freeipa-server-k8s
juju grant-secret freeipa-admin keycloak-k8s
juju grant-secret keycloak-admin keycloak-k8s
```

### Step 4: Deploy FreeIPA server

```bash
juju deploy ./freeipa-server-k8s_amd64.charm \
  --config domain=freeipa.local \
  --config realm=FREEIPA.LOCAL \
  --config admin-password=secret:abc123... \
  --resource freeipa-image=freeipa/freeipa-server:almalinux-9
```

### Step 5: Deploy Keycloak

```bash
juju deploy ./keycloak-k8s_amd64.charm \
  --config admin-password=secret:def456... \
  --config freeipa-admin-password=secret:abc123... \
  --resource keycloak-image=quay.io/keycloak/keycloak:latest

juju integrate keycloak-k8s:freeipa freeipa-server-k8s:freeipa
```

### Step 6: Deploy CephFS proxy (optional)

The K8s CephFS charm is a proxy for an external Ceph cluster:

```bash
juju deploy ./cephfs-share-k8s_amd64.charm \
  --config fsid=<ceph-cluster-fsid> \
  --config monitor-hosts="10.0.0.1:6789 10.0.0.2:6789" \
  --config client-key="<base64-cephx-key>"
```

See the [cephfs-share-k8s README](k8s-charms/cephfs-share/) for details on
gathering the required Ceph cluster info.

## Configuration Reference

### freeipa-server

| Option | Default | Description |
|--------|---------|-------------|
| `domain` | (required) | DNS domain (e.g. `freeipa.local`) |
| `realm` | (from domain) | Kerberos realm (e.g. `FREEIPA.LOCAL`) |
| `admin-password` | (required, secret) | Juju secret URI containing the admin password |
| `setup-dns` | `true` | Enable integrated DNS server |
| `dns-forwarders` | `8.8.8.8` | Comma-separated DNS forwarder IPs |
| `no-ntp` | `true` | Disable NTP (recommended for containers) |
| `image` | `freeipa/freeipa-server:almalinux-10` | OCI image |
| `container-name` | `freeipa-server` | Docker container name |
| `extra-install-opts` | | Extra `ipa-server-install` flags |

### freeipa-client

| Option | Default | Description |
|--------|---------|-------------|
| `freeipa-server` | (required) | FQDN or IP of the FreeIPA server |
| `domain` | (required) | FreeIPA domain |
| `realm` | (from domain) | Kerberos realm |
| `admin-password` | (required, secret) | Juju secret URI with IPA admin password |
| `mkhomedir` | `true` | Auto-create home dirs on login |
| `force-join` | `false` | Force re-enrollment |
| `no-ntp` | `false` | Disable NTP config |
| `extra-install-opts` | | Extra `ipa-client-install` flags |

### keycloak

| Option | Default | Description |
|--------|---------|-------------|
| `admin-password` | (required, secret) | Juju secret URI with Keycloak admin password |
| `admin-username` | `admin` | Keycloak admin username |
| `realm-name` | `freeipa` | Keycloak realm name |
| `freeipa-server` | | FreeIPA server FQDN (or via relation) |
| `freeipa-domain` | | FreeIPA domain (or via relation) |
| `freeipa-admin-password` | (required, secret) | Juju secret URI with FreeIPA admin password |
| `image` | `quay.io/keycloak/keycloak:latest` | OCI image |
| `container-name` | `keycloak` | Docker container name |
| `http-port` | `8080` | HTTP port |
| `https-port` | `8443` | HTTPS port |

### cephfs-share

| Option | Default | Description |
|--------|---------|-------------|
| `fs-name` | `cephfs` | CephFS filesystem name |
| `client-user` | `fs-client` | Ceph client username |
| `share-path` | `/` | Exported path within CephFS |
| `osd-size` | `4G` | Size of each loop-backed OSD |
| `osd-count` | `3` | Number of OSD devices |
| `microceph-channel` | `squid/stable` | MicroCeph snap channel |

## Integrations

| Provider | Requirer | Interface | Purpose |
|----------|----------|-----------|---------|
| freeipa-server:freeipa | keycloak:freeipa | freeipa | LDAP federation config |
| freeipa-server:ldap | (any):ldap | ldap | LDAP connection details |
| ubuntu:juju-info | freeipa-client:juju-info | juju-info | Subordinate attachment |
| ubuntu:juju-info | filesystem-client:juju-info | juju-info | Subordinate attachment |
| cephfs-share:filesystem | filesystem-client:filesystem | filesystem_info | CephFS mount details |

## Development

### Tooling

| Tool | Purpose |
|------|---------|
| [uv](https://docs.astral.sh/uv/) | Python dependency management |
| [just](https://just.systems/) | Task runner |
| [charmcraft](https://snapcraft.io/charmcraft) | Charm packaging |
| [ruff](https://docs.astral.sh/ruff/) | Linting and formatting |
| [repository.py](repository.py) | Build orchestration (stage, pack, lint) |

### Commands

```bash
# Build all charms (machine + k8s)
just build

# Build specific charms
just build freeipa-server keycloak

# Build only k8s charms
just build k8s:freeipa-server k8s:keycloak k8s:cephfs-share

# Format code
just fmt

# Lint
just lint

# Type-check
just typecheck

# Run unit tests
just unit

# Clean build artifacts
just clean

# Deploy full stack (build + deploy + integrate)
just deploy

# Deploy with custom config
FREEIPA_DOMAIN=example.com FREEIPA_PASSWORD=MyPass just deploy

# Tear down
just destroy
```

### Project structure

```
freeipa-charmed-operators/
├── charms/                     # Machine charms
│   ├── freeipa-server/         #   FreeIPA server (Docker)
│   ├── freeipa-client/         #   FreeIPA client subordinate
│   ├── keycloak/               #   Keycloak IdP (Docker)
│   │   └── templates/          #     realm.json template
│   └── cephfs-share/           #   MicroCeph + CephFS
├── k8s-charms/                 # Kubernetes charms (Pebble sidecar)
│   ├── freeipa-server/         #   FreeIPA server (Pebble)
│   ├── keycloak/               #   Keycloak (Pebble)
│   │   └── templates/          #     realm.json template
│   └── cephfs-share/           #   CephFS proxy (config-driven)
├── terraform/                  # OpenTofu/Terraform plan
│   ├── main.tf                 #   Full stack deployment
│   ├── variables.tf
│   └── terraform.tfvars.example
├── justfile                    # Task runner recipes
├── repository.py               # Build orchestration tool
├── pyproject.toml              # Root Python project config
└── README.md
```

### Terraform / OpenTofu

A Terraform plan is provided in `terraform/` for declarative deployment.
See [terraform/README.md](terraform/README.md) for details and current
compatibility notes.

```bash
just tofu-init
just tofu-plan -var-file=terraform.tfvars
just tofu-apply -var-file=terraform.tfvars
```

## Troubleshooting

### FreeIPA server stuck in maintenance

The initial install takes 10-15 minutes. Check progress:

```bash
juju ssh freeipa-server/0 -- "sudo docker logs freeipa-server 2>&1 | tail -20"
```

### Client can't reach FreeIPA server

The FreeIPA server hostname must resolve on client machines. Add it to
`/etc/hosts`:

```bash
juju ssh ubuntu/0 -- "echo '<server-ip> freeipa-server.freeipa.local' | sudo tee -a /etc/hosts"
```

### ipa-client-install failed

Check the detailed log:

```bash
juju ssh ubuntu/0 -- "sudo cat /var/log/ipaclient-install.log | tail -20"
```

Common fixes:
- Add `/etc/hosts` entry (see above)
- Uninstall stale state: `juju ssh ubuntu/0 -- "sudo ipa-client-install --uninstall -U"`
- Toggle re-enrollment: `juju config freeipa-client force-join=true`

### filesystem-client blocked: "Cannot mount filesystems on LXD containers"

The ubuntu units must be deployed as VMs, not LXD containers:

```bash
juju deploy ubuntu --base ubuntu@24.04 --constraints virt-type=virtual-machine
```

### SSSD not returning SSH keys

Flush the cache:

```bash
juju ssh ubuntu/0 -- "sudo rm -rf /var/lib/sss/db/* && sudo systemctl restart sssd"
```

### Keycloak shows "waiting for freeipa config"

Ensure the freeipa-server config or relation is set:

```bash
juju config keycloak freeipa-server=freeipa-server.freeipa.local
juju config keycloak freeipa-domain=freeipa.local
juju config keycloak freeipa-admin-password=secret:<freeipa-secret-id>
```

Or use the relation (config is published automatically):

```bash
juju integrate keycloak:freeipa freeipa-server:freeipa
```

### Charm shows "Secret not found"

The secret must be granted to the application before it can read it:

```bash
# List secrets
juju secrets

# Grant a secret to an app
juju grant-secret freeipa-admin freeipa-server

# Rotate a password (all charms pick up the change automatically)
juju secret-set freeipa-admin password=NewSecurePassword
```
