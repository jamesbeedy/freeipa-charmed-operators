output "app_name" {
  description = "Application name"
  value       = juju_application.keycloak.name
}

output "provides" {
  description = "Map of provider endpoint names to relation names"
  value = {
    oidc = "oidc"
  }
}

output "requires" {
  description = "Map of requirer endpoint names to relation names"
  value = {
    freeipa = "freeipa"
  }
}
