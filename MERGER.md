# Instructions on how to run a merge integration test between two branches.

The goal is to test two different implementations of the `butler` spec, as defined in `spec/`. There are (should be)
two working subdirectories:

* `opt_a`: Is the `controller` and should be used to run `bin/butler` and related program.
* `opt_b`: Is the `verifier` and should be used to run `bin/verifier` and related program.

The subdirectories should be a literal `git clone` of this repository, switched to the `controller` and `verifier`
branches. Those branches hold the two different implementations that should be tested.

The `venv` based off of `requirements.txt` needs to be setup for both subdirectories (it's not stored in git).

Execute the functional equivalent of `smokeit` except use the appropriate tools from the different subdirectories.

Run `observe` twice, once from each branch, and capture the output to two separate log files `opt_a.log` and `opt_b.log`.

If the smoke test fails, indicating that there is an incompatibility in the implementations, diagnose and analyse to
determine what the problem is, and recommend a change to the specs (in `spec/`) to remediate.

If the smoke test passes, analyze the generated log files to see if there is any other discrepancy or ambiguities
that should be addressed (and likewise recomment a change to the specs).

The actual implementation in `opt_a/butler` and `opt_b/butler` will be different and that's expected. (Same with `*/bin/`).

Any test files should be generated in directories covered by `.gitignore`. Do not clean up the test runs after
execution. At the end of the testing, there should not be any artifacts left that are visible by `git status`,
and the `opt_a` and `opt_b` directories (and corresponding logs) should remain.

If the BUTLER_MERGER_SPEC env variable is defined, it should use that as the connectivity specificaiton for all tools.
It would conform to the `uufi.md` spec as defined. Otherwise, the tests should use `mqtt://mergetest@localhost/` as
the specification.
