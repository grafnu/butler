# BlobStore Provider Interface and Implementations

This document defines the formal operational contract and implementation descriptions for the **BlobStore (Blob Repository)**. The BlobStore is designed as a modular, pluggable storage backend decoupled from the Butler core orchestrator and the UUFI bus, enabling separate deployment environments to use different storage architectures.

---

## 1. Core Provider Interface

Every BlobStore implementation MUST fulfill the following abstract interface contracts to ensure interoperability with the Butler orchestrator:

### 1.1. Methods and Behaviors

#### `resolve_package_metadata(make, model, blob_id, version) -> PackageMetadata`
*   **Purpose:** Resolves the metadata parameters for a specific software/firmware update package.
*   **Parameters:**
    *   `make` (string): The manufacturer of the device.
    *   `model` (string): The model identifier of the device.
    *   `blob_id` (string): The targeted software or subsystem ID.
    *   `version` (string): The target version string.
*   **Returns:** A `PackageMetadata` object containing:
    *   `sha256` (string): The hex-encoded SHA-256 hash of the binary file.
    *   `url` (string): A secure, accessible URI pointing to the binary package.

#### `get_package_url(make, model, blob_id, version) -> string`
*   **Purpose:** Resolves and returns a secure, client-accessible URI (e.g., `file://` or authenticated `https://`) for downloading the update bundle.

---

## 2. Pluggable Implementations

This section details the specification of supported BlobStore providers. New providers can be added to this section using the same template.

### 2.1. Reference Provider: Local Disk Storage (Testing/Development)

This provider is optimized for local integration testing, offline development, and simple local deployments.

#### 2.1.1. Storage Mapping and Software Catalog Schema
*   **Base Configuration:** `BUTLER_BLOBS_DIR` (defaults to `udmi_blob_store/packages`).
*   **Directory Path Structure:**
    `{BUTLER_BLOBS_DIR}/{make}/{model}/{blob_id}/{version}/`
*   **File Layout:**
    *   `bundle.bin` or `bundle.txt`: The raw update package payload binary or text file.
    *   *Note:* No separate hash files or database columns are required. The Local Disk provider calculates the SHA-256 hash of the payload file dynamically at runtime whenever `resolve_package_metadata` is invoked, preventing configuration skew.
*   **Software Catalog Schema (`model.json`):** Sourced from the file specified by `BUTLER_MODEL_FILE` (default: `udmi_blob_store/model.json`), this database maps target software versions to their relative or absolute file package URIs. It MUST follow the nested JSON schema:
    ```json
    {
      "{make}": {
        "{model}": {
          "{blob_id}": {
            "{version}": {
              "url": "file://{path_to_bundle_file}"
            }
          }
        }
      }
    }
    ```
    During a metadata query (`resolve_package_metadata`), the local provider queries this database structure to resolve the package `url`, then dynamically calculates and returns the payload's SHA-256 hash.

#### 2.1.2. URI Scheme
*   **Scheme:** `file://`
*   **Format:** `file://{absolute_or_relative_path_to_bundle_file}`
*   **Resolution:** The recipient client strips the `file://` scheme to resolve the path on the local file system. To ensure path consistency when components (such as the Device Under Test) are executed from different working directories (like the `./udmi/` subdirectory), all components MUST resolve relative path structures (e.g., `file://udmi_blob_store/packages/...`) relative to the project/workspace root directory.

#### 2.1.3. Security & Authentication
*   **Mechanism:** Standard POSIX file-level read permissions. No cryptographic signatures or network-level authentication headers are required.

---

### 2.2. Production Provider: GCP Google Cloud Storage (GCS)

This provider is designed for highly scalable, production-grade cloud deployments.

#### 2.2.1. Storage Mapping
*   **Base Configuration:** `BUTLER_GCS_BUCKET` (specifying the target GCS bucket name).
*   **Object Key Path Structure:**
    `gs://{BUTLER_GCS_BUCKET}/{make}/{model}/{blob_id}/{version}/bundle.bin`
*   **Metadata Layout:**
    *   `bundle.bin`: The binary package uploaded as a GCS object.
    *   `sha256`: The hex-encoded SHA-256 hash stored directly as **custom GCS object metadata** (e.g., `x-goog-meta-sha256`) on `bundle.bin`. The GCS provider extracts this hash dynamically from the object metadata at runtime during `resolve_package_metadata` to eliminate the need for separate files or external databases.

#### 2.2.2. URI Scheme
*   **Scheme:** `https://`
*   **Format:** Secure **GCS Signed URL** (e.g., `https://storage.googleapis.com/{bucket}/{key}?GoogleAccessId=...&Expires=...&Signature=...`)
*   **Resolution:** The recipient device downloads the package using standard HTTPS GET requests.

#### 2.2.3. Security & Authentication
*   **Mechanism:** GCS buckets are kept **fully private** (no public access allowed).
*   **Signing:** The BlobStore provider uses its GCP Service Account Credentials to generate a **time-limited Signed URL** (valid for a configurable period, e.g., 15 minutes) for each resolved query. This ensures only authorized devices with the temporary signature can download the binary.

---

## 3. Extensibility Guide (Adding New Providers)

To introduce a new storage provider (such as AWS S3, Azure Blob Storage, or an HTTP Artifactory):
1.  **Define Storage Mapping:** Detail how the hierarchy `{make}/{model}/{blob_id}/{version}` maps to the new storage engine's folders, buckets, or prefix paths, and where the SHA-256 hash is located (file vs. object metadata).
2.  **Declare URI Scheme:** Document the URI scheme used (e.g., `s3://` or `https://` with signed query strings).
3.  **Specify Security & Authentication:** Outline the access control mechanism, secure URL signing guidelines, and time-limit configurations.
