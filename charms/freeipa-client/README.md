# FreeIPA Client Charm

A [Juju](https://juju.is) subordinate charm that enrolls machine units with a
[FreeIPA](https://www.freeipa.org) server. It installs the FreeIPA client packages,
runs `ipa-client-install` to join the FreeIPA domain, and hardens SSH by disabling
password authentication in favor of SSH key auth via SSSD.

## How It Works

This is a **subordinate charm** -- it cannot be deployed standalone. It must be
related to a principal machine charm (e.g. `ubuntu`) via the `juju-info` interface.
When related, it runs on the same machine as the principal and:

1. Installs `freeipa-client` and `oddjob-mkhomedir` packages
2. Runs `ipa-client-install` with the configured server, domain, realm, and admin password
3. Hardens SSH by creating `/etc/ssh/sshd_config.d/99-freeipa-hardening.conf` to disable
   password auth (SSH key-only access via FreeIPA SSSD)
4. Enables automatic home directory creation on first login (via `mkhomedir`)

After enrollment, the machine resolves FreeIPA users via SSSD. SSH public keys stored
in FreeIPA LDAP are automatically served to `sshd` via `sss_ssh_authorizedkeys`.

## Prerequisites

- [Juju](https://juju.is/docs/juju/install-juju) 3.5+ with a LXD cloud
- A running FreeIPA server (see the [freeipa-server charm](../freeipa-server/))
- A principal machine charm to attach to (e.g. `ubuntu`)
- Network connectivity from the client machine to the FreeIPA server

## Build

From the project root:

```bash
just build freeipa-client
```

Or manually:

```bash
cd charms/freeipa-client
charmcraft pack
```

This produces `freeipa-client_amd64.charm`.

## Deploy

### Step 1: Ensure a FreeIPA server is running

The FreeIPA server must be deployed and active before enrolling clients.
See the [freeipa-server charm](../freeipa-server/) for deployment instructions.

### Step 2: Deploy a principal machine charm

```bash
juju deploy ubuntu --base ubuntu@24.04 \
  --constraints virt-type=virtual-machine
```

> **Note:** Use `virt-type=virtual-machine` if you also plan to mount CephFS,
> which requires kernel module access not available in LXD containers.

### Step 3: Create and grant the admin password secret

```bash
# Use the same secret as the FreeIPA server, or create a new one
juju add-secret freeipa-admin password=ChangeMeNow123
# => secret:abc123...

juju grant-secret freeipa-admin freeipa-client
```

### Step 4: Deploy the client charm

```bash
juju deploy ./freeipa-client_amd64.charm \
  --config freeipa-server=freeipa-server.freeipa.local \
  --config domain=freeipa.local \
  --config realm=FREEIPA.LOCAL \
  --config admin-password=secret:abc123...
```

### Step 5: Relate to the principal charm

```bash
juju integrate freeipa-client:juju-info ubuntu:juju-info
```

### Step 6: Ensure DNS resolution

The client machine must be able to resolve the FreeIPA server hostname. Add an
`/etc/hosts` entry if DNS is not configured:

```bash
juju ssh ubuntu/0 -- "echo '<freeipa-server-ip> freeipa-server.freeipa.local' | sudo tee -a /etc/hosts"
```

### Step 7: Verify enrollment

```bash
juju status --watch 5s
# Wait for freeipa-client to show "active: enrolled in FREEIPA.LOCAL"

# Verify on the machine
juju ssh ubuntu/0 -- "id admin"
# uid=...(admin) gid=...(admins) groups=...(admins)
```

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `freeipa-server` | string | *(required)* | FQDN or IP of the FreeIPA server |
| `domain` | string | *(required)* | FreeIPA domain (e.g. `freeipa.local`) |
| `realm` | string | *(from domain)* | Kerberos realm (e.g. `FREEIPA.LOCAL`). Derived from domain if not set |
| `admin-password` | secret | *(required)* | Juju secret URI with the IPA admin password for enrollment |
| `mkhomedir` | boolean | `true` | Automatically create home directories on first login |
| `no-ntp` | boolean | `false` | Disable NTP configuration during enrollment |
| `force-join` | boolean | `false` | Force re-enrollment even if already enrolled |
| `extra-install-opts` | string | | Additional `ipa-client-install` flags (space-separated) |

### Re-enrolling a machine

If enrollment fails or you need to re-enroll:

```bash
juju config freeipa-client force-join=true
```

## Relations

### Requires

| Relation | Interface | Scope | Description |
|----------|-----------|-------|-------------|
| `juju-info` | `juju-info` | container | Subordinate attachment to a principal machine charm |

### Example integrations

```bash
# Attach to ubuntu charm
juju integrate freeipa-client:juju-info ubuntu:juju-info

# Attach to any machine charm
juju integrate freeipa-client:juju-info <my-app>:juju-info
```

## SSH Key Authentication

After enrollment, the charm configures SSH for key-only authentication:

- Password authentication is disabled
- SSH keys are managed in FreeIPA LDAP (`ipasshpubkey` attribute)
- SSSD serves keys to `sshd` via `sss_ssh_authorizedkeys`

To add an SSH key for a user, use the Keycloak charm's `set-user-ssh-key` action:

```bash
juju run keycloak/0 set-user-ssh-key \
  username=jdoe \
  ssh-key="ssh-ed25519 AAAA...xyz user@laptop"
```

Then SSH in:

```bash
ssh -i ~/.ssh/id_ed25519 jdoe@<ubuntu-unit-ip>
```

## Scaling

When you add more units to the principal charm, each new unit automatically gets
the freeipa-client subordinate and enrolls with FreeIPA:

```bash
juju add-unit ubuntu
# The new unit automatically enrolls with FreeIPA
```

## Troubleshooting

### Client shows "blocked"

Check that the FreeIPA server hostname resolves from the client machine:

```bash
juju ssh ubuntu/0 -- "getent hosts freeipa-server.freeipa.local"
```

If it doesn't resolve, add an `/etc/hosts` entry and trigger re-enrollment:

```bash
juju ssh ubuntu/0 -- "echo '<ip> freeipa-server.freeipa.local' | sudo tee -a /etc/hosts"
juju config freeipa-client force-join=true
```

### ipa-client-install failed

Check the detailed log:

```bash
juju ssh ubuntu/0 -- "sudo cat /var/log/ipaclient-install.log | tail -30"
```

### Stale enrollment state

If a previous enrollment attempt left stale state:

```bash
juju ssh ubuntu/0 -- "sudo ipa-client-install --uninstall -U"
juju config freeipa-client force-join=true
```

### SSSD not returning users

Flush the SSSD cache:

```bash
juju ssh ubuntu/0 -- "sudo rm -rf /var/lib/sss/db/* && sudo systemctl restart sssd"
```
