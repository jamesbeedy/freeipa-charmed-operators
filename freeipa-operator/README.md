# freeipa-operator

A Juju charm that deploys [FreeIPA](https://www.freeipa.org/) on Kubernetes.

FreeIPA provides centralized identity management including LDAP directory,
Kerberos authentication, DNS, and certificate authority services.

## Usage

```bash
juju deploy freeipa --config admin-password=MySecurePassword123
```

### Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `admin-password` | (required) | Password for IPA admin and Directory Manager |
| `realm` | (auto) | Kerberos realm (e.g. `EXAMPLE.COM`) |
| `domain` | (auto) | DNS domain (e.g. `example.com`) |
| `setup-dns` | `true` | Enable integrated DNS server |
| `dns-forwarders` | `8.8.8.8` | Comma-separated DNS forwarder IPs |
| `no-ntp` | `true` | Disable NTP (host manages time) |
| `extra-install-opts` | | Additional `ipa-server-install` arguments |

### Example with full config

```bash
juju deploy freeipa \
  --config admin-password=MySecurePassword123 \
  --config realm=EXAMPLE.COM \
  --config domain=example.com \
  --config dns-forwarders="8.8.8.8,8.8.4.4"
```

### Relations

- **`ldap`** (provides) - Exposes LDAP connection details to related applications.

## Exposed Ports

| Port | Service |
|------|---------|
| 443 | HTTPS (Web UI + API) |
| 389/636 | LDAP / LDAPS |
| 88 | Kerberos |
| 464 | Kerberos password change |
| 53 | DNS (if enabled) |

## Notes

- Initial installation takes **15+ minutes**. The charm will show
  `MaintenanceStatus` until the install completes.
- All FreeIPA data persists in the `/data` volume. Loss of this storage
  requires full reinstallation.
- The container uses `freeipa/freeipa-server:almalinux-10` by default.

## Development

```bash
charmcraft pack
juju deploy ./freeipa_*.charm --resource freeipa-image=freeipa/freeipa-server:almalinux-10
```
