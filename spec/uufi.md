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
- **Filtering:** Subscriptions should filter for messages where the `principal` attribute matches the local identity or is absent.
- **Constraint:** `:port` is prohibited.

#### MQTT (`mqtt://`)
- **Host/Port:** Standard network mapping.
- **Topic Structure:** `/uufi/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}`
- **Topic Isolation:** The `principal` identifier MUST be included in the JSON envelope.
- **Cloud Model Service:**
  - **Discovery:** Clients publish a `query/cloud` message to `/uufi/c/query/cloud`.
  - **Response:** The **System** (and ONLY the System) MUST respond by publishing the requested model information to `/uufi/c/config/cloud`.
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
- **Addressing:** Envelope `principal` MUST match Client's identity.

**Retries:** The Client SHOULD periodically republish the Step 1 state message (e.g., every 5 seconds) if a valid Step 2 confirmation has not been received, until the 60-second timeout.

**Activation:** The Client is **Active** when `udmi.reply.transaction_id` matches the original `state.udmi.setup.transaction_id`.

### Registry ID Discovery
- **Default:** `default`
- **Discovery:** System may provide `{registryId}` in `config.udmi` during handshake.
- **Priority:** If a Client has a pre-configured registry ID (e.g., via command-line arguments), it SHOULD prioritize it over the one provided by the System during handshake to ensure identity consistency in restricted environments.

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
- **Registries:** Map of `{registry_id}` to a map of `{device_id}` to a map of `{subsystem_id}` to subsystem state.
- **Detail:** Optional parameters.

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
| Update Config | `config` | `blobset` | Publish |
| Update State | `state` | `blobset` | Receive |

### 7.2 MQTT QoS
- **Requirement:** QoS 1 (At Least Once) for all state and configuration messages.

### 7.1. Handshake (PubSub)

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
  "udmi": {
    "setup": {
      "functions_ver": 9,
      "transaction_id": "UUFI:sess123:001",
      "msg_source": "client-id"
    }
  }
}
```

### 7.2. Pointset Config (MQTT)

**Topic:** `/uufi/r/reg-1/d/dev-1/c/config/pointset`

**Payload:**
```json
{
  "transactionId": "UUFI:sess123:002",
  "principal": "client-id",
  "payload": {
    "version": "1.5.2",
    "timestamp": "2026-04-29T10:05:00Z",
    "pointset": {
      "points": {
        "temp": { "set_value": 22.5 }
      }
    }
  }
}
```

## 8. Reliability

### MQTT QoS
- **Requirement:** QoS 1 (At Least Once) for all state and configuration messages.

### Idempotency
- **Nonce:** SHOULD use a unique message instance ID (8-digit hex nonce) for identification.
- **Deduplication:** Track `nonce` values for 5 minutes. If `nonce` is not present, implementations MAY use `transactionId` for deduplication, EXCEPT for messages in the `udmi` subfolder (e.g., handshakes), which MUST NOT be deduplicated by `transactionId` to allow for protocol retries.

## 9. Compliance

### 9.1. Payload Structure
- **Nesting:** The `payload` object MUST contain exactly one top-level key matching the `subFolder` name.
- **Subsystem Nesting:** For `blobset` config and state payloads, data MUST be nested within a subsystem-id key (e.g., `main`) to support multi-subsystem devices. For maximum compatibility with UDMI standards, implementations MUST include a `blobs` wrapper key within the `blobset` object, containing the subsystem-id keys. Implementations MUST handle both nested (with the `blobs` wrapper) and unnested (flat) payloads for backward compatibility and robust interoperability.
- **Mandatory Fields:** `timestamp` and `version` MUST be at the root of the `payload` object.

### 9.2. Timestamp Format
...
## 11. Local Repository Structure (Standardized)

To ensure that tools from different implementations (e.g., a Trigger from Impl A and an Orchestrator from Impl B) can interoperate within the same local workspace, the following directory and file structures are standardized.

### 11.1. Blob Repository
Blobs MUST be stored in a directory structure following this pattern:
`{base_dir}/{make}/{model}/{subsystem}/{version}/`

Each version directory MUST contain:
- `bundle.bin`: The binary blob content.
- `sha256.txt`: A text file containing the hex-encoded SHA-256 hash of `bundle.bin`.

### 11.2. Model Repository
The cloud model, when stored as a local JSON file, MUST use the 3-level nesting defined in Section 10.4 (Registries -> Devices -> Subsystems).
- **Format:** RFC 3339 minimal precision (e.g., `2026-05-01T22:32:17Z`).
- **Timezone:** UTC required.
- **Precision:** No microseconds.

### 9.3. Redundancy Rule
- **MQTT:** Implementations MUST NOT include redundant fields in the MQTT JSON envelope if those fields are already encoded in the topic path (`subType`, `subFolder`, and if present, `deviceRegistryId`, `deviceId`).
- **Validation:** Implementations MUST reject messages where envelope fields duplicate topic-encoded data. However, fields NOT present in the topic path (e.g., `deviceRegistryId` in a registry-less topic) MUST NOT be rejected if present in the envelope.

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
  "required": ["payload"]
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
            "udmi_version": { "type": "string", "description": "System UDMI version" }
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
- **Unexpected Arguments:** All UUFI-compliant tools MUST NOT fail if they receive unexpected or unknown command-line arguments. They SHOULD ignore unknown arguments and proceed with execution.
- **Argument Order:** Positional arguments SHOULD follow the order defined in the tool's usage string.
```

