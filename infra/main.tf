locals {
  services = [
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
  ]
  layers = ["bronze", "silver", "gold", "config"]
  image  = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.pulse.repository_id}/brand-pulse:${var.image_tag}"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# --- Lake ------------------------------------------------------------------------------

resource "google_storage_bucket" "lake" {
  name                        = "${var.project_id}-pulse-lake"
  location                    = var.bq_location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # A slice is overwritten on every run; versions let a bad load be rolled back per file
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }
}

# --- Warehouse -------------------------------------------------------------------------

resource "google_bigquery_dataset" "layer" {
  for_each   = toset(local.layers)
  dataset_id = each.value
  location   = var.bq_location
}

# Bronze is read in place: no load job, no second copy of the raw data. The schema is
# explicit (the lake's PAGEVIEWS_SCHEMA) rather than autodetected, so the table can be
# created before the first file lands and a drifting file fails the read loudly.
resource "google_bigquery_table" "pageviews" {
  dataset_id          = google_bigquery_dataset.layer["bronze"].dataset_id
  table_id            = "pageviews"
  deletion_protection = false

  schema = jsonencode([
    { name = "brand_id", type = "STRING" },
    { name = "wiki_project", type = "STRING" },
    { name = "articles", type = "STRING" },
    { name = "date", type = "DATE" },
    { name = "views", type = "INTEGER" },
    { name = "_loaded_at", type = "TIMESTAMP" },
    { name = "_run_id", type = "STRING" },
  ])

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${google_storage_bucket.lake.name}/pageviews/*.parquet"]
  }
}

# --- Images and service accounts -------------------------------------------------------

resource "google_artifact_registry_repository" "pulse" {
  location      = var.region
  repository_id = "brand-pulse"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
}

resource "google_service_account" "pipeline" {
  account_id   = "pulse-pipeline"
  display_name = "brand-pulse jobs: ingest and dbt"
}

resource "google_service_account" "orchestrator" {
  account_id   = "pulse-orchestrator"
  display_name = "brand-pulse workflow and scheduler"
}

# Least privilege: the jobs write the lake bucket and the layer datasets, nothing else
resource "google_storage_bucket_iam_member" "pipeline_lake" {
  bucket = google_storage_bucket.lake.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_bigquery_dataset_iam_member" "pipeline_layers" {
  for_each   = google_bigquery_dataset.layer
  dataset_id = each.value.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_project_iam_member" "pipeline_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

# --- Jobs ------------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "ingest" {
  name                = "pulse-ingest"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.pipeline.email
      timeout         = "1800s"
      max_retries     = 0 # a rerun is the retry schedule's job, not a blind restart

      containers {
        image   = local.image
        command = ["pulse"]
        args    = ["ingest"]
        env {
          name  = "PULSE_LAKE_URI"
          value = "gs://${google_storage_bucket.lake.name}"
        }
        resources {
          limits = { cpu = "1", memory = "1Gi" }
        }
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_job" "transform" {
  name                = "pulse-transform"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.pipeline.email
      timeout         = "1800s"
      max_retries     = 0

      containers {
        image = local.image
        # Build first, then check freshness: a stale source still gets its marts rebuilt,
        # and the failed freshness check fails the job, which is what pages someone
        command = ["sh", "-c"]
        args    = ["dbt build && dbt source freshness"]
        env {
          name  = "DBT_TARGET"
          value = "prod"
        }
        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        resources {
          limits = { cpu = "1", memory = "2Gi" }
        }
      }
    }
  }
  depends_on = [google_project_service.apis]
}

# --- Orchestration ---------------------------------------------------------------------

resource "google_cloud_run_v2_job_iam_member" "orchestrator_runs_jobs" {
  for_each = {
    ingest    = google_cloud_run_v2_job.ingest.name
    transform = google_cloud_run_v2_job.transform.name
  }
  name     = each.value
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_project_iam_member" "orchestrator" {
  for_each = toset([
    "roles/run.viewer",        # poll the job executions it started
    "roles/workflows.invoker", # the scheduler starts the workflow
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.orchestrator.email}"
}

resource "google_workflows_workflow" "pipeline" {
  name                = "brand-pulse"
  region              = var.region
  service_account     = google_service_account.orchestrator.id
  source_contents     = file("${path.module}/../orchestration/pipeline.workflows.yaml")
  call_log_level      = "LOG_ERRORS_ONLY"
  deletion_protection = false

  user_env_vars = {
    INGEST_JOB    = google_cloud_run_v2_job.ingest.name
    TRANSFORM_JOB = google_cloud_run_v2_job.transform.name
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_scheduler_job" "pipeline" {
  for_each  = { daily = var.daily_schedule, retry = var.retry_schedule }
  name      = "brand-pulse-${each.key}"
  region    = var.region
  schedule  = each.value
  time_zone = "Etc/UTC"

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/${google_workflows_workflow.pipeline.id}/executions"
    body        = base64encode("{}")
    oauth_token {
      service_account_email = google_service_account.orchestrator.email
    }
  }
}

# --- Dashboard -------------------------------------------------------------------------

resource "google_service_account" "dashboard" {
  account_id   = "pulse-dashboard"
  display_name = "brand-pulse dashboard: reads gold and config only"
}

resource "google_bigquery_dataset_iam_member" "dashboard_reads" {
  for_each   = toset(["gold", "config"])
  dataset_id = google_bigquery_dataset.layer[each.value].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.dashboard.email}"
}

resource "google_project_iam_member" "dashboard_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.dashboard.email}"
}

resource "google_cloud_run_v2_service" "dashboard" {
  name                = "pulse-dashboard"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.dashboard.email
    # Streamlit keeps a websocket per browser session; affinity keeps it on one instance
    session_affinity = true

    scaling {
      min_instance_count = 0 # scales to zero: costs nothing while nobody is looking
      max_instance_count = 2
    }

    containers {
      image   = local.image
      command = ["streamlit", "run", "/app/dashboard/app.py"]
      args = [
        "--server.port=8080",
        "--server.address=0.0.0.0",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
      ]
      ports {
        container_port = 8080
      }
      env {
        name  = "PULSE_BQ_PROJECT"
        value = var.project_id
      }
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
    }
  }
  depends_on = [google_project_service.apis]
}

# A public demo: anyone with the link can open the dashboard (it shows public data only)
resource "google_cloud_run_v2_service_iam_member" "dashboard_public" {
  name     = google_cloud_run_v2_service.dashboard.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Alerting --------------------------------------------------------------------------

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "brand-pulse on-call"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

# One policy for every failure mode: a job's logging.error (skipped brands), a crashed
# job, a failed freshness check and a failed workflow execution all log at ERROR
resource "google_monitoring_alert_policy" "pipeline_errors" {
  display_name = "brand-pulse: pipeline error"
  combiner     = "OR"

  conditions {
    display_name = "ERROR from a pulse job or the workflow"
    condition_matched_log {
      filter = <<-EOT
        severity>=ERROR AND (
          (resource.type="cloud_run_job" AND resource.labels.job_name=~"^pulse-") OR
          (resource.type="workflows.googleapis.com/Workflow" AND resource.labels.workflow_id="brand-pulse")
        )
      EOT
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  notification_channels = google_monitoring_notification_channel.email[*].id
}
