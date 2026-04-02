output "app_name" {
  description = "Application name"
  value       = juju_application.cephfs_share.name
}

output "provides" {
  description = "Map of provider endpoint names to relation names"
  value = {
    filesystem = "filesystem"
  }
}
