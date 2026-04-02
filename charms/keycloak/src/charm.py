#!/usr/bin/env python3
"""Keycloak identity provider charm with FreeIPA LDAP federation."""

import json
import logging
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from string import Template

import ops

logger = logging.getLogger(__name__)

KEYCLOAK_DATA_DIR = Path("/srv/keycloak-data")
KEYCLOAK_IMPORT_DIR = KEYCLOAK_DATA_DIR / "import"
REALM_TEMPLATE = Path("templates/realm.json")


class KeycloakCharm(ops.CharmBase):
    """Machine charm that deploys Keycloak via Docker with FreeIPA LDAP federation."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.stop, self._on_stop)
        framework.observe(self.on.update_status, self._on_update_status)
        framework.observe(self.on.freeipa_relation_changed, self._on_freeipa_relation_changed)
        framework.observe(self.on.freeipa_relation_joined, self._on_freeipa_relation_joined)
        framework.observe(self.on.create_user_action, self._on_create_user_action)
        framework.observe(self.on.set_user_ssh_key_action, self._on_set_user_ssh_key_action)
        framework.observe(self.on.secret_changed, self._on_secret_changed)

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Install Docker and pull the Keycloak image."""
        self.unit.status = ops.MaintenanceStatus("installing docker")
        try:
            self._install_docker()
        except subprocess.CalledProcessError as e:
            logger.error("Failed to install Docker: %s", e)
            self.unit.status = ops.BlockedStatus("failed to install docker")
            return

        image = self.config.get("image", "quay.io/keycloak/keycloak:latest")
        self.unit.status = ops.MaintenanceStatus(f"pulling {image}")
        try:
            subprocess.check_call(["docker", "pull", image])
        except subprocess.CalledProcessError as e:
            logger.error("Failed to pull image: %s", e)
            self.unit.status = ops.BlockedStatus("failed to pull keycloak image")
            return

        KEYCLOAK_DATA_DIR.mkdir(parents=True, exist_ok=True)
        KEYCLOAK_IMPORT_DIR.mkdir(parents=True, exist_ok=True)

        self._open_ports()
        self.unit.status = ops.WaitingStatus("waiting for config")

    def _on_start(self, event: ops.StartEvent) -> None:
        """Start Keycloak."""
        self._configure_keycloak()

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config changes — restart container with new config."""
        self._configure_keycloak()

    def _on_stop(self, event: ops.StopEvent) -> None:
        """Stop and remove the Keycloak container."""
        container_name = self.config.get("container-name", "keycloak")
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Periodic health check."""
        container_name = self.config.get("container-name", "keycloak")
        if self._is_container_running(container_name):
            self.unit.status = ops.ActiveStatus()
        elif self._has_required_config():
            self.unit.status = ops.WaitingStatus("keycloak container not running")

    def _on_freeipa_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Handle freeipa relation joined."""
        self._configure_keycloak()

    def _on_freeipa_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        """Handle freeipa relation data changes."""
        self._configure_keycloak()

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

    def _get_freeipa_admin_password(self) -> str:
        """Get the FreeIPA admin password from config secret or relation secret."""
        # Try config secret first
        pw = self._get_secret_value("freeipa-admin-password", "password")
        if pw:
            return pw

        # Try relation-provided secret
        relation = self.model.get_relation("freeipa")
        if relation and relation.app:
            secret_id = relation.data[relation.app].get("admin-password-secret-id")
            if secret_id:
                try:
                    secret = self.model.get_secret(id=secret_id)
                    content = secret.get_content()
                    return content.get("password", "")
                except ops.SecretNotFoundError:
                    pass
        return ""

    # -------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------

    def _install_docker(self) -> None:
        """Install Docker from system packages."""
        subprocess.check_call(["apt-get", "update"])
        subprocess.check_call(["apt-get", "install", "-y", "docker.io"])
        subprocess.check_call(["systemctl", "enable", "--now", "docker"])

    def _open_ports(self) -> None:
        """Open Keycloak service ports via Juju."""
        http_port = self.config.get("http-port", 8080)
        https_port = self.config.get("https-port", 8443)
        self.unit.open_port("tcp", http_port)
        self.unit.open_port("tcp", https_port)

    def _has_required_config(self) -> bool:
        """Check if all required config is present."""
        return bool(self._get_secret_value("admin-password", "password"))

    def _get_freeipa_config(self) -> dict[str, str]:
        """Get FreeIPA connection details from relation or config."""
        # Try relation first
        relation = self.model.get_relation("freeipa")
        if relation and relation.app:
            data = relation.data[relation.app]
            hostname = data.get("hostname", "")
            domain = data.get("domain", "")
            if hostname and domain:
                return {
                    "hostname": hostname,
                    "domain": domain,
                    "realm": data.get("realm", domain.upper()),
                }

        # Fall back to config
        server = self.config.get("freeipa-server", "")
        domain = self.config.get("freeipa-domain", "")
        if server and domain:
            return {
                "hostname": server,
                "domain": domain,
                "realm": domain.upper(),
            }

        return {}

    def _domain_to_base_dn(self, domain: str) -> str:
        """Convert a domain like 'freeipa.local' to 'dc=freeipa,dc=local'."""
        return ",".join(f"dc={part}" for part in domain.split("."))

    def _render_realm(self, freeipa_config: dict[str, str]) -> Path:
        """Render the realm.json template with FreeIPA connection details."""
        template_path = self.charm_dir / REALM_TEMPLATE
        template_content = template_path.read_text()

        domain = freeipa_config["domain"]
        base_dn = self._domain_to_base_dn(domain)
        realm_name = self.config.get("realm-name", "freeipa")
        freeipa_password = self._get_freeipa_admin_password()

        # Substitute template variables
        # The realm.json uses ${VAR} syntax — we use string.Template
        template = Template(template_content)
        rendered = template.safe_substitute(
            REALM_NAME=realm_name,
            LDAP_HOST=freeipa_config["hostname"],
            LDAP_ADMIN_PASSWORD=freeipa_password,
            BIND_DN=f"uid=admin,cn=users,cn=accounts,{base_dn}",
            USERS_DN=f"cn=users,cn=accounts,{base_dn}",
            GROUPS_DN=f"cn=groups,cn=accounts,{base_dn}",
        )

        output = KEYCLOAK_IMPORT_DIR / "realm.json"
        output.write_text(rendered)
        logger.info("Rendered realm.json to %s", output)
        return output

    def _configure_keycloak(self) -> None:
        """Ensure Keycloak container is running with current config."""
        admin_password = self._get_secret_value("admin-password", "password")
        if not admin_password:
            self.unit.status = ops.BlockedStatus(
                "admin-password secret required — see: juju add-secret"
            )
            return

        freeipa_config = self._get_freeipa_config()
        freeipa_password = self._get_freeipa_admin_password()

        if not freeipa_config:
            self.unit.status = ops.BlockedStatus(
                "missing freeipa config: set freeipa-server and freeipa-domain, "
                "or add freeipa relation"
            )
            return

        if not freeipa_password:
            self.unit.status = ops.BlockedStatus(
                "freeipa-admin-password secret required"
            )
            return

        container_name = self.config.get("container-name", "keycloak")

        # Always re-render realm and restart to pick up config changes
        self._render_realm(freeipa_config)

        # Stop existing container if running
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

        image = self.config.get("image", "quay.io/keycloak/keycloak:latest")
        admin_username = self.config.get("admin-username", "admin")
        http_port = self.config.get("http-port", 8080)
        https_port = self.config.get("https-port", 8443)

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--network", "host",
            "-v", f"{KEYCLOAK_IMPORT_DIR}:/opt/keycloak/data/import:Z",
            "-e", f"KC_BOOTSTRAP_ADMIN_USERNAME={admin_username}",
            "-e", f"KC_BOOTSTRAP_ADMIN_PASSWORD={admin_password}",
            "-e", f"KC_HTTP_PORT={http_port}",
            "-e", f"KC_HTTPS_PORT={https_port}",
            image,
            "start-dev", "--import-realm", "--verbose",
        ]

        self.unit.status = ops.MaintenanceStatus("starting Keycloak container")
        logger.info("Running: %s", " ".join(cmd))

        try:
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as e:
            logger.error("Failed to start Keycloak container: %s", e)
            self.unit.status = ops.BlockedStatus(
                "failed to start keycloak container"
            )
            return

        self.unit.status = ops.ActiveStatus()

    # -------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------

    def _on_create_user_action(self, event: ops.ActionEvent) -> None:
        """Create a user in FreeIPA via LDAP, then sync into Keycloak."""
        container_name = self.config.get("container-name", "keycloak")
        if not self._is_container_running(container_name):
            event.fail("Keycloak container is not running")
            return

        username = event.params["username"]
        first_name = event.params["first-name"]
        last_name = event.params["last-name"]
        email = event.params.get("email", "")
        password = event.params["password"]
        realm = self.config.get("realm-name", "freeipa")

        freeipa_config = self._get_freeipa_config()
        if not freeipa_config:
            event.fail("FreeIPA config not available")
            return

        freeipa_password = self._get_freeipa_admin_password()
        if not freeipa_password:
            event.fail("freeipa-admin-password config is required")
            return

        # Create user in FreeIPA via ipa-server JSON-RPC API
        try:
            self._create_freeipa_user(
                freeipa_config=freeipa_config,
                freeipa_password=freeipa_password,
                username=username,
                first_name=first_name,
                last_name=last_name,
                email=email,
                password=password,
            )
        except Exception as e:
            event.fail(f"Failed to create FreeIPA user: {e}")
            return

        # Trigger Keycloak LDAP sync to import the new user
        try:
            token = self._get_admin_token()
            self._trigger_ldap_sync(token, realm)
        except Exception as e:
            logger.warning("Keycloak LDAP sync failed: %s", e)

        event.set_results({
            "result": "success",
            "username": username,
            "realm": realm,
            "message": (
                f"User '{username}' created in FreeIPA and synced to "
                f"Keycloak realm '{realm}'. The user should be available "
                "on all enrolled client machines."
            ),
        })

    def _create_freeipa_user(
        self,
        freeipa_config: dict[str, str],
        freeipa_password: str,
        username: str,
        first_name: str,
        last_name: str,
        email: str,
        password: str,
    ) -> None:
        """Create a user in FreeIPA via its JSON-RPC API."""
        hostname = freeipa_config["hostname"]
        base_url = f"https://{hostname}/ipa"

        # Step 1: Get a session cookie by authenticating
        login_url = f"{base_url}/session/login_password"
        login_data = urllib.parse.urlencode({
            "user": "admin",
            "password": freeipa_password,
        }).encode()

        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        login_req = urllib.request.Request(
            login_url,
            data=login_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{base_url}/session/login_password",
            },
        )
        with urllib.request.urlopen(login_req, timeout=30, context=ctx) as resp:
            cookie = resp.headers.get("Set-Cookie", "").split(";")[0]

        # Step 2: Call user_add via JSON-RPC
        rpc_url = f"{base_url}/session/json"
        user_args = [username]
        user_opts = {
            "givenname": first_name,
            "sn": last_name,
            "userpassword": password,
            "loginshell": "/bin/bash",
        }
        if email:
            user_opts["mail"] = email

        rpc_payload = {
            "method": "user_add",
            "params": [user_args, user_opts],
            "id": 0,
        }

        rpc_req = urllib.request.Request(
            rpc_url,
            data=json.dumps(rpc_payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Referer": f"{base_url}/session/json",
                "Cookie": cookie,
            },
        )
        with urllib.request.urlopen(rpc_req, timeout=30, context=ctx) as resp:
            result = json.loads(resp.read())

        if result.get("error"):
            raise RuntimeError(
                f"FreeIPA user_add error: {result['error'].get('message', result['error'])}"
            )

    def _on_set_user_ssh_key_action(self, event: ops.ActionEvent) -> None:
        """Add an SSH public key to a FreeIPA user."""
        username = event.params["username"]
        ssh_key = event.params["ssh-key"]

        freeipa_config = self._get_freeipa_config()
        if not freeipa_config:
            event.fail("FreeIPA config not available")
            return

        freeipa_password = self._get_freeipa_admin_password()
        if not freeipa_password:
            event.fail("freeipa-admin-password config is required")
            return

        try:
            self._set_freeipa_user_ssh_key(
                freeipa_config=freeipa_config,
                freeipa_password=freeipa_password,
                username=username,
                ssh_key=ssh_key,
            )
        except Exception as e:
            event.fail(f"Failed to set SSH key: {e}")
            return

        event.set_results({
            "result": "success",
            "username": username,
            "message": (
                f"SSH key added for user '{username}' in FreeIPA. "
                "The key will be available on all enrolled clients via SSSD."
            ),
        })

    def _set_freeipa_user_ssh_key(
        self,
        freeipa_config: dict[str, str],
        freeipa_password: str,
        username: str,
        ssh_key: str,
    ) -> None:
        """Add an SSH public key to a FreeIPA user via JSON-RPC API."""
        hostname = freeipa_config["hostname"]
        base_url = f"https://{hostname}/ipa"

        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Authenticate
        login_url = f"{base_url}/session/login_password"
        login_data = urllib.parse.urlencode({
            "user": "admin",
            "password": freeipa_password,
        }).encode()

        login_req = urllib.request.Request(
            login_url,
            data=login_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{base_url}/session/login_password",
            },
        )
        with urllib.request.urlopen(login_req, timeout=30, context=ctx) as resp:
            cookie = resp.headers.get("Set-Cookie", "").split(";")[0]

        # Call user_mod to set the SSH key
        rpc_url = f"{base_url}/session/json"
        rpc_payload = {
            "method": "user_mod",
            "params": [[username], {"ipasshpubkey": [ssh_key]}],
            "id": 0,
        }

        rpc_req = urllib.request.Request(
            rpc_url,
            data=json.dumps(rpc_payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Referer": f"{base_url}/session/json",
                "Cookie": cookie,
            },
        )
        with urllib.request.urlopen(rpc_req, timeout=30, context=ctx) as resp:
            result = json.loads(resp.read())

        if result.get("error"):
            raise RuntimeError(
                f"FreeIPA user_mod error: "
                f"{result['error'].get('message', result['error'])}"
            )

    def _get_admin_token(self) -> str:
        """Get an admin access token from Keycloak."""
        http_port = self.config.get("http-port", 8080)
        admin_username = self.config.get("admin-username", "admin")
        admin_password = self._get_secret_value("admin-password", "password") or ""

        url = (
            f"http://localhost:{http_port}"
            "/realms/master/protocol/openid-connect/token"
        )
        data = urllib.parse.urlencode({
            "username": admin_username,
            "password": admin_password,
            "grant_type": "password",
            "client_id": "admin-cli",
        }).encode()

        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        return body["access_token"]

    def _keycloak_api_request(
        self,
        url: str,
        token: str,
        method: str = "GET",
        data: dict | None = None,
    ) -> dict | None:
        """Make an authenticated request to the Keycloak admin API."""
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read()
            if content:
                return json.loads(content)
        return None

    def _trigger_ldap_sync(self, token: str, realm: str) -> None:
        """Trigger a full LDAP sync for the realm's user federation provider."""
        http_port = self.config.get("http-port", 8080)
        base = f"http://localhost:{http_port}/admin/realms/{realm}"

        # Find the LDAP storage provider component ID
        url = f"{base}/components?type=org.keycloak.storage.UserStorageProvider"
        components = self._keycloak_api_request(url, token)
        if not components:
            return
        component_id = components[0]["id"]

        # Trigger sync
        sync_url = (
            f"{base}/user-storage/{component_id}/sync?action=triggerFullSync"
        )
        self._keycloak_api_request(sync_url, token, method="POST")

    # -------------------------------------------------------------------
    # Container helpers
    # -------------------------------------------------------------------

    def _is_container_running(self, container_name: str) -> bool:
        """Check if the Docker container is running."""
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"


if __name__ == "__main__":
    ops.main(KeycloakCharm)
