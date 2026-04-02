# FreeIPA Charmed Operators - Task Runner
# Usage: just <recipe>

repo := "python3 repository.py"

default:
    @just --list

# Format all code
fmt *charms:
    {{repo}} fmt {{charms}}

# Lint all code
lint *charms:
    {{repo}} lint {{charms}}

# Type-check all charm source
typecheck *charms:
    {{repo}} typecheck {{charms}}

# Run unit tests
unit *args:
    {{repo}} unit {{args}}

# Stage charms into _build/
stage *charms:
    {{repo}} stage {{charms}}

# Build (stage + pack) all charms
build *charms:
    {{repo}} build {{charms}}

# Clean build artifacts and charmcraft environments
clean:
    {{repo}} clean

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

# Default deployment config (override via env or just args)
freeipa_domain := env("FREEIPA_DOMAIN", "freeipa.local")
freeipa_realm  := env("FREEIPA_REALM", "FREEIPA.LOCAL")
freeipa_pw     := env("FREEIPA_PASSWORD", "FreeIPA2025!")
keycloak_pw    := env("KEYCLOAK_PASSWORD", "Keycloak2025!")
model          := env("JUJU_MODEL", "freeipa-dev")

# Build all charms, create a model, and deploy the full stack
deploy: build _deploy-model _deploy-secrets _deploy-apps _deploy-integrations
    @echo ""
    @echo "Deployment complete. Run 'juju status' to monitor progress."
    @echo "FreeIPA server install takes ~15 minutes."
    @echo ""
    @echo "Once freeipa-server is active, set /etc/hosts on keycloak & client machines:"
    @echo "  juju ssh keycloak/0 -- \"echo '<server-ip> freeipa-server.{{freeipa_domain}}' | sudo tee -a /etc/hosts\""

# Create the Juju model with nesting enabled for Docker-in-LXD
_deploy-model:
    #!/usr/bin/env bash
    set -euo pipefail
    if juju models 2>/dev/null | grep -q "{{model}}"; then
        echo "Model {{model}} already exists, switching to it."
        juju switch {{model}}
    else
        echo "Creating model {{model}}..."
        juju add-model {{model}}
    fi
    # Enable nesting on the LXD profile for Docker support
    PROFILE=$(lxc profile list -f csv | grep "juju-$(echo {{model}} | tr -d '-')\|juju-{{model}}" | head -1 | cut -d, -f1)
    if [ -z "$PROFILE" ]; then
        PROFILE=$(lxc profile list -f csv | grep "juju-" | tail -1 | cut -d, -f1)
    fi
    if [ -n "$PROFILE" ]; then
        echo "Enabling security.nesting on LXD profile: $PROFILE"
        lxc profile set "$PROFILE" security.nesting=true
    else
        echo "Warning: could not find LXD profile, nesting may not be enabled."
    fi

# Create Juju secrets for sensitive config
_deploy-secrets:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Creating Juju secrets..."

    FREEIPA_SECRET=$(juju add-secret freeipa-admin password={{freeipa_pw}})
    echo "  FreeIPA admin secret: $FREEIPA_SECRET"

    KEYCLOAK_SECRET=$(juju add-secret keycloak-admin password={{keycloak_pw}})
    echo "  Keycloak admin secret: $KEYCLOAK_SECRET"

    # Grant secrets to the applications that need them
    juju grant-secret freeipa-admin freeipa-server
    juju grant-secret freeipa-admin freeipa-client
    juju grant-secret freeipa-admin keycloak
    juju grant-secret keycloak-admin keycloak

    # Store secret URIs for _deploy-apps to use
    echo "$FREEIPA_SECRET" > /tmp/.freeipa-secret-id
    echo "$KEYCLOAK_SECRET" > /tmp/.keycloak-secret-id

# Deploy all charm applications
_deploy-apps:
    #!/usr/bin/env bash
    set -euo pipefail
    FREEIPA_SECRET=$(cat /tmp/.freeipa-secret-id)
    KEYCLOAK_SECRET=$(cat /tmp/.keycloak-secret-id)

    echo "Deploying freeipa-server..."
    juju deploy ./freeipa-server_amd64.charm \
        --config domain={{freeipa_domain}} \
        --config realm={{freeipa_realm}} \
        --config admin-password="$FREEIPA_SECRET"

    echo "Deploying ubuntu (VM for CephFS kernel mount support)..."
    juju deploy ubuntu --base ubuntu@24.04 --constraints virt-type=virtual-machine

    echo "Deploying freeipa-client..."
    juju deploy ./freeipa-client_amd64.charm \
        --config freeipa-server=freeipa-server.{{freeipa_domain}} \
        --config domain={{freeipa_domain}} \
        --config realm={{freeipa_realm}} \
        --config admin-password="$FREEIPA_SECRET"

    echo "Deploying keycloak..."
    juju deploy ./keycloak_amd64.charm \
        --config admin-password="$KEYCLOAK_SECRET" \
        --config freeipa-server=freeipa-server.{{freeipa_domain}} \
        --config freeipa-domain={{freeipa_domain}} \
        --config freeipa-admin-password="$FREEIPA_SECRET"

    echo "Deploying cephfs-share..."
    juju deploy ./cephfs-share_amd64.charm

    echo "Deploying filesystem-client..."
    juju deploy filesystem-client --channel latest/edge \
        --config mountpoint=/home

    # Clean up temp files
    rm -f /tmp/.freeipa-secret-id /tmp/.keycloak-secret-id

# Wire up all integrations
_deploy-integrations:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Integrating freeipa-client -> ubuntu (subordinate)..."
    juju integrate freeipa-client:juju-info ubuntu:juju-info

    echo "Integrating keycloak -> freeipa-server..."
    juju integrate keycloak:freeipa freeipa-server:freeipa

    echo "Integrating filesystem-client -> ubuntu (subordinate)..."
    juju integrate filesystem-client:juju-info ubuntu:juju-info

    echo "Integrating filesystem-client -> cephfs-share..."
    juju integrate filesystem-client:filesystem cephfs-share:filesystem

# Tear down the deployment model
destroy:
    juju destroy-model {{model}} --no-prompt --destroy-storage --force --no-wait

# ---------------------------------------------------------------------------
# OpenTofu / Terraform
# ---------------------------------------------------------------------------

# Initialize OpenTofu in the terraform/ directory
tofu-init:
    cd terraform && tofu init

# Plan the deployment with OpenTofu
tofu-plan *args:
    cd terraform && tofu plan {{args}}

# Apply the deployment with OpenTofu
tofu-apply *args:
    cd terraform && tofu apply {{args}}

# Destroy the deployment with OpenTofu
tofu-destroy *args:
    cd terraform && tofu destroy {{args}}
