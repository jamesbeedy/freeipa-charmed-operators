resource "juju_application" "keycloak" {
  name  = var.app_name
  model_uuid = var.model_uuid

  charm {
    name     = "keycloak"
    base     = var.base
    channel  = var.channel
    revision = var.revision
  }

  config      = var.config
  constraints = var.constraints
  units       = var.units
}
