#!/usr/bin/env python3
"""FreeIPA server K8s charm using Pebble sidecar."""

import logging

import ops

logger = logging.getLogger(__name__)

CONTAINER_NAME = "freeipa"
SERVICE_NAME = "freeipa"
INSTALL_MARKER = "/data/etc/ipa/default.conf"


class FreeIPAServerK8sCharm(ops.CharmBase):
    """K8s sidecar charm for the FreeIPA identity management server."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(
            self.on["freeipa"].pebble_ready, self._on_pebble_ready
        )
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.secret_changed, self._on_secret_changed)
        framework.observe(self.on.update_status, self._on_update_status)
        framework.observe(
            self.on.ldap_relation_joined, self._on_ldap_relation_joined
        )
        framework.observe(
            self.on.freeipa_relation_joined, self._on_freeipa_relation_joined
        )

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_secret_changed(self, event: ops.SecretChangedEvent) -> None:
        """Handle secret rotation."""
        event.secret.get_content(refresh=True)

    def _get_secret_value(self, config_key: str, secret_key: str) -> str | None:
        """Resolve a secret-type config option to its plaintext value."""
        secret_id = self.config.get(config_key)
        if not secret_id:
            return None
        try:
            secret = self.model.get_secret(id=secret_id)
            return secret.get_content().get(secret_key)
        except ops.SecretNotFoundError:
            logger.error("Secret %s not found", secret_id)
            return None

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent) -> None:
        """Configure the FreeIPA workload when Pebble is ready."""
        self._configure_workload(event.workload)

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config changes."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            return
        self._configure_workload(container)

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Check install progress periodically."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            self.unit.status = ops.WaitingStatus("waiting for Pebble")
            return
        if self._is_installed(container):
            self.unit.status = ops.ActiveStatus()
            self._update_ldap_relations()
            self._update_freeipa_relations()

    def _on_ldap_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Provide LDAP connection details."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect() or not self._is_installed(container):
            event.defer()
            return
        self._publish_ldap_data(event.relation)

    def _on_freeipa_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Provide FreeIPA enrollment details."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect() or not self._is_installed(container):
            event.defer()
            return
        self._publish_freeipa_data(event.relation)

    # -------------------------------------------------------------------
    # Workload configuration
    # -------------------------------------------------------------------

    def _configure_workload(self, container: ops.Container) -> None:
        """Configure and start the FreeIPA server via Pebble."""
        admin_password = self._get_secret_value("admin-password", "password")
        if not admin_password:
            self.unit.status = ops.BlockedStatus(
                "admin-password secret required — see: juju add-secret"
            )
            return

        hostname = self._get_hostname()
        if not hostname:
            self.unit.status = ops.BlockedStatus("domain config is required")
            return

        install_opts = self._build_install_opts_string()

        # Patch init script for systemd 257+ compatibility
        self._patch_init_script(container)

        container.add_layer(
            CONTAINER_NAME,
            self._pebble_layer(admin_password, hostname, install_opts),
            combine=True,
        )
        container.replan()

        if self._is_installed(container):
            self.unit.status = ops.ActiveStatus()
            self._update_ldap_relations()
            self._update_freeipa_relations()
        else:
            self.unit.status = ops.MaintenanceStatus(
                "FreeIPA server installing (this may take 15+ minutes)"
            )

    def _pebble_layer(
        self, admin_password: str, hostname: str, install_opts: str
    ) -> ops.pebble.LayerDict:
        """Build the Pebble layer for FreeIPA."""
        return {
            "summary": "FreeIPA server layer",
            "description": "Pebble layer for FreeIPA identity management server",
            "services": {
                SERVICE_NAME: {
                    "override": "replace",
                    "summary": "FreeIPA server",
                    "command": "/usr/local/sbin/init",
                    "startup": "enabled",
                    "environment": {
                        "PASSWORD": admin_password,
                        "IPA_SERVER_HOSTNAME": hostname,
                        "IPA_SERVER_INSTALL_OPTS": install_opts,
                    },
                },
            },
        }

    def _patch_init_script(self, container: ops.Container) -> None:
        """Patch the init script in-place for systemd 257+ compat."""
        try:
            content = container.pull("/usr/local/sbin/init").read()
            if "--show-status=false" in content:
                patched = content.replace("--show-status=false ", "")
                container.push("/usr/local/sbin/init", patched, permissions=0o755)
                logger.info("Patched init script for systemd 257+ compatibility")
        except (ops.pebble.PathError, ops.pebble.ProtocolError):
            logger.debug("Could not patch init script, skipping")

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _build_install_opts_string(self) -> str:
        """Build the IPA_SERVER_INSTALL_OPTS environment variable value."""
        opts = ["-U"]

        realm = self.config.get("realm")
        if realm:
            opts.extend(["-r", realm])

        domain = self.config.get("domain")
        if domain:
            opts.extend(["-n", domain])

        if self.config.get("setup-dns"):
            opts.append("--setup-dns")
            forwarders = self.config.get("dns-forwarders", "")
            if forwarders.strip():
                for fwd in forwarders.split(","):
                    fwd = fwd.strip()
                    if fwd:
                        opts.extend(["--forwarder", fwd])
            else:
                opts.append("--no-forwarders")

        if self.config.get("no-ntp"):
            opts.append("--no-ntp")

        extra = self.config.get("extra-install-opts", "")
        if extra.strip():
            opts.extend(extra.strip().split())

        return " ".join(opts)

    def _get_hostname(self) -> str:
        """Derive the FQDN hostname for the FreeIPA server."""
        domain = self.config.get("domain")
        if domain:
            return f"{self.app.name}.{domain}"
        return ""

    def _is_installed(self, container: ops.Container) -> bool:
        """Check whether FreeIPA has completed initial installation."""
        return container.exists(INSTALL_MARKER)

    # -------------------------------------------------------------------
    # Relation helpers
    # -------------------------------------------------------------------

    def _update_ldap_relations(self) -> None:
        """Push LDAP connection info to all related applications."""
        for relation in self.model.relations.get("ldap", []):
            self._publish_ldap_data(relation)

    def _publish_ldap_data(self, relation: ops.Relation) -> None:
        """Set LDAP connection data on a relation."""
        domain = self.config.get("domain", "")
        base_dn = (
            ",".join(f"dc={part}" for part in domain.split(".")) if domain else ""
        )
        relation.data[self.app].update(
            {
                "url": f"ldap://{self._get_hostname()}",
                "base-dn": base_dn,
                "port": "389",
                "tls-port": "636",
            }
        )

    def _update_freeipa_relations(self) -> None:
        """Push FreeIPA enrollment info to all related client charms."""
        for relation in self.model.relations.get("freeipa", []):
            self._publish_freeipa_data(relation)

    def _publish_freeipa_data(self, relation: ops.Relation) -> None:
        """Set FreeIPA enrollment data on a relation."""
        domain = self.config.get("domain", "")
        realm = self.config.get("realm", "") or domain.upper()
        relation.data[self.app].update(
            {
                "hostname": self._get_hostname(),
                "domain": domain,
                "realm": realm,
            }
        )


if __name__ == "__main__":
    ops.main(FreeIPAServerK8sCharm)
