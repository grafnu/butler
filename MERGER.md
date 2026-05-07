# Instructions on how to run a merge integration test between two branches.

The goal is to test mulitple different implementations of the `butler` spec, as defined in `spec/`. There are (should be)
multiple working subdirectories, each one containing a different version of an implementation.

Each zip file in `impl/` constitutes a different implementation version. Those files should be extracted and then used for
testing.

* `cd impl; unzip {ID}.zip` should expand to the directory `impl/{ID}/`

The `venv` based off of `requirements.txt` needs to be setup for each subdirectory independently.

Execute the functional equivalent of `smokeit` except use the appropriate tools from different subdirectories. Two
versions are tested at a time, one primarily for `verifier` the other for `butler`.

Run `observe` twice, once from each  and capture the output to separate log files `impl/{ID}.log`.

If the smoke test fails, indicating that there is an incompatibility in the implementations, diagnose and analyse to
determine what the problem is, and recommend a change to the specs (in `spec/`) to remediate.

If the smoke test passes, analyze the generated log files to see if there is any other discrepancy or ambiguities
that should be addressed (and likewise recomment a change to `spec/`).

The actual implementation in `*/butler/` will be different and that's expected. (Same with `*/bin/`).

Any test files should be generated in directories covered by `.gitignore`. Do not clean up the test runs after
execution. At the end of the testing, there should not be any artifacts left that are visible by `git status`.

Run the setup and tests multiple times, exactly just enough iterations to satisfy the following constraints:
* Every branch is run at least twice, once as `butler` and the other as `observe`.
* The graph of connected components should be a connected graph.

The resulting impl/${ID}.log files should be commited to the repo and pushed as the results.

A summary the resuls should be put in `summary.txt` and committed to the repo.
