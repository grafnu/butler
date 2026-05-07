# Instructions on how to run a merge integration test between two implementations.

The goal is to test multiple different implementations of the `butler` spec, as defined in `spec/`. There are (should be)
multiple working subdirectories, each one containing a different version of an implementation.

Each zip file in `impl/` constitutes a different implementation version. Those files should be extracted and then used for
testing. Each implementation ID (e.g., A, B, C from A.zip, B.zip, C.zip) is referred to as a "branch" in these instructions.

* `cd impl; unzip {ID}.zip` should expand to the directory `impl/{ID}/`

The `venv` based off of `requirements.txt` needs to be setup for each subdirectory independently.

Execute the functional equivalent of `smokeit` (found in `impl/{ID}/bin/smokeit`) except use the appropriate tools from different subdirectories. Two
versions are tested at a time, one primarily for `verifier` the other for `butler`. Both implementations should use the **same** connectivity specification (as defined in `AGENTS.md`) to ensure they can communicate.

Run `observe` (found in `impl/{ID}/bin/observe`) twice, once from each implementation, and capture the output to separate log files `impl/{ID}.log`.

If the smoke test fails, indicating that there is an incompatibility in the implementations, diagnose and analyse to
determine what the problem is, and recommend a change to the specs (in `spec/`) to remediate.

If the smoke test passes, analyze the generated log files to see if there is any other discrepancy or ambiguities
that should be addressed (and likewise recomment a change to `spec/`).

The actual implementation in `*/butler/` will be different and that's expected. (Same with `*/bin/`).

Any test files should be generated in directories covered by `.gitignore`. Do not clean up the test runs after
execution. At the end of the testing, there should not be any artifacts left that are visible by `git status`.

Run the setup and tests multiple times, exactly just enough iterations to satisfy the following constraints:
* Every branch (ID) is run at least twice, once as `butler` and the other as `observe`.
* The graph of connected components should be a connected graph (e.g., if you have A, B, and C, you might test A-B and B-C).

The resulting `impl/${ID}.log` files and `summary.txt` should be committed to the repo and pushed as the results automatically upon completion of the test suite.
