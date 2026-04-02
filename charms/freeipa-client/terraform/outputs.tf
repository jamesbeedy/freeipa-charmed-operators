output "app_name" {
  description = "Application name"
  value       = juju_application.freeipa_client.name
}

output "requires" {
  description = "Map of requirer endpoint names to relation names"
  value = {
    juju_info = "juju-info"
  }
}
