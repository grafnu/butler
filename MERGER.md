# Instructions on how to run a merge integration test between two branches.

The goal is to test mulitple different implementations of the `butler` spec, as defined in `spec/`. There are (should be)
multiple working subdirectories, each one containing a different version of an implementation.

The branches to test are:
* verifier
* controller
* jules

The subdirectories should be a literal `git clone` of this repository, switched to the relevant branch.
Those branches hold the different implementations that should be tested. The clones should go in `opt_{branch}`.

The `venv` based off of `requirements.txt` needs to be setup for each subdirectory independently.

Execute the functional equivalent of `smokeit` except use the appropriate tools from the different subdirectories.

Run `observe` twice, once from each branch, and capture the output to separate log files `opt_{branch}.log` at the
top level (so as a peer to `opt_{branch}`).

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
