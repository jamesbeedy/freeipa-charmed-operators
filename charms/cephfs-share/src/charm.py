#!/usr/bin/env python3
"""CephFS shared filesystem charm using MicroCeph."""

import json
import logging
import subprocess
from pathlib import Path

import ops

logger = logging.getLogger(__name__)

CEPH_READY_MARKER = Path("/var/snap/microceph/common/.cephfs-ready")


class CephfsShareCharm(ops.CharmBase):
    """Machine charm that deploys MicroCeph and provides CephFS via filesystem_info."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.filesystem_relation_joined, self._on_filesystem_joined)
        framework.observe(self.on.update_status, self._on_update_status)

    # -------------------------------------------------------------------
    # Event handlers
    # -------------------------------------------------------------------

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Install MicroCeph and set up CephFS."""
        channel = self.config.get("microceph-channel", "squid/stable")
        self.unit.status = ops.MaintenanceStatus(f"installing microceph ({channel})")

        try:
            subprocess.check_call(["snap", "install", "microceph", "--channel", channel])
        except subprocess.CalledProcessError as e:
            logger.error("Failed to install microceph snap: %s", e)
            self.unit.status = ops.BlockedStatus("failed to install microceph")
            return

        self.unit.status = ops.MaintenanceStatus("bootstrapping microceph cluster")
        try:
            self._bootstrap_ceph()
        except subprocess.CalledProcessError as e:
            logger.error("Failed to bootstrap MicroCeph: %s", e)
            self.unit.status = ops.BlockedStatus("failed to bootstrap microceph")
            return

        self.unit.status = ops.MaintenanceStatus("creating CephFS")
        try:
            self._setup_cephfs()
        except subprocess.CalledProcessError as e:
            logger.error("Failed to set up CephFS: %s", e)
            self.unit.status = ops.BlockedStatus("failed to create cephfs")
            return

        CEPH_READY_MARKER.touch()
        self.unit.status = ops.ActiveStatus("cephfs ready")

    def _on_start(self, event: ops.StartEvent) -> None:
        """Publish filesystem info if ready."""
        if self._is_ready():
            self._publish_filesystem_info()
            self.unit.status = ops.ActiveStatus("cephfs ready")

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Re-publish on config change."""
        if self._is_ready():
            self._publish_filesystem_info()

    def _on_filesystem_joined(self, event: ops.RelationJoinedEvent) -> None:
        """Publish CephFS info when a client joins."""
        if not self._is_ready():
            event.defer()
            return
        self._publish_filesystem_info()

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Periodic health check."""
        if self._is_ready():
            self.unit.status = ops.ActiveStatus("cephfs ready")

    # -------------------------------------------------------------------
    # MicroCeph setup
    # -------------------------------------------------------------------

    def _bootstrap_ceph(self) -> None:
        """Bootstrap a single-node MicroCeph cluster."""
        subprocess.check_call(["microceph", "cluster", "bootstrap"])

        # Add loop-backed OSDs
        osd_size = self.config.get("osd-size", "4G")
        osd_count = self.config.get("osd-count", 3)
        for _ in range(osd_count):
            subprocess.check_call(
                ["microceph", "disk", "add", f"loop,{osd_size},1"]
            )

    def _setup_cephfs(self) -> None:
        """Create CephFS pools, filesystem, and client auth."""
        fs_name = self.config.get("fs-name", "cephfs")
        client_user = self.config.get("client-user", "fs-client")

        # Create pools
        self._ceph_cmd("osd", "pool", "create", f"{fs_name}_data")
        self._ceph_cmd("osd", "pool", "create", f"{fs_name}_metadata")

        # Create filesystem
        self._ceph_cmd("fs", "new", fs_name, f"{fs_name}_metadata", f"{fs_name}_data")

        # Wait for MDS to become active
        self._ceph_cmd("fs", "set", fs_name, "allow_standby_replay", "true")

        # Authorize client
        self._ceph_cmd("fs", "authorize", fs_name, f"client.{client_user}", "/", "rw")

    def _ceph_cmd(self, *args: str) -> str:
        """Run a microceph.ceph command."""
        result = subprocess.run(
            ["microceph.ceph", *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    # -------------------------------------------------------------------
    # Filesystem info publishing
    # -------------------------------------------------------------------

    def _is_ready(self) -> bool:
        """Check if CephFS is set up."""
        return CEPH_READY_MARKER.exists()

    def _get_fsid(self) -> str:
        """Get the Ceph cluster FSID."""
        output = self._ceph_cmd("-s", "-f", "json")
        return json.loads(output)["fsid"]

    def _get_monitor_hosts(self) -> list[str]:
        """Get monitor host addresses."""
        output = self._ceph_cmd("mon", "dump", "-f", "json")
        data = json.loads(output)
        hosts = []
        for mon in data.get("mons", []):
            # addr format: "v2:10.0.0.1:3300/0,v1:10.0.0.1:6789/0"
            addr = mon.get("public_addr", mon.get("addr", ""))
            # Extract v1 (legacy) address — host only, no port
            for part in addr.split(","):
                if part.startswith("v1:"):
                    host = part[3:].split(":")[0]
                    hosts.append(host)
                    break
            else:
                # Fallback: strip version prefix and port
                clean = addr.split("/")[0]
                if clean.startswith("v2:"):
                    clean = clean[3:]
                host = clean.split(":")[0]
                hosts.append(host)
        return hosts

    def _get_client_key(self) -> str:
        """Get the cephx key for the client user."""
        client_user = self.config.get("client-user", "fs-client")
        return self._ceph_cmd("auth", "print-key", f"client.{client_user}")

    def _build_cephfs_uri(self) -> str:
        """Build the CephFS URI for the filesystem_info interface."""
        fs_name = self.config.get("fs-name", "cephfs")
        client_user = self.config.get("client-user", "fs-client")
        share_path = self.config.get("share-path", "/")

        fsid = self._get_fsid()
        monitors = self._get_monitor_hosts()
        key = self._get_client_key()

        # URI format: cephfs://user@(mon1,mon2,...)/path?fsid=X&name=Y&auth=plain:KEY
        mon_str = ",".join(monitors)
        return (
            f"cephfs://{client_user}@({mon_str}){share_path}"
            f"?fsid={fsid}&name={fs_name}&auth=plain:{key}"
        )

    def _publish_filesystem_info(self) -> None:
        """Publish CephFS connection info on all filesystem relations."""
        if not self.unit.is_leader():
            return

        try:
            uri = self._build_cephfs_uri()
        except Exception as e:
            logger.error("Failed to build CephFS URI: %s", e)
            return

        logger.info("Publishing CephFS URI: %s", uri.split("auth=")[0] + "auth=***")

        for relation in self.model.relations.get("filesystem", []):
            relation.data[self.app]["endpoint"] = uri


if __name__ == "__main__":
    ops.main(CephfsShareCharm)
