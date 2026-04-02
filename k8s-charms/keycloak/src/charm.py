#!/usr/bin/env python3
"""Keycloak K8s charm with FreeIPA LDAP federation via Pebble sidecar."""

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from string import Template

import ops

logger = logging.getLogger(__name__)

CONTAINER_NAME = "keycloak"
SERVICE_NAME = "keycloak"
REALM_TEMPLATE = Path("templates/realm.json")
REALM_IMPORT_PATH = "/opt/keycloak/data/import/realm.json"


class KeycloakK8sCharm(ops.CharmBase):
    """K8s sidecar charm for Keycloak with FreeIPA LDAP federation."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(
            self.on["keycloak"].pebble_ready, self._on_pebble_ready
        )
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.update_status, self._on_update_status)
        framework.observe(
            self.on.freeipa_relation_changed, self._on_freeipa_relation_changed
        )
        framework.observe(
            self.on.freeipa_relation_joined, self._on_freeipa_relation_joined
        )
        framework.observe(self.on.create_user_action, self._on_create_user_action)
        framework.observe(self.on.secret_changed, self._on_secret_changed)

    # -------------------------------------------------------------------
    # Secrets
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

    def _get_freeipa_admin_password(self) -> str:
        """Get FreeIPA admin password from config secret or relation secret."""
        pw = self._get_secret_value("freeipa-admin-password", "password")
        if pw:
            return pw
        relation = self.model.get_relation("freeipa")
        if relation and relation.app:
            secret_id = relation.data[relation.app].get("admin-password-secret-id")
            if secret_id:
                try:
                    secret = self.model.get_secret(id=secret_id)
                    return secret.get_content().get("password", "")
                except ops.SecretNotFoundError:
                    pass
        return ""

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent) -> None:
        """Configure Keycloak when Pebble is ready."""
        self._configure_workload(event.workload)

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config changes."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            return
        self._configure_workload(container)

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Periodic health check."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            self.unit.status = ops.WaitingStatus("waiting for Pebble")
            return
        try:
            svc = container.get_service(SERVICE_NAME)
            if svc.is_running():
                self.unit.status = ops.ActiveStatus()
            else:
                self.unit.status = ops.WaitingStatus("keycloak not running")
        except (ops.pebble.ConnectionError, ops.ModelError):
            self.unit.status = ops.WaitingStatus("pebble not ready")

    def _on_freeipa_relation_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Handle freeipa relation joined."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            event.defer()
            return
        self._configure_workload(container)

    def _on_freeipa_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        """Handle freeipa relation data changes."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            event.defer()
            return
        self._configure_workload(container)

    # -------------------------------------------------------------------
    # Workload configuration
    # -------------------------------------------------------------------

    def _configure_workload(self, container: ops.Container) -> None:
        """Push realm config and (re)plan the Pebble layer."""
        admin_password = self._get_secret_value("admin-password", "password")
        if not admin_password:
            self.unit.status = ops.BlockedStatus("admin-password config is required")
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
                "freeipa-admin-password config is required"
            )
            return

        # Push rendered realm.json into the container
        realm_json = self._render_realm(freeipa_config)
        if realm_json:
            container.push(REALM_IMPORT_PATH, realm_json, make_dirs=True)
            logger.info("Pushed realm.json into container")

        container.add_layer(
            CONTAINER_NAME,
            self._pebble_layer(),
            combine=True,
        )
        container.replan()

        self.unit.status = ops.ActiveStatus()

    def _pebble_layer(self) -> ops.pebble.LayerDict:
        """Build the Pebble layer for Keycloak."""
        admin_username = self.config.get("admin-username", "admin")
        admin_password = self._get_secret_value("admin-password", "password") or ""
        http_port = self.config.get("http-port", 8080)
        https_port = self.config.get("https-port", 8443)

        return {
            "summary": "keycloak layer",
            "description": "Pebble layer for Keycloak",
            "services": {
                SERVICE_NAME: {
                    "override": "replace",
                    "summary": "keycloak",
                    "command": (
                        "/opt/keycloak/bin/kc.sh start-dev "
                        "--import-realm --verbose"
                    ),
                    "startup": "enabled",
                    "environment": {
                        "KC_BOOTSTRAP_ADMIN_USERNAME": admin_username,
                        "KC_BOOTSTRAP_ADMIN_PASSWORD": admin_password,
                        "KC_HTTP_PORT": str(http_port),
                        "KC_HTTPS_PORT": str(https_port),
                    },
                },
            },
        }

    # -------------------------------------------------------------------
    # FreeIPA config and realm rendering
    # -------------------------------------------------------------------

    def _get_freeipa_config(self) -> dict[str, str]:
        """Get FreeIPA connection details from relation or config."""
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

    def _render_realm(self, freeipa_config: dict[str, str]) -> str | None:
        """Render realm.json template. Returns the rendered string or None."""
        template_path = self.charm_dir / REALM_TEMPLATE
        if not template_path.exists():
            return None

        domain = freeipa_config["domain"]
        base_dn = self._domain_to_base_dn(domain)

        template = Template(template_path.read_text())
        return template.safe_substitute(
            REALM_NAME=self.config.get("realm-name", "freeipa"),
            LDAP_HOST=freeipa_config["hostname"],
            LDAP_ADMIN_PASSWORD=self._get_freeipa_admin_password(),
            BIND_DN=f"uid=admin,cn=users,cn=accounts,{base_dn}",
            USERS_DN=f"cn=users,cn=accounts,{base_dn}",
            GROUPS_DN=f"cn=groups,cn=accounts,{base_dn}",
        )

    # -------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------

    def _on_create_user_action(self, event: ops.ActionEvent) -> None:
        """Create a user in FreeIPA, then sync into Keycloak."""
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            event.fail("Keycloak container not available")
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
                f"Keycloak realm '{realm}'."
            ),
        })

    # -------------------------------------------------------------------
    # FreeIPA JSON-RPC API
    # -------------------------------------------------------------------

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

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

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

        rpc_url = f"{base_url}/session/json"
        user_opts = {
            "givenname": first_name,
            "sn": last_name,
            "userpassword": password,
        }
        if email:
            user_opts["mail"] = email

        rpc_payload = {
            "method": "user_add",
            "params": [[username], user_opts],
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
                f"FreeIPA user_add error: "
                f"{result['error'].get('message', result['error'])}"
            )

    # -------------------------------------------------------------------
    # Keycloak admin API
    # -------------------------------------------------------------------

    def _get_admin_token(self) -> str:
        """Get an admin access token from Keycloak."""
        http_port = self.config.get("http-port", 8080)
        url = (
            f"http://localhost:{http_port}"
            "/realms/master/protocol/openid-connect/token"
        )
        data = urllib.parse.urlencode({
            "username": self.config.get("admin-username", "admin"),
            "password": self._get_secret_value("admin-password", "password") or "",
            "grant_type": "password",
            "client_id": "admin-cli",
        }).encode()

        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        return body["access_token"]

    def _keycloak_api_request(
        self, url: str, token: str, method: str = "GET", data: dict | None = None,
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
        """Trigger a full LDAP sync."""
        http_port = self.config.get("http-port", 8080)
        base = f"http://localhost:{http_port}/admin/realms/{realm}"

        url = f"{base}/components?type=org.keycloak.storage.UserStorageProvider"
        components = self._keycloak_api_request(url, token)
        if not components:
            return

        sync_url = (
            f"{base}/user-storage/{components[0]['id']}/sync"
            "?action=triggerFullSync"
        )
        self._keycloak_api_request(sync_url, token, method="POST")


if __name__ == "__main__":
    ops.main(KeycloakK8sCharm)
