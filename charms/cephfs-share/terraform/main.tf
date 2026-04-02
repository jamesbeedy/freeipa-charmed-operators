resource "juju_application" "cephfs_share" {
  name  = var.app_name
  model_uuid = var.model_uuid

  charm {
    name     = "cephfs-share"
    base     = var.base
    channel  = var.channel
    revision = var.revision
  }

  config      = var.config
  constraints = var.constraints
  units       = var.units
}
