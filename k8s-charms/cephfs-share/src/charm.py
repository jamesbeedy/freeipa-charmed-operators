#!/usr/bin/env python3
"""CephFS share proxy K8s charm — publishes CephFS info from config."""

import logging

import ops

logger = logging.getLogger(__name__)


class CephfsShareK8sCharm(ops.CharmBase):
    """K8s charm that proxies CephFS connection info via filesystem_info."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(
            self.on.filesystem_relation_joined, self._on_filesystem_joined
        )

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Validate config and publish filesystem info."""
        if not self._has_required_config():
            missing = self._missing_config()
            self.unit.status = ops.BlockedStatus(
                f"missing config: {', '.join(missing)}"
            )
            return

        self._publish_filesystem_info()
        self.unit.status = ops.ActiveStatus("cephfs info published")

    def _on_filesystem_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Publish CephFS info when a client joins."""
        if not self._has_required_config():
            event.defer()
            return
        self._publish_filesystem_info()

    # -------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------

    def _has_required_config(self) -> bool:
        """Check if all required config is present."""
        return not bool(self._missing_config())

    def _missing_config(self) -> list[str]:
        """Return list of missing required config keys."""
        missing = []
        for key in ("fsid", "monitor-hosts", "client-key"):
            if not self.config.get(key, ""):
                missing.append(key)
        return missing

    def _build_cephfs_uri(self) -> str:
        """Build the CephFS URI for the filesystem_info interface."""
        fsid = self.config.get("fsid", "")
        fs_name = self.config.get("fs-name", "cephfs")
        share_path = self.config.get("share-path", "/")
        client_user = self.config.get("client-user", "fs-client")
        client_key = self.config.get("client-key", "")
        monitor_hosts = self.config.get("monitor-hosts", "")

        mon_str = ",".join(monitor_hosts.strip().split())
        return (
            f"cephfs://{client_user}@({mon_str}){share_path}"
            f"?fsid={fsid}&name={fs_name}&auth=plain:{client_key}"
        )

    def _publish_filesystem_info(self) -> None:
        """Publish CephFS connection info on all filesystem relations."""
        if not self.unit.is_leader():
            return

        uri = self._build_cephfs_uri()
        logger.info(
            "Publishing CephFS URI: %s", uri.split("auth=")[0] + "auth=***"
        )

        for relation in self.model.relations.get("filesystem", []):
            relation.data[self.app]["endpoint"] = uri


if __name__ == "__main__":
    ops.main(CephfsShareK8sCharm)
