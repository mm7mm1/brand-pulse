terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
  }

  # State bucket is passed at init: terraform init -backend-config="bucket=<state-bucket>"
  backend "gcs" {
    prefix = "brand-pulse"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
