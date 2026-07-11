# UUFI Foundational Specification and Infrastructure Audit (uufi.md)

This document outlines the audit findings comparing `spec/butler.md` against the foundational UUFI specification (`impl/udmi/docs/specs/uufi.md`), and specifies missing instructions in `uufi.md` required for base infrastructure setup and client testing.

---

## 1. Specification Relegation Summary

All base infrastructure lifecycle management—broker startup, gateway execution, DUT simulation (`start_dut`), database triggers (`site_trigger`), and database registration (`registrar`)—has been relegated directly to [impl/udmi/docs/specs/uufi.md Section 9](file:///home/peringknife/vibrant/main/impl/udmi/docs/specs/uufi.md#L215).

`spec/butler.md` has been refactored to focus **exclusively** on starting and verifying Butler:
1. **Butler Execution:** `bin/butler <conn_spec>`
2. **Verifier Execution:** `bin/verifier <conn_spec>`
3. **Smoke Testing & State Verification:** `bin/smokeit <conn_spec>`

---

## 2. Missing Instructions Identified in `uufi.md` Section 9

To make `uufi.md` completely self-contained for testing any client or orchestrator against the base system, the following explicit CLI parameters must be documented in `uufi.md` Section 9:

### A. Infrastructure Startup (`bin/start_local`)
- **Missing Specification:** Section 9.1 documents `bin/start_local` without positional arguments.
- **Required Instruction:** Must explicitly mandate format:
  ```bash
  bin/start_local [site_model_dir] //mqtt/localhost:$port
  ```
  *Rationale:* Passing `//mqtt/localhost:$port` satisfies parameter expansions (`${project_spec%/*}` and `${project_spec#//}`) inside `start_local`, permitting custom isolated port execution without modifying UDMI binaries.

### B. Pubber DUT Simulation (`bin/start_dut`)
- **Missing Specification:** Section 9.2 describes launching Pubber DUT but omits exact argument ordering and connection string syntax.
- **Required Instruction:** Must explicitly mandate format:
  ```bash
  bin/start_dut <site_model_dir> //mqtt/localhost:$port [device_id] [serial_no]
  ```
  *Rationale:* The second positional argument MUST begin with `//` (e.g. `//mqtt/localhost:$port`). Omitting `//` or passing schemes like `mqtts://` causes `reset_config` to select `iot_provider="jwt"`, resulting in a fatal runtime error (`Unknown iotProvider jwt`).

### C. Database & Tagabase Registration (`bin/registrar`)
- **Missing Specification:** Section 9 omits the database synchronization step prior to test execution.
- **Required Instruction:** Must document running the standard registrar tool in full online mode:
  ```bash
  bin/registrar <site_model_dir> //mqtt/localhost:$port
  ```
  *Rationale:* Generates `metadata_norm.json`, populates `generated_config.json`, and initializes tagabase entries for simulated devices.

### D. Site Model Database Mutations (`bin/site_trigger`)
- **Missing Specification:** Section 9.5 lists `bin/site_trigger` without enforcing `//mqtt/localhost:$port` syntax for custom ports.
- **Required Instruction:** Must mandate format:
  ```bash
  bin/site_trigger update <site_path> <device_id> <blob_id> <version> //mqtt/localhost:$port
  ```

### E. Hermetic Non-Privileged Teardown (`--stop` / PID Teardown)
- **Missing Specification:** Section 9 omits instructions for teardown in isolated environments.
- **Required Instruction:** Document PID file resolution (`out/mosquitto.pid`, `out/etcd.pid`) and targeted `SIGTERM`/`SIGKILL` termination, prohibiting sweeping `pkill` commands.
