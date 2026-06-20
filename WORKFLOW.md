# Developer Workflow and Environment Setup

This document provides a comprehensive guide for setting up the development environment and executing the spec-driven development, integration, and verification lifecycle for the **Butler Managed Update System**.

---

## 1. Environment Setup

### Prerequisites
To develop, build, and test the Butler system, you need the following pre-installed on your system:
- **Python 3.8+** (Python 3.13 is recommended and utilized in current virtual environments)
- **Java 11+**
- **Mosquitto** broker and clients (`mosquitto-clients`)
- **expect** and development packages (Linux packages)

### Step 1: Cloned UDMI Directory Setup
The `udmi` directory MUST exist inside the `impl/` directory (at `impl/udmi/` relative to the workspace root).

- **How the `impl/` Directory and `impl/udmi` Clone are Created on a Clean Slate:**
  Since a clean checkout of the repository starts with no `impl/` directory, you must initialize it manually by cloning the corresponding `udmi` remote repository:
  1. **Determine the Remote URL:** Resolve the remote repository URL for the `udmi` clone by taking the current project's remote origin URL (e.g., query with `git config --get remote.origin.url`) and replacing the repository name (`butler` or `butler.git`) with `udmi` (or `udmi.git`). For example, if the origin is `git@github.com:XXXXX/butler.git`, the `udmi` remote repository is `git@github.com:XXXXX/udmi.git`.
  2. **Create and Clone:** Create the `impl/` directory and clone the `udmi` remote repository into `impl/udmi/`:
     ```bash
     mkdir -p impl
     git clone <udmi_remote_url> impl/udmi
     ```
  3. **Align Branches (Optional):** If working on a specific feature or development branch, checkout the corresponding branch in the `impl/udmi/` clone to maintain operational parity.

- **Sandbox Isolation & Repository Constraints:** To enable running the system in sandbox mode (`gemini -s`) and in complete isolation from other code on the system, the `udmi` directory MUST be a local git clone of the remote repository inside the `impl/` workspace.
- **Keep Up to Date:** The `impl/udmi/` clone must be kept up to date with the remote repository (e.g. by executing `git pull` in that directory). All references to other components or external libraries must go through a remote git repository cloned locally.
- **Shared Resource & Immutability Constraint:** This is an immutable, read-only resource. Never modify files inside `impl/udmi` directly or clone site models there to ensure sandbox predictability and prevent race conditions.
- Ensure it is up to date:
  ```bash
  cd impl/udmi
  git pull
  ```

### Step 2: Install System Dependencies
Install necessary system packages (such as Mosquitto, mosquitto-clients, and expect) by delegating to the UDMI setup utility (Linux-only, macOS developers should use Homebrew):
```bash
impl/udmi/bin/setup_base
```
*Note on Privileges:* Running `impl/udmi/bin/setup_base` is **optional** if you already have the required dependencies pre-installed on your system.

### Spec-Driven Code Generation (REBUILD.md)
On the clean `main` branch or when bootstrapping a brand-new implementation, the implementation directories `butler/` (core Python logic) and `bin/` (operational executables) may not be pre-populated or checked into the repository.
- **When to use `REBUILD.md`:** If you are setting up a new implementation from scratch, or need to perform a completely clean, spec-driven rebuild of the codebase to align with changes in `spec/`, you must invoke the agentic build pipeline using `gemini -s -p @REBUILD.md`.
- **Process Overview:** The agentic build pipeline completely deletes existing `butler/` and `bin/` directories, parses the formal architectural and protocol specifications in `spec/` (e.g., `butler.md`, `blobstore.md`, `update.md`), and automatically generates/bootstraps compliant source code and wrapper scripts.

---

## 2. The Spec-Driven Agentic Development Workflow

The Butler Managed Update System follows a **spec-driven agentic lifecycle** that maintains alignment across multiple disparate implementations (`impl_<id>`), reconciling spec updates back into the `main` branch.

Below is the workflow sequence detailing how to update specifications, update implementation code, run cross-implementation tests, and merge specification updates back to the `main` branch.

```
                  +-------------------------+
                  |    impl_<id> Branches   | <-----------------+
                  |       @UPDATE.md        |                   |
                  +------------+------------+                   |
                               |                                |
                               v                                |
                  +-------------------------+                   |
                  |      merger Branch      |                   |
                  |        @MERGER.md       |                   |
                  +------------+------------+                   |
                               |                                |
                               v                                |
                  +-------------------------+                   |
                  |       main Branch       | ------------------+
                  |   manually update spec  |
                  +-------------------------+
```

### Phase 1: Implementation Update and Spec Auditing (`impl_<id>` Branches)

When core specifications under `spec/` in `main` are updated, those changes must be pulled into each implementation branch (`impl_<id>`) and the implementation code updated to comply. This step is automated via the Gemini developer agent using the `UPDATE.md` instructions.

For each implementation branch `impl_<id>` (where `<id>` is the implementation identifier, e.g., `A`, `B`, `C`, or `D`), execute the following in separate terminal windows:

1. **Update and Audit the Implementation:**
   ```bash
   cd impl/<id>
   gemini -s -p @UPDATE.md
   ```
   *Action:* Gemini merges `origin/main` into the current branch, parses specifications, and delegates environment preparation, dependency management, port validation, and local smoke testing (`bin/smokeit`) on isolated, branch-mapped MQTT/testing ports directly to the implementation-specific automated scripts and setup skills.

2. **Verify Commit Histories:**
   After the automated agent processes complete, verify the generated commit messages and ensure the repository remains in a clean, compliant state:
   ```bash
   git log
   ```

### Phase 2: Cross-Implementation Interoperability Testing (`merger` Branch)

Once all implementations have updated and verified themselves against the updated specification, reconcile them on the `merger` branch to ensure they are fully interoperable. This is automated via the Gemini developer agent using the `MERGER.md` instructions.

Switch to the `merger` directory:
```bash
cd merger/
gemini -s -p @MERGER.md
```
*Action:* Under the hood, Gemini runs a complete cross-implementation matrix:
- Concurrently fetches and clones/syncs all remote `impl_*` branches into `merger/impl/`.
- Delegates the creation and management of isolated python virtual environments directly to each implementation's own setup skills.
- Generates and executes the complete `N * (N - 1)` cross-implementation test runs (running each implementation as `butler` against every other implementation as `verifier`, and vice-versa) on dynamically allocated local TCP ports to avoid collisions.
- Collects test logs into `impl/{ID}.log` and summarizes the results in `impl/test_summary.txt`.
- If interoperability issues or ambiguities are uncovered, the specifications in `spec/` are dynamically adjusted and updated to achieve full spec compliance.
- Finally, the updated specs are committed and pushed to `origin/merger`.

### Phase 3: Merging Verified Specs Back to `main`

Once the specifications have been refined and proven to be robust through interoperability cross-testing on `merger`, the verified changes are checked out back into the `main` branch's `spec/` folder.

In the `main` branch terminal, run:
```bash
cd ..
git fetch
git checkout origin/merger -- spec/
```
*Action:* This command fetches the latest remote changes and checks out the validated `spec/` files from the `merger` branch directly into the local `main` branch workspace, completing the development loop. These changes should be reviewed to make sure they're sane.

---

## 3. Developer Commands Reference Sheet

| Phase / Command | Target Directory / Branch | Purpose / Description |
| :--- | :--- | :--- |
| `gemini -s -p @UPDATE.md` | `impl_<id>` | Merges main, audits specs, adjusts implementation logic, and runs local smoke tests (delegating python and local workspace environment setup to implementation-specific setup skills). |
| `gemini -s -p @MERGER.md` | `merger` | Executes full concurrent cross-testing of all implementation pairs, refines core specs, and pushes verified specification changes. |
| `git checkout origin/merger -- spec/` | `main` | Imports the verified and refined specifications from the integration branch into the main branch. |
| `gemini -s -p @REBUILD.md` | `impl_<id>` / New setup | Bootstraps a brand-new implementation from scratch or performs a clean spec-driven rebuild of `butler/` and `bin/` from files in `spec/`. |
| `bin/setup mqtt://localhost:<port>/` | Local workspace | Boots up local broker infrastructure on the specified isolated port and configures the UUFI connection spec. |
| `bin/smokeit mqtt://localhost:<port>/` | Local workspace | Runs the interactive integration smoke test (Orchestrator, Verifier, and simulated DUT) on the specified port. |
