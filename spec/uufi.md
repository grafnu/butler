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
- **Principal:** The `user` component with a trailing `@`.
- **Path:** First component maps to the root topic name (default: `udmi_uufi`).
- **Subscription:** `{topic}+{user}`.
- **Filtering:** Subscriptions MUST filter for messages where the `principal` attribute matches the local identity or is absent.
- **Constraint:** `:port` is prohibited.

#### MQTT (`mqtt://`)
- **Host/Port:** Standard network mapping.
- **Topic Structure:** `/uufi/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}`
- **Topic Isolation:** The `principal` identifier MUST be included in the JSON envelope.
- **Cloud Model Service:**
  - **Discovery:** System components MUST dynamically discover registries and devices via the UUFI message bus. Clients publish a `query/cloud` message to `[/{prefix}]/uufi/c/query/cloud`.
  - **Response:** The **System** (and ONLY the System) MUST respond by publishing the requested model information to `[/{prefix}]/uufi/c/config/cloud`.
  - **Structure:** Uses nested **Registries** (Section 5.1).

## 3. Handshake Protocol

Handshake is Client-initiated. The System MUST NOT initiate a handshake unless acting as a Client.

### Step 1: State Declaration
The Client publishes a UDMI `state` message to `/uufi/c/state/udmi`.
- **Payload:** Must include `udmi.setup` (see Schema 10.2).
- **Addressing:** Registry-less topic. `source` in envelope contains Client identity.

### Step 2: Configuration Confirmation
The System publishes a UDMI `config` message to `/uufi/c/config/udmi`.
- **Payload:** Must include `udmi.setup` and `udmi.reply` (see Schema 10.3).
- **Addressing:** Envelope `principal` MUST match Client's identity. To ensure interoperability with tagged identities (Section 3.1), "matching" the identity MUST account for identity differentiators (e.g., matching the base part of the identity before the first differentiator separator like a dot). Specifically, to ensure a handshake reply reaches a client using an identity differentiator (e.g., `user.toolname`), the System MUST use the exact `principal` (or `source` if `principal` is missing) from the received Step 1 `state` message as the `target_principal` for the Step 2 `config` reply.

### 3.1. Tagged Identities
A **Tagged Identity** is a principal identifier that includes an optional differentiator (e.g., `user.tag`). Differentiators are separated by the dot (`.`) character.
- **Isolation:** Implementations MUST only compare the base part of the identity (the segment before the first dot) when performing identity-based isolation or filtering.
- **Interoperability:** System components MUST preserve and reflect the full tagged identity when responding to client-initiated messages (e.g., handshake replies, cloud model responses).

**Retries:** The Client MUST periodically republish the Step 1 state message (e.g., every 5 seconds) if a valid Step 2 confirmation has not been received, until the 60-second timeout.

**Activation:** The Client is **Active** when `udmi.reply.transaction_id` matches the original `state.udmi.setup.transaction_id`.

### Registry ID Discovery
- **Default:** `default`
- **Discovery:** The System provides the `{registryId}` in `config.udmi` during handshake.
- **Priority:** If a Client has a pre-configured registry ID (e.g., via command-line arguments), it MUST prioritize it over the one provided by the System during handshake to ensure identity consistency in restricted environments.

### Timeouts
- **Window:** 60 seconds.
- **Failure:** On timeout, the Client MUST log a critical error and terminate (Fail-fast).

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
- **Redundancy:** Envelope fields MUST NOT include data encoded in the topic path (`subType`, `subFolder`, and if present, `deviceRegistryId`, `deviceId`). Implementations MUST NOT reject messages where these fields are present in the envelope but NOT in the topic path (e.g., registry-less topics).
- **Nesting:** UDMI message data MUST be nested within the `payload` key.

## 5. Cloud Model Operations

### 5.1. Schema
- **Operation:** `READ`, `CREATE`, `UPDATE`, `DELETE`, `BIND`, `UNBIND`.
- **Registries:** Map of `{registry_id}` to a map containing a **mandatory** `devices` key, which is a map of `{device_id}` to a map where each key is a `{subsystem_id}` (e.g., `main`, `meta`, `system`) and each value is the corresponding subsystem state.
- **CRITICAL Nesting Rule:** Implementations MUST NOT include an additional `subsystems` wrapper key between the `{device_id}` and the `{subsystem_id}` in any payload (especially `cloud` model and config messages). This is a foundational interoperability requirement. Subsystems MUST be direct children of the `{device_id}` key. Conversely, the `devices` level between `{registry_id}` and `{device_id}` MUST NOT be omitted. Extra or missing nesting levels WILL cause reconciliation failures in compliant orchestrators and MUST be flagged as a FAIL by verifiers. Implementations MUST allow and ignore any extra fields within a subsystem object (e.g., a redundant `subsystem` field) to ensure backward compatibility and robust partial merges.
- **Detail:** Additional parameters as specified in the authoritative schema.

### 5.2. Update Semantics (Partial Merge)
The `UPDATE` operation for the `cloud` subfolder is a partial merge at the device subsystem level. Existing fields not in the payload MUST NOT be modified.

### 5.3. Response Loop Prevention
To prevent infinite message loops, components responding to cloud model operations (e.g., a Cloud Model Server or Mocket emulator) MUST NOT process messages with a `status` field as if they were new requests. Responses MUST be identified and handled as confirmations or completions of previous operations.

## 6. UDMI to UUFI Mapping

| UDMI Operation | Envelope `subType` | Envelope `subFolder` | Direction |
| :--- | :--- | :--- | :--- |
| Handshake State | `state` | `udmi` | Publish |
| Handshake Config | `config` | `udmi` | Receive |
| Config Update | `config` | *varies* | Publish |
| State Event | `state` | *varies* | Receive |
| Telemetry | events | pointset | Receive |
| Discovery | events | discovery | Receive |
| Validation | events | validation | Receive |
| Model Query | query | cloud | Publish |

| Model Update | `model` | `cloud` | Publish |
| Model Reply | `config` | `cloud` | Receive |
| Update Config | `config` | `blobset` | Publish |
| Update State | `state` | `blobset` | Receive |

### 7.2 MQTT QoS
- **Requirement:** QoS 1 (At Least Once) for all state and configuration messages.

### 7.3 Idempotency
- **Transaction ID:** MUST use a unique `transactionId` for message identification.
- **Deduplication:** Track `transactionId`s for 5 minutes. Implementations MUST ensure that deduplication logic does not interfere with the Handshake protocol (Section 3), which MUST reflect the same `transactionId` between Step 1 and Step 2. Specifically, a message MUST NOT be rejected as a duplicate if it is a valid handshake reply (Step 2) to a previously sent handshake state (Step 1).

### 7.4. Self-Message Filtering
To ensure efficiency and avoid redundant processing, components MUST ignore incoming messages where the `source` field in the envelope matches their own `source` identifier. While the Deduplication rule (Section 7.3) allows processing of self-messages for specific local state synchronization, components MUST NOT engage in behavior where processing a self-originated message triggers the publication of a new message that could sustain a loop.

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
- **Prefix Isolation:** Implementations MUST strictly enforce the prefix tree for all outgoing and incoming messages. For incoming messages, components MUST validate that the topic starts with the connection-defined prefix (if one is specified in the connection string); messages from other prefixes MUST be ignored.

### 8.4. Version String Format
- **Default/Unknown Version:** Implementations MUST use the string `0.0.0` to represent an unknown, uninitialized, or null version (e.g., for `current_version`, `target_version`, or `lkg_version`).
- **Persistence:** To ensure stability and prevent accidental data loss during partial merges, a non-zero version string (one that is NOT `0.0.0`) MUST NEVER be overwritten by `0.0.0`.

### 8.5. Identity Isolation
To support multi-client environments on a shared messaging backbone (especially when topic prefixes are not used), implementations MUST strictly enforce identity isolation using the `principal` field:
- **Filtering:** All components MUST filter ALL incoming messages (including `config`, `state`, `query`, and `model` types) and reject those where the `principal` field does not match their own local identity (accounting for identity differentiators). 
- **Enforcement:** For MQTT, if the `principal` field is missing from an incoming envelope, the message MUST be rejected to prevent cross-trial interference and ensure protocol compliance.
- **Differentiators:** When matching identities, implementations MUST only compare the base part of the identity (the portion before the first dot `.`) to allow for tool-specific tagging (e.g., `user.verifier` MUST match `user`).

## 9. Reliability

### MQTT QoS
- **Requirement:** QoS 1 (At Least Once) for all state and configuration messages.

### Idempotency
- **Nonce:** MUST use a unique message instance ID (8-digit hex nonce) for identification.
- **Deduplication:** Track `nonce` values for 5 minutes. If `nonce` is not present, implementations MUST use `transactionId` for deduplication, EXCEPT for messages in the `udmi` subfolder (e.g., handshakes), which MUST NOT be deduplicated by `transactionId` to allow for protocol retries.

## 10. Schemas

### 10.1. UUFI Message Envelope
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "UufiEnvelope",
  "type": "object",
  "properties": {
    "projectId": { "type": "string", "description": "GCP Project ID" },
    "deviceRegistryId": { "type": "string", "description": "Managed Registry ID" },
    "deviceId": { "type": "string", "description": "Target/Source Device ID" },
    "subFolder": { "type": "string", "description": "UDMI subFolder" },
    "subType": { "type": "string", "description": "UDMI subType" },
    "transactionId": { "type": "string", "description": "Tracking identifier" },
    "publishTime": { "type": "string", "format": "date-time", "description": "Envelope wrapping timestamp" },
    "source": { "type": "string", "description": "Client session identifier" },
    "principal": { "type": "string", "description": "Session owner identity" },
    "nonce": { "type": "string", "description": "Unique message instance ID (8-digit hex)" },
    "payload": {
      "type": "object",
      "description": "UDMI message container",
      "properties": {
        "timestamp": { "type": "string", "format": "date-time", "description": "UDMI message generation time" },
        "version": { "type": "string", "description": "UDMI schema version" }
      },
      "required": ["timestamp", "version"]
    }
  },
  "required": ["payload", "nonce", "transactionId", "projectId", "publishTime", "source", "principal"]
}
```

### 10.2. Handshake State Payload
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "HandshakeStatePayload",
  "type": "object",
  "properties": {
    "version": { "type": "string" },
    "timestamp": { "type": "string", "format": "date-time" },
    "udmi": {
      "type": "object",
      "properties": {
        "setup": {
          "type": "object",
          "properties": {
            "functions_ver": { "type": "integer", "description": "Expected UDMI functions version" },
            "transaction_id": { "type": "string", "description": "Handshake transaction ID" },
            "msg_source": { "type": "string", "description": "Originating client ID" },
            "user": { "type": "string", "description": "Authenticated user ID" }
          },
          "required": ["functions_ver", "transaction_id"]
        }
      },
      "required": ["setup"]
    }
  },
  "required": ["version", "timestamp", "udmi"]
}
```

### 10.3. Handshake Config Payload
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "HandshakeConfigPayload",
  "type": "object",
  "properties": {
    "version": { "type": "string" },
    "timestamp": { "type": "string", "format": "date-time" },
    "udmi": {
      "type": "object",
      "properties": {
        "setup": {
          "type": "object",
          "properties": {
            "functions_min": { "type": "integer", "description": "Minimum supported functions version" },
            "functions_max": { "type": "integer", "description": "Maximum supported functions version" },
            "udmi_version": { "type": "string", "description": "System UDMI version" },
            "deviceRegistryId": { "type": "string", "description": "System-provided registry ID" }
          }
        },
        "reply": {
          "type": "object",
          "properties": {
            "functions_ver": { "type": "integer", "description": "Reflected functions version" },
            "transaction_id": { "type": "string", "description": "Reflected transaction ID" },
            "msg_source": { "type": "string", "description": "Reflected client ID" }
          },
          "required": ["transaction_id"]
        }
      },
      "required": ["setup", "reply"]
    }
  },
  "required": ["version", "timestamp", "udmi"]
}
```

### 10.4. Cloud Model Payload
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "CloudModelPayload",
  "type": "object",
  "properties": {
    "version": { "type": "string" },
    "timestamp": { "type": "string", "format": "date-time" },
    "cloud": {
      "type": "object",
      "properties": {
        "operation": {
          "type": "string",
          "enum": ["READ", "CREATE", "UPDATE", "DELETE", "BIND", "UNBIND"],
          "description": "Model operation type"
        },
        "registries": {
          "type": "object",
          "description": "Map of registry_id to device configurations",
          "patternProperties": {
            "^[a-zA-Z0-9_-]+$": {
              "type": "object",
              "properties": {
                "devices": {
                  "type": "object",
                  "patternProperties": {
                    "^[a-zA-Z0-9_-]+$": {
                      "type": "object",
                      "description": "Map of subsystem_id to subsystem state",
                      "patternProperties": {
                        "^[a-zA-Z0-9_-]+$": {
                          "type": "object",
                          "description": "Device subsystem state",
                          "properties": {
                            "target_version": { "type": "string" },
                            "current_version": { "type": "string" },
                            "status": { "type": "string" },
                            "lkg_version": { "type": "string" },
                            "make": { "type": "string" },
                            "model": { "type": "string" }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        },
        "detail": { "type": "object", "description": "Operation-specific parameters" }
      },
      "required": ["operation", "registries"]
    }
  },
  "required": ["version", "timestamp", "cloud"]
}
```

## 11. Command Line Interface (CLI)

### 11.1. Robustness
- **Unexpected Arguments:** All UUFI-compliant tools MUST NOT fail if they receive unexpected or unknown command-line arguments. They MUST ignore unknown arguments and proceed with execution.
- **Argument Order:** Positional arguments MUST follow the order defined in the tool's usage string.

## 12. Local Repository Structure (Standardized)

To ensure that tools from different implementations (e.g., a Trigger from Impl A and an Orchestrator from Impl B) can interoperate within the same local workspace, the following directory and file structures are standardized.

### 12.1. Blob Repository
Blobs MUST be stored in a directory structure following this pattern:
`{base_dir}/{make}/{model}/{subsystem}/{version}/`

Each version directory MUST contain:
- `bundle.bin`: The binary blob content.
- `sha256.txt`: A text file containing the hex-encoded SHA-256 hash of `bundle.bin`.

### 12.2. Model Repository
The cloud model, when stored as a local JSON file, MUST use the 3-level nesting defined in Section 10.4 (Registries -> Devices -> Subsystems).
