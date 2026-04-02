#!/usr/bin/env python3
"""FreeIPA client subordinate charm."""

import logging
import subprocess
from pathlib import Path

import ops

logger = logging.getLogger(__name__)

FREEIPA_PACKAGES = ["freeipa-client", "oddjob-mkhomedir"]


class FreeIPAClientCharm(ops.CharmBase):
    """Subordinate charm that enrolls machines with a FreeIPA server."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.secret_changed, self._on_secret_changed)

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Install FreeIPA client packages."""
        self.unit.status = ops.MaintenanceStatus("installing freeipa-client packages")
        try:
            subprocess.check_call(
                ["apt-get", "update"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            subprocess.check_call(
                ["apt-get", "install", "-y", *FREEIPA_PACKAGES],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as e:
            logger.error("Failed to install packages: %s", e)
            self.unit.status = ops.BlockedStatus(
                "failed to install freeipa-client packages"
            )
            return
        self.unit.status = ops.BlockedStatus(
            "set freeipa-server, domain, and admin-password config"
        )

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Attempt enrollment when config changes."""
        self._enroll()

    def _on_secret_changed(self, event: ops.SecretChangedEvent) -> None:
        """Handle secret rotation."""
        event.secret.get_content(refresh=True)

    # -------------------------------------------------------------------
    # Secrets
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

    # -------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------

    def _enroll(self) -> None:
        """Enroll this machine with the FreeIPA server."""
        server = self.config.get("freeipa-server", "")
        domain = self.config.get("domain", "")
        admin_password = self._get_secret_value("admin-password", "password")
        realm = self.config.get("realm", "") or domain.upper()

        if not all([server, domain, admin_password]):
            missing = []
            if not server:
                missing.append("freeipa-server")
            if not domain:
                missing.append("domain")
            if not admin_password:
                missing.append("admin-password")
            self.unit.status = ops.BlockedStatus(
                f"missing config: {', '.join(missing)}"
            )
            return

        if self._is_enrolled() and not self.config.get("force-join"):
            self.unit.status = ops.ActiveStatus(f"enrolled in {realm}")
            return

        # Uninstall any partial/previous enrollment before re-enrolling
        if self._is_enrolled():
            logger.info("Uninstalling previous FreeIPA client config")
            subprocess.run(
                ["ipa-client-install", "--uninstall", "-U"],
                capture_output=True,
            )

        cmd = self._build_install_command(
            server=server,
            domain=domain,
            realm=realm,
            admin_password=admin_password,
        )

        self.unit.status = ops.MaintenanceStatus("enrolling with FreeIPA server")
        logger.info("Running: %s", " ".join(cmd))
        try:
            subprocess.check_call(cmd, timeout=300)
        except subprocess.CalledProcessError as e:
            logger.error("ipa-client-install failed (exit %d)", e.returncode)
            self.unit.status = ops.BlockedStatus(
                "ipa-client-install failed - check juju debug-log"
            )
            return
        except subprocess.TimeoutExpired:
            logger.error("ipa-client-install timed out")
            self.unit.status = ops.BlockedStatus("ipa-client-install timed out")
            return

        self._harden_ssh()
        self.unit.status = ops.ActiveStatus(f"enrolled in {realm}")

    def _harden_ssh(self) -> None:
        """Disable password authentication for SSH — key-only access."""
        sshd_config = Path("/etc/ssh/sshd_config.d/99-freeipa-hardening.conf")
        sshd_config.write_text(
            "# Managed by freeipa-client charm\n"
            "PasswordAuthentication no\n"
            "KbdInteractiveAuthentication no\n"
        )
        subprocess.run(["systemctl", "restart", "ssh"], capture_output=True)
        logger.info("SSH hardened: password auth disabled")

    def _build_install_command(
        self,
        server: str,
        domain: str,
        realm: str,
        admin_password: str,
    ) -> list[str]:
        """Build the ipa-client-install command."""
        args = [
            "ipa-client-install",
            "-U",
            "--server", server,
            "--domain", domain,
            "--realm", realm,
            "--principal", "admin",
            "--password", admin_password,
        ]

        if self.config.get("mkhomedir"):
            args.append("--mkhomedir")

        if self.config.get("no-ntp"):
            args.append("--no-ntp")

        if self.config.get("force-join"):
            args.append("--force-join")

        extra = self.config.get("extra-install-opts", "")
        if extra.strip():
            args.extend(extra.strip().split())

        return args

    def _is_enrolled(self) -> bool:
        """Check if this machine is already enrolled with a FreeIPA server."""
        try:
            result = subprocess.run(
                ["ipa-client-install", "--unattended"],
                capture_output=True,
                text=True,
            )
            return "already configured" in result.stderr.lower()
        except FileNotFoundError:
            return False


if __name__ == "__main__":
    ops.main(FreeIPAClientCharm)
