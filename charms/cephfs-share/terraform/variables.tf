variable "app_name" {
  description = "Application name"
  type        = string
  default     = "cephfs-share"
}

variable "base" {
  description = "Ubuntu base for the application"
  type        = string
  default     = "ubuntu@24.04"
}

variable "channel" {
  description = "Charm channel"
  type        = string
  default     = "latest/edge"
}

variable "config" {
  description = "Charm configuration options"
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Juju constraints for the application"
  type        = string
  default     = ""
}

variable "model_uuid" {
  description = "Juju model UUID"
  type        = string
}

variable "revision" {
  description = "Charm revision"
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}
