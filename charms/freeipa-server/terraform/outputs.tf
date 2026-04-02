output "app_name" {
  description = "Application name"
  value       = juju_application.freeipa_server.name
}

output "provides" {
  description = "Map of provider endpoint names to relation names"
  value = {
    ldap    = "ldap"
    freeipa = "freeipa"
  }
}
