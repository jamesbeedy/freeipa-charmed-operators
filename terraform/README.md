# Terraform / OpenTofu Plan

This directory contains an OpenTofu (Terraform) plan that deploys the full
FreeIPA charmed operators stack using the
[Juju Terraform provider](https://github.com/juju/terraform-provider-juju).

## Prerequisites

- **Juju 3.x controller** — the Juju Terraform provider v1.x does not yet
  support Juju 4.x (support is in development on the `feature/juju-4.0` branch).
- **Charms published to Charmhub** — the provider does not support deploying
  local `.charm` files. All custom charms must be published first.
- [OpenTofu](https://opentofu.org/) or [Terraform](https://www.terraform.io/)

## Current Status

This plan is **structurally complete** and validates successfully. It will be
fully functional once:

1. The custom charms (`freeipa-server`, `freeipa-client`, `keycloak`,
   `cephfs-share`) are published to Charmhub.
2. The Juju Terraform provider releases Juju 4.x support.

## Usage (once prerequisites are met)

```bash
# Copy example variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# Initialize
tofu init

# Plan
tofu plan -var-file=terraform.tfvars

# Apply
tofu apply -var-file=terraform.tfvars

# Destroy
tofu destroy -var-file=terraform.tfvars
```

## For local development

Use `just deploy` from the project root instead — it builds local `.charm`
files and deploys them directly via the Juju CLI.
