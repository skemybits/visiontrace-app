# VisionTrace App

Databricks App source for VisionTrace: structured security event search over restricted-zone camera footage.

Uses only synthetic demo data in `workspace.visiontrace`. No secrets or customer data are stored in this repository.

Pages: video list, per-video event timeline, and filter search over events. Reads Delta tables through the shared Serverless Starter Warehouse using the app service principal.
