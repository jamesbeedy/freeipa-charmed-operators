#!/usr/bin/env python3
"""FreeIPA Kubernetes charm."""

import logging

import ops

logger = logging.getLogger(__name__)

FREEIPA_CONTAINER = "freeipa"


class FreeIPACharm(ops.CharmBase):
    """Charm the FreeIPA identity management server."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.framework.observe(self.on.freeipa_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.ldap_relation_joined, self._on_ldap_relation_joined)

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle install event."""
        self.unit.status = ops.MaintenanceStatus("installing")

    def _on_start(self, event: ops.StartEvent) -> None:
        """Handle start event."""
        self._configure_freeipa()

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent) -> None:
        """Handle pebble-ready event for the FreeIPA container."""
        self._configure_freeipa()

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle changes in charm configuration."""
        self._configure_freeipa()

    def _on_ldap_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Provide LDAP connection details to related applications."""
        if not self._is_installed():
            event.defer()
            return
        self._publish_ldap_data(event.relation)

    # -------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------

    def _configure_freeipa(self) -> None:
        """Configure and start FreeIPA in the workload container."""
        container = self.unit.get_container(FREEIPA_CONTAINER)
        if not container.can_connect():
            self.unit.status = ops.WaitingStatus("waiting for container")
            return

        admin_password = self.config.get("admin-password")
        if not admin_password:
            self.unit.status = ops.BlockedStatus("admin-password config is required")
            return

        install_cmd = self._build_install_command()
        env = self._build_environment()

        pebble_layer = {
            "summary": "FreeIPA server layer",
            "description": "Pebble layer for FreeIPA identity management server",
            "services": {
                "freeipa": {
                    "override": "replace",
                    "summary": "FreeIPA server",
                    "command": " ".join(["/usr/sbin/init"]),
                    "startup": "enabled",
                    "environment": env,
                },
            },
        }

        container.add_layer("freeipa", pebble_layer, combine=True)
        container.replan()

        if self._is_installed():
            self.unit.status = ops.ActiveStatus()
            self._update_ldap_relations()
        else:
            self.unit.status = ops.MaintenanceStatus(
                "FreeIPA server installing (this may take 15+ minutes)"
            )

    def _build_environment(self) -> dict[str, str]:
        """Build environment variables for the FreeIPA container."""
        env: dict[str, str] = {}

        admin_password = self.config.get("admin-password")
        if admin_password:
            env["PASSWORD"] = admin_password

        hostname = self._get_hostname()
        if hostname:
            env["IPA_SERVER_HOSTNAME"] = hostname

        install_opts = self._build_install_opts_string()
        if install_opts:
            env["IPA_SERVER_INSTALL_OPTS"] = install_opts

        return env

    def _build_install_command(self) -> list[str]:
        """Build the ipa-server-install command arguments."""
        args = ["ipa-server-install", "-U"]

        realm = self.config.get("realm")
        if realm:
            args.extend(["-r", realm])

        domain = self.config.get("domain")
        if domain:
            args.extend(["-n", domain])

        admin_password = self.config.get("admin-password")
        if admin_password:
            args.extend(["-a", admin_password, "-p", admin_password])

        if self.config.get("setup-dns"):
            args.append("--setup-dns")
            forwarders = self.config.get("dns-forwarders", "")
            if forwarders.strip():
                for fwd in forwarders.split(","):
                    fwd = fwd.strip()
                    if fwd:
                        args.extend(["--forwarder", fwd])
            else:
                args.append("--no-forwarders")

        if self.config.get("no-ntp"):
            args.append("--no-ntp")

        extra = self.config.get("extra-install-opts", "")
        if extra.strip():
            args.extend(extra.strip().split())

        return args

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

    def _is_installed(self) -> bool:
        """Check whether FreeIPA has completed initial installation."""
        container = self.unit.get_container(FREEIPA_CONTAINER)
        if not container.can_connect():
            return False
        try:
            # FreeIPA writes a sysrestore state file after successful install
            container.list_files("/data/etc/ipa/default.conf")
            return True
        except (ops.pebble.PathError, ops.pebble.APIError):
            return False

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
        base_dn = ",".join(f"dc={part}" for part in domain.split(".")) if domain else ""

        relation.data[self.app].update(
            {
                "url": f"ldap://{self._get_hostname()}",
                "base-dn": base_dn,
                "port": "389",
                "tls-port": "636",
            }
        )


if __name__ == "__main__":
    ops.main(FreeIPACharm)
