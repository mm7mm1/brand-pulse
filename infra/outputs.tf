output "lake_bucket" {
  value = google_storage_bucket.lake.name
}

output "image" {
  value = local.image
}

output "workflow" {
  value = google_workflows_workflow.pipeline.id
}
