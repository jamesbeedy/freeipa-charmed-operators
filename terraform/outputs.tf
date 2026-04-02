output "model_name" {
  description = "Juju model name"
  value       = juju_model.freeipa.name
}

output "freeipa_server_app_name" {
  description = "FreeIPA server application name"
  value       = module.freeipa_server.app_name
}

output "freeipa_client_app_name" {
  description = "FreeIPA client application name"
  value       = module.freeipa_client.app_name
}

output "keycloak_app_name" {
  description = "Keycloak application name"
  value       = module.keycloak.app_name
}

output "cephfs_share_app_name" {
  description = "CephFS share application name"
  value       = module.cephfs_share.app_name
}

output "ubuntu_app_name" {
  description = "Ubuntu application name"
  value       = juju_application.ubuntu.name
}

output "filesystem_client_app_name" {
  description = "Filesystem client application name"
  value       = juju_application.filesystem_client.name
}
