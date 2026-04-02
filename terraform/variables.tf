variable "model_name" {
  description = "Juju model name"
  type        = string
}

variable "freeipa_domain" {
  description = "FreeIPA domain"
  type        = string
}

variable "freeipa_realm" {
  description = "FreeIPA realm"
  type        = string
}

variable "freeipa_password" {
  description = "FreeIPA admin password"
  type        = string
  sensitive   = true
}

variable "keycloak_password" {
  description = "Keycloak admin password"
  type        = string
  sensitive   = true
}

variable "ubuntu_units" {
  description = "Number of ubuntu units to deploy"
  type        = number
  default     = 1
}
