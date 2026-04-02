#!/usr/bin/env python3
"""FreeIPA server machine charm using Docker."""

import logging
import subprocess
from pathlib import Path

import ops

logger = logging.getLogger(__name__)

FREEIPA_DATA_DIR = Path("/srv/freeipa-data")
FREEIPA_PATCHES_DIR = Path("/srv/freeipa-patches")
FREEIPA_INSTALL_MARKER = FREEIPA_DATA_DIR / "etc" / "ipa" / "default.conf"

PUBLISHED_PORTS = [
    ("80", "tcp"),
    ("443", "tcp"),
    ("389", "tcp"),
    ("636", "tcp"),
    ("88", "tcp"),
    ("88", "udp"),
    ("464", "tcp"),
    ("464", "udp"),
    ("53", "tcp"),
    ("53", "udp"),
]


class FreeIPAServerCharm(ops.CharmBase):
    """Machine charm that deploys FreeIPA server via Docker."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.stop, self._on_stop)
        framework.observe(self.on.update_status, self._on_update_status)
        framework.observe(self.on.secret_changed, self._on_secret_changed)
        framework.observe(self.on.ldap_relation_joined, self._on_ldap_relation_joined)
        framework.observe(self.on.freeipa_relation_joined, self._on_freeipa_relation_joined)

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_secret_changed(self, event: ops.SecretChangedEvent) -> None:
        """Handle secret rotation — refresh cached content."""
        event.secret.get_content(refresh=True)

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Install Docker, pull the FreeIPA image, and prepare the host."""
        self.unit.status = ops.MaintenanceStatus("installing docker")
        try:
            self._install_docker()
        except subprocess.CalledProcessError as e:
            logger.error("Failed to install Docker: %s", e)
            self.unit.status = ops.BlockedStatus("failed to install docker")
            return

        image = self.config.get("image", "docker.io/freeipa/freeipa-server:almalinux-10")
        self.unit.status = ops.MaintenanceStatus(f"pulling {image}")
        try:
            subprocess.check_call(["docker", "pull", image])
        except subprocess.CalledProcessError as e:
            logger.error("Failed to pull image: %s", e)
            self.unit.status = ops.BlockedStatus("failed to pull freeipa image")
            return

        FREEIPA_DATA_DIR.mkdir(parents=True, exist_ok=True)
        FREEIPA_PATCHES_DIR.mkdir(parents=True, exist_ok=True)

        self._prepare_host()
        self._patch_init_script(image)
        self._open_ports()

        self.unit.status = ops.WaitingStatus("waiting for admin-password config")

    def _on_start(self, event: ops.StartEvent) -> None:
        """Start the FreeIPA container."""
        self._configure_freeipa()

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config changes."""
        self._configure_freeipa()

    def _on_stop(self, event: ops.StopEvent) -> None:
        """Stop and remove the FreeIPA container."""
        container_name = self.config.get("container-name", "freeipa-server")
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
        )

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Periodic check for install completion."""
        container_name = self.config.get("container-name", "freeipa-server")
        if not self._is_container_running(container_name):
            return
        if self._is_installed():
            self.unit.status = ops.ActiveStatus()
            self._update_ldap_relations()
            self._update_freeipa_relations()

    def _on_ldap_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Provide LDAP connection details to related applications."""
        if not self._is_installed():
            event.defer()
            return
        self._publish_ldap_data(event.relation)

    def _on_freeipa_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Provide FreeIPA enrollment details to client charms."""
        if not self._is_installed():
            event.defer()
            return
        self._publish_freeipa_data(event.relation)

    # -------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------

    def _get_secret_value(self, config_key: str, secret_key: str) -> str | None:
        """Resolve a secret-type config option to its plaintext value."""
        secret_id = self.config.get(config_key)
        if not secret_id:
            return None
        try:
            secret = self.model.get_secret(id=secret_id)
            return secret.get_content().get(secret_key)
        except ops.SecretNotFoundError:
            logger.error(
                "Secret %s not found — did you run 'juju grant-secret'?",
                secret_id,
            )
            return None

    def _open_ports(self) -> None:
        """Open FreeIPA service ports via Juju."""
        for port, proto in PUBLISHED_PORTS:
            self.unit.open_port(proto, int(port))

    def _install_docker(self) -> None:
        """Install Docker CE from the system packages."""
        subprocess.check_call(["apt-get", "update"])
        subprocess.check_call(["apt-get", "install", "-y", "docker.io"])
        subprocess.check_call(["systemctl", "enable", "--now", "docker"])

    def _prepare_host(self) -> None:
        """Prepare the host for running the FreeIPA container."""
        # Stop systemd-resolved to free port 53 for FreeIPA's DNS
        subprocess.run(
            ["systemctl", "stop", "systemd-resolved"],
            capture_output=True,
        )
        subprocess.run(
            ["systemctl", "disable", "systemd-resolved"],
            capture_output=True,
        )
        # Ensure /etc/resolv.conf points to a real upstream DNS
        resolv = Path("/etc/resolv.conf")
        if resolv.is_symlink():
            resolv.unlink()
        resolv.write_text("nameserver 8.8.8.8\nnameserver 8.8.4.4\n")

    def _patch_init_script(self, image: str) -> None:
        """Extract and patch the FreeIPA init script for systemd 257+ compat."""
        patched_init = FREEIPA_PATCHES_DIR / "init"
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--entrypoint", "cat",
                image, "/usr/local/sbin/init",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning("Could not extract init script, skipping patch")
            return
        # Remove --show-status=false which is unsupported in systemd 257+
        patched = result.stdout.replace("--show-status=false ", "")
        patched_init.write_text(patched)
        patched_init.chmod(0o755)

    def _configure_freeipa(self) -> None:
        """Ensure the FreeIPA container is running with current config."""
        admin_password = self._get_secret_value("admin-password", "password")
        if not admin_password:
            self.unit.status = ops.BlockedStatus(
                "admin-password secret required — see: juju add-secret"
            )
            return

        container_name = self.config.get("container-name", "freeipa-server")

        if self._is_container_running(container_name):
            if self._is_installed():
                self.unit.status = ops.ActiveStatus()
                self._update_ldap_relations()
                self._update_freeipa_relations()
            else:
                self.unit.status = ops.MaintenanceStatus(
                    "FreeIPA server installing (this may take 15+ minutes)"
                )
            return

        # Remove any stopped container with the same name
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
        )

        hostname = self._get_hostname()
        if not hostname:
            self.unit.status = ops.BlockedStatus("domain config is required")
            return

        image = self.config.get("image", "docker.io/freeipa/freeipa-server:almalinux-10")
        install_opts = self._build_install_opts_string()

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--hostname", hostname,
            "--privileged",
            "--cgroupns=host",
            "-v", f"{FREEIPA_DATA_DIR}:/data:Z",
            "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            "-e", f"PASSWORD={admin_password}",
            "-e", f"IPA_SERVER_HOSTNAME={hostname}",
            "-e", f"IPA_SERVER_INSTALL_OPTS={install_opts}",
        ]

        # Bind-mount patched init script if available
        patched_init = FREEIPA_PATCHES_DIR / "init"
        if patched_init.exists():
            cmd.extend(["-v", f"{patched_init}:/usr/local/sbin/init:Z"])

        # Publish ports
        for port, proto in PUBLISHED_PORTS:
            cmd.extend(["-p", f"{port}:{port}/{proto}"])

        cmd.append(image)

        self.unit.status = ops.MaintenanceStatus("starting FreeIPA container")
        logger.info("Running: %s", " ".join(cmd))

        try:
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as e:
            logger.error("Failed to start FreeIPA container: %s", e)
            self.unit.status = ops.BlockedStatus(
                "failed to start freeipa container"
            )
            return

        self.unit.status = ops.MaintenanceStatus(
            "FreeIPA server installing (this may take 15+ minutes)"
        )

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

    def _is_container_running(self, container_name: str) -> bool:
        """Check if the Docker container is running."""
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _is_installed(self) -> bool:
        """Check whether FreeIPA has completed initial installation."""
        return FREEIPA_INSTALL_MARKER.exists()

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
        """Set FreeIPA enrollment data on a relation for client charms."""
        domain = self.config.get("domain", "")
        realm = self.config.get("realm", "") or domain.upper()

        data = {
            "hostname": self._get_hostname(),
            "domain": domain,
            "realm": realm,
        }

        # Grant the admin-password secret to the related app
        secret_id = self.config.get("admin-password")
        if secret_id:
            try:
                secret = self.model.get_secret(id=secret_id)
                secret.grant(relation)
                data["admin-password-secret-id"] = secret_id
            except ops.SecretNotFoundError:
                pass

        relation.data[self.app].update(data)


if __name__ == "__main__":
    ops.main(FreeIPAServerCharm)
