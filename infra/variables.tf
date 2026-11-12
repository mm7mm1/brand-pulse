variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "bq_location" {
  description = "BigQuery and lake bucket location - kept equal so external tables can read the lake"
  type        = string
  default     = "US"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "alert_email" {
  description = "Where pipeline errors are emailed; empty = the alert still fires, visible in Monitoring"
  type        = string
  default     = ""
}

variable "daily_schedule" {
  description = "Main run, UTC. Wikimedia publishes a day's pageviews a few hours after midnight UTC."
  type        = string
  default     = "0 6 * * *"
}

variable "retry_schedule" {
  description = "Second run of the same window: ingestion is idempotent, so it only fills what the morning skipped"
  type        = string
  default     = "0 10 * * *"
}
