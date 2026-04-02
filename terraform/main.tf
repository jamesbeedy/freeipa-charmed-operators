resource "juju_model" "freeipa" {
  name = var.model_name
}

module "freeipa_server" {
  source     = "../charms/freeipa-server/terraform"
  model_uuid = juju_model.freeipa.uuid
  config = {
    domain           = var.freeipa_domain
    realm            = var.freeipa_realm
    "admin-password" = var.freeipa_password
  }
}

module "freeipa_client" {
  source     = "../charms/freeipa-client/terraform"
  model_uuid = juju_model.freeipa.uuid
  config = {
    "freeipa-server" = "freeipa-server.${var.freeipa_domain}"
    domain           = var.freeipa_domain
    realm            = var.freeipa_realm
    "admin-password" = var.freeipa_password
  }
}

module "keycloak" {
  source     = "../charms/keycloak/terraform"
  model_uuid = juju_model.freeipa.uuid
  config = {
    "admin-password"         = var.keycloak_password
    "freeipa-server"         = "freeipa-server.${var.freeipa_domain}"
    "freeipa-domain"         = var.freeipa_domain
    "freeipa-admin-password" = var.freeipa_password
  }
}

module "cephfs_share" {
  source     = "../charms/cephfs-share/terraform"
  model_uuid = juju_model.freeipa.uuid
}

resource "juju_application" "ubuntu" {
  name       = "ubuntu"
  model_uuid = juju_model.freeipa.uuid
  units      = var.ubuntu_units

  charm {
    name    = "ubuntu"
    channel = "latest/stable"
    base    = "ubuntu@24.04"
  }
}

resource "juju_application" "filesystem_client" {
  name       = "filesystem-client"
  model_uuid = juju_model.freeipa.uuid

  charm {
    name    = "filesystem-client"
    channel = "latest/edge"
    base    = "ubuntu@24.04"
  }

  config = {
    mountpoint = "/home"
  }
}

# freeipa-client:juju-info -> ubuntu:juju-info
resource "juju_integration" "freeipa_client_ubuntu" {
  model_uuid = juju_model.freeipa.uuid

  application {
    name     = module.freeipa_client.app_name
    endpoint = module.freeipa_client.requires.juju_info
  }

  application {
    name     = juju_application.ubuntu.name
    endpoint = "juju-info"
  }
}

# keycloak:freeipa -> freeipa-server:freeipa
resource "juju_integration" "keycloak_freeipa_server" {
  model_uuid = juju_model.freeipa.uuid

  application {
    name     = module.keycloak.app_name
    endpoint = module.keycloak.requires.freeipa
  }

  application {
    name     = module.freeipa_server.app_name
    endpoint = module.freeipa_server.provides.freeipa
  }
}

# filesystem-client:juju-info -> ubuntu:juju-info
resource "juju_integration" "filesystem_client_ubuntu" {
  model_uuid = juju_model.freeipa.uuid

  application {
    name     = juju_application.filesystem_client.name
    endpoint = "juju-info"
  }

  application {
    name     = juju_application.ubuntu.name
    endpoint = "juju-info"
  }
}

# filesystem-client:filesystem -> cephfs-share:filesystem
resource "juju_integration" "filesystem_client_cephfs_share" {
  model_uuid = juju_model.freeipa.uuid

  application {
    name     = juju_application.filesystem_client.name
    endpoint = "filesystem"
  }

  application {
    name     = module.cephfs_share.app_name
    endpoint = module.cephfs_share.provides.filesystem
  }
}
