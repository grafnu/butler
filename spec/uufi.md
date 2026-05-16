# Unified UDMI Functional Interface (UUFI)

The **Unified UDMI Functional Interface (UUFI)** defines a standardized messaging mechanism for external applications (the **Client**) to integrate with a UDMI-managed system (the **System**).

## 1. Architecture

UUFI utilizes a messaging transport where Clients and Systems interact via dedicated topics and subscriptions.

### Message Flow
- **Publish (to System):** The Client publishes a UUFI-encapsulated UDMI message.
- **Receive (from System):** The System delivers UUFI-encapsulated UDMI messages to the Client.

## 2. Connectivity

### 2.1. Connection String
UUFI interfaces use a URL-like connection string format. Supported schemes: `mqtt://` and `pubsub://`.

Format: `scheme://[user@]host[:port][/path]`

- **Default User:** `unknown`
- **User Separation:** The `@` character is required if a `user` is specified.
- **Default Port:** Protocol-specific.

### 2.2. Protocol Mapping

#### PubSub (`pubsub://`)
- **Host:** GCP Project ID.
- **User:** Maps to a subscription suffix and the `principal` attribute.
- **Principal:** The `user` component with a trailing `@` (e.g., `user@`).
- **Path:** First component maps to the root topic name (default: `udmi_uufi`).
- **Subscription:** `{topic}+{user}`.
- **Filtering:** Subscriptions MUST filter for messages where the `principal` attribute matches the local identity or is absent.
- **Constraint:** `:port` is prohibited.
- **Debug Differentiation:** For singular receiver protocols (e.g., PubSub), append identifiers to the `user` component:
  - `butler`: (none)
  - `observe`: `.observe`
  - `verifier`: `.verifier`
  - `mocket`: `.mocket`

#### MQTT (`mqtt://`)
- **Host/Port:** Standard network mapping.
- **Topic Structure:** `[/{prefix}]/uufi/[r/{deviceRegistryId}/[d/{deviceId}/]]c/{subType}/{subFolder}`
  - The `prefix` is the optional path component of the connection string, representing one or more path segments. Implementations MUST NOT use the `user` component (username) as a topic prefix; it is reserved for identity and authentication. Implementations MUST ensure that joining a prefix and the `/uufi/` root does not result in double-slashes (e.g., `/prefix//uufi/...`). Topic normalization MUST be applied.
 Implementations MUST strip any leading or trailing slashes from the path component before using it as a `prefix`. In UDMI environments, the `prefix` often corresponds to the `UDMI_PREFIX` environment variable, which isolates multiple UDMI installations on the same messaging backbone.
- **Prefix Isolation:** The `prefix` MUST be used to isolate different environments sharing the same broker. If provided, it MUST be the leading part of the topic path (e.g. matching all segments of the path provided in the connection string). Implementations MUST support multi-segment prefixes and MUST NOT omit the prefix if provided in the connection string. All active subscriptions (including those for traffic observation) MUST be scoped to the provided prefix to ensure environmental isolation. Prefix enforcement MUST be strict: implementations MUST NOT publish to or subscribe from topics outside their designated prefix tree. To avoid collisions when multiple clients share the same broker, implementations MUST use unique MQTT Client IDs, for example by incorporating the prefix, a random nonce, or a combination of both.
- **Project Identity:** For the MQTT transport, the `projectId` field in the envelope MUST be treated as a general environment or project identifier. All components within a single UUFI session MUST use a consistent `projectId` (default: `vibrant`) to avoid ambiguity in message processing.
- **Cloud Model Service:**
  - **Discovery:** System components MUST dynamically discover registries and devices via the UUFI message bus. Clients publish a `query/cloud` message to `[/{prefix}]/uufi/c/query/cloud`.
  - **Subscription:** System components (specifically the Butler/Orchestrator) MUST subscribe to both the global model update topic `[/{prefix}]/uufi/c/model/cloud` and the device-specific model update topics `[/{prefix}]/uufi/r/+/d/+/c/model/cloud` to ensure all client-initiated updates are captured.
  - **Response:** System publishes the model to `[/{prefix}]/uufi/c/config/cloud`.
  - **Structure:** Uses nested **Registries** (Section 5.1).

## 3. Handshake Protocol

The Handshake Protocol is the message sequence and associated behavior used to establish an active UUFI session between a Client and the System.

### Sequence
1. **State Declaration**: The Client publishes a UDMI `state` message to `/uufi/c/state/udmi`.
   - **Payload**: MUST include the `setup` block.
   - **Addressing**: Registry-less topic. `source` in envelope contains Client identity.
2. **Configuration Confirmation**: The System publishes a UDMI `config` message to `/uufi/c/config/udmi`.
   - **Payload**: MUST include `setup` and `reply` blocks. Specifically, the `reply.msg_source` MUST match the `setup.msg_source` from the received Step 1 state message.
   - **Addressing**: Envelope `principal` MUST match Client's identity.

### Behavior and Lifecycle
- **Initiation**: Handshake is Client-initiated. The System MUST NOT initiate a handshake unless acting as a Client.
- **Wait for Handshake**: The System MUST wait for at least one Client handshake before becoming fully active, but it MUST NOT block indefinitely if no Clients are present.
- **Retries**: Clients MUST periodically republish the Step 1 state message every 5 seconds if a valid Step 2 confirmation has not been received, until the 60-second timeout.
- **Activation**: A Client is **Active** when the `reply.transaction_id` matches the original `state.setup.transaction_id`.
- **Timeout**: The Handshake MUST complete within 60s. On timeout, the Client MUST log a critical error and terminate (Fail-fast).

### Identity and Addressing
- **Principal Mapping**: For handshake replies, the System MUST use the `principal` or `source` from the received state message. The received message's `principal` MUST be used if present; otherwise, the `source` MUST be used as a fallback.
- **Matching**: When matching identities, implementations MUST only compare the base part of the identity (the portion before the first dot `.`) to allow for tool-specific tagging (e.g., `user.verifier` MUST match `user`).
- **Naming Schemes**: System components MUST NOT detect or reject identities with multiple components (e.g., `user.toolname`) as "manual differentiators" if they are part of a standardized naming scheme.
- **Registry Discovery:** The System MUST provide a `deviceRegistryId` in the `config.udmi` handshake reply if the System has prior knowledge of the Client's registry. If provided, the `deviceRegistryId` MUST be placed within the `setup` block of the payload. Once a Client receives a `deviceRegistryId`, it MUST use this value in the topic path for all subsequent device-specific messages (e.g., `events`, `state`, `config`). The provided `deviceRegistryId` is authoritative for the session; failure to use it in the topic path is a protocol violation. If no `deviceRegistryId` is provided, the Client MUST use the value `unknown`.
- **Responsiveness:** MQTT message callback handlers MUST NOT perform long-running or blocking operations. Heavy processing MUST be offloaded to a separate thread.

### 3.1 Metadata and Topic Conventions
- **Metadata Storage**: Device metadata (`make`, `model`) MUST be stored in a dedicated `meta` subsystem within the cloud model for consistency.
- **Metadata Ingestion**: System components MUST ingest and cache `make` and `model` information from all available sources (registration, cloud updates, and state reports).
- **Metadata Prioritization**: When ingesting metadata or versions, implementations MUST prioritize specific, non-fallback values. Known non-fallback values MUST NEVER be overwritten by uninitialized states (e.g., `"unknown"` or `"0.0.0"`).
- **Topic Slashes**: All UUFI topics MUST start with a leading slash `/`.
- **Blobset Config Keys**: Implementations MUST use standard UDMI keys in the `blobset` subfolder config payloads.
- **Local Blobs**: For local file references, the `url` MUST use the `file://` scheme. Recipients MUST strip the scheme to resolve the path.

## 4. Message Encapsulation

All messages are wrapped in a UUFI Envelope.

### Mandatory Payload Fields
Inner JSON `payload` object MUST include:
- `timestamp`: RFC 3339 (minimal precision).
- `version`: UDMI schema version.

### Transport Mapping

| Transport | Envelope Location | Payload Location |
| :--- | :--- | :--- |
| **PubSub** | Message Attributes | Message Data (JSON) |
| **MQTT** | JSON Wrapper | Payload `payload` key |

#### MQTT Constraints
- **Redundancy:** Envelope fields MUST NOT include data encoded in the topic path (`subType`, `subFolder`, and if present, `deviceRegistryId`, `deviceId`). Implementations MUST reject messages where envelope fields duplicate topic-encoded data.
- **Mandatory Fields:** The MQTT envelope MUST include `projectId`, `transactionId`, `publishTime`, `source`, `principal`, and `payload`.
- **Nesting:** UDMI message data MUST be nested within the `payload` key.

## 5. Cloud Model Operations

### 5.1. Schema
- **Operation:** `READ`, `CREATE`, `UPDATE`, `DELETE`, `BIND`, `UNBIND`.
- **Registries:** Map of `{registry_id}` to a map containing a **mandatory** `devices` key, which is a map of `{device_id}` to a map where each key is a `{subsystem_id}` (e.g., `main`, `meta`, `system`) and each value is the corresponding subsystem state.
- **CRITICAL Nesting Rule:** Implementations MUST NOT include an additional `subsystems` wrapper key between the `{device_id}` and the `{subsystem_id}` in any payload (especially `cloud` model and config messages). This is a foundational interoperability requirement. Subsystems MUST be direct children of the `{device_id}` key. Conversely, the `devices` level between `{registry_id}` and `{device_id}` MUST NOT be omitted. Extra or missing nesting levels WILL cause reconciliation failures in compliant orchestrators and MUST be flagged as a FAIL by verifiers. Implementations MUST allow and ignore any extra fields within a subsystem object (e.g., a redundant `subsystem` field) to ensure backward compatibility and robust partial merges.
- **Detail:** Additional parameters as specified in the authoritative schema.

### 5.2. Update Semantics (Partial Merge)
The `UPDATE` operation for the `cloud` subfolder is a partial merge at the device subsystem level. Existing fields not in the payload MUST NOT be modified.

## 6. UDMI to UUFI Mapping

| UDMI Operation | Envelope `subType` | Envelope `subFolder` | Direction |
| :--- | :--- | :--- | :--- |
| Handshake State | `state` | `udmi` | Publish |
| Handshake Config | `config` | `udmi` | Receive |
| Config Update | `config` | *varies* | Publish |
| State Event | `state` | *varies* | Receive |
| Telemetry | `events` | `pointset` | Receive |
| Discovery | `events` | `discovery` | Receive |
| Model Query | `query` | `cloud` | Publish |
| Model Update | `model` | `cloud` | Publish |
| Model Reply | `config` | `cloud` | Receive |
| Blobset Config | `config` | `blobset` | Publish |
| Blobset State | `state` | `blobset` | Receive |

### 7.1 Transaction Identifiers
- **Envelope Field:** MUST use `transactionId` (camelCase) in the MQTT and PubSub envelope.
- **Payload Field:** For handshake messages (`udmi` subfolder), the `setup` and `reply` blocks MUST use `transaction_id` (snake_case) to maintain UDMI schema compatibility.
- **Consistency:** The value of `transactionId` in the envelope MUST match the `transaction_id` in the payload for the same message.

### 7.2 MQTT QoS
- **Requirement:** QoS 1 (At Least Once) for all state and configuration messages.

### 7.3 Idempotency
- **Transaction ID:** MUST use a unique `transactionId` for message identification.
- **Deduplication:** Track `transactionId`s for 5 minutes. Implementations MUST ensure that deduplication logic does not interfere with the Handshake protocol (Section 3), which MUST reflect the same `transactionId` between Step 1 and Step 2. Specifically, a message MUST NOT be rejected as a duplicate if it is a valid handshake reply (Step 2) to a previously sent handshake state (Step 1).

## 8. Payload and Formatting Rules

### 8.1. Payload Structure
- **Nesting:** The `payload` object contains the fields of the UDMI message corresponding to the `subFolder` and `subType`.
- **Subsystem Nesting:** For `blobset` config and state payloads, data MUST be nested within a subsystem-id key (e.g., `system`) to support multi-subsystem devices. For maximum compatibility with UDMI standards, implementations MUST include a `blobs` wrapper key within the `blobset` object, containing the subsystem-id keys. Implementations MUST handle both nested (with the `blobs` wrapper) and unnested (flat) payloads for backward compatibility and robust interoperability.

- **UDMI Subfolder Nesting:** For messages using the `udmi` subfolder (e.g., handshakes), the payload data MUST be nested within a `udmi` key at the root of the `payload` object. Implementations MUST also handle flattened payloads for robustness.

- **Mandatory Fields:** `timestamp` and `version` MUST be at the root of the `payload` object.
- **Metadata:** The `make` and `model` fields are mandatory for all `blobset` subfolder payloads (state and config) within the subsystem nesting. These fields are essential for the System to locate the correct blob in the repository and MUST be included in every subsystem entry subject to reconciliation. Additionally, the `generation` field MUST be included in `blobset` config payloads to provide a temporal reference for the update command; it MUST follow the RFC 3339 minimal precision format (as defined in Section 8.2). Implementations MUST NOT use the version string as the value for the `generation` field.
- **Blobset Config URL:** The `url` field in a `blobset` config payload MUST be a valid URI. Implementations MUST support the `file://` scheme for local file references. When a `file://` URI is provided, the recipient MUST strip the scheme and any leading slashes as appropriate for the local operating system to resolve the absolute or relative path. For absolute paths, implementations MUST use the `file:///` (three slashes) format to avoid ambiguity with the `netloc` component of the URI.


### 8.2. Timestamp Format
- **Format:** RFC 3339 minimal precision (e.g., `2026-05-01T22:32:17Z`).
- **Timezone:** UTC required (`Z` suffix).
- **Precision:** System-originated messages MUST NOT include fractional seconds. Clients MUST be able to handle fractional seconds (microseconds) if provided by other components, and all implementations MUST handle them gracefully by ignoring extra precision if necessary.

### 8.3. MQTT Specific Rules
- **Redundancy Rule:** Implementations MUST reject messages where envelope fields duplicate topic-encoded data.
- **Leading Slash:** For MQTT transport, all UUFI topics MUST start with a leading slash `/`. Implementations MUST NOT accept or publish to topics lacking the leading slash.
- **Wildcards:** Subscription wildcards (e.g., `/#`) MUST also adhere to the leading slash rule and MUST be scoped to the connection-defined prefix to ensure consistent topic matching across the prefix tree.

### 8.5. Identity Isolation
To support multi-client environments on a shared messaging backbone (especially when topic prefixes are not used), implementations MUST strictly enforce identity isolation using the `principal` field:
- **Filtering:** All components MUST filter incoming messages and reject those where the `principal` field does not match their own local identity (accounting for identity differentiators). 
- **Enforcement:** For MQTT, if the `principal` field is missing from an incoming envelope, the message MUST be rejected to prevent cross-trial interference and ensure protocol compliance.
- **Differentiators:** When matching identities, implementations MUST only compare the base part of the identity (the portion before the first dot `.`) to allow for tool-specific tagging (e.g., `user.verifier` MUST match `user`).

---

# Appendix A: Schemas and Examples

This appendix references the formal JSON schemas and provides message examples for the UUFI protocol. The **UDMI Schema Repository** is the authoritative source for all message structures.

## A.1. Examples

### A.1.1. Handshake (PubSub)

**Attributes:**
```json
{
  "subFolder": "udmi",
  "subType": "state",
  "transactionId": "UUFI:sess123:001",
  "source": "client-id",
  "principal": "client-id@"
}
```

**Data:**
```json
{
  "version": "1.5.2",
  "timestamp": "2026-04-29T10:00:00Z",
  "setup": {
    "functions_ver": 9,
    "transaction_id": "UUFI:sess123:001",
    "msg_source": "client-id"
  }
}
```

### A.1.2. Pointset Config (MQTT)

**Topic:** `/uufi/r/reg-1/d/dev-1/c/config/pointset`

**Payload:**
```json
{
  "transactionId": "UUFI:sess123:002",
  "principal": "client-id",
  "payload": {
    "version": "1.5.2",
    "timestamp": "2026-04-29T10:05:00Z",
    "points": {
      "temp": { "set_value": 22.5 }
    }
  }
}
```

### A.1.3. Blobset Config (MQTT)

**Topic:** `/uufi/r/reg-1/d/dev-1/c/config/blobset`

**Payload:**
```json
{
  "transactionId": "UUFI:sess123:003",
  "principal": "client-id",
  "payload": {
    "version": "1.5.2",
    "timestamp": "2026-04-29T10:10:00Z",
    "blobset": {
      "blobs": {
        "system": {
          "phase": "apply",
          "url": "file:///path/to/bundle.bin",
          "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
          "generation": "2026-04-29T10:10:00Z"
        }
      }
    }
  }
}
```

## A.2. Authoritative Schemas

UUFI implementations MUST adhere to the following schemas from the UDMI repository:

| UUFI Component | Authoritative UDMI Schema |
| :--- | :--- |
| **Message Envelope** | `envelope.json` |
| **Handshake State** | `state_udmi.json` |
| **Handshake Config** | `config_udmi.json` |
| **Cloud Model** | `model_cloud.json` |
| **Blobset Config** | `config_blobset.json` |
| **Blobset State** | `state_blobset.json` |
