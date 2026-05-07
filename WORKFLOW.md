# Updating workflow

This document describes the overall workflow for updating the _butler_ spec
across multiple implementations and resolution. The input is a proposed change
to the spec, and the output is a release version.

## Repository structure.

There are three main kinds of repos involvedin the overall workflow:

* **`spec`**: The root of the tree, where the core specifications are managed.
* **`impl-X`**: A number of independent implementations of the spec.
* **`rcnsl`**: The point of reoncilliation (aka `rcnsl`) where multiple implementations are cross-tested.

The ultimate output of the workflow is an implementation in the `rcnsl` branch, which is
a promoted version of one of the versions from an `impl` branch, which source their
behavorial specifications from the `spec` branch. Additionally, if there are relevant
changes to the spec from the `rcnsl` branch, they are migrated back into `spec` through
a metered reviewed process.

## Workflow stages.

The process involves a number of regimented steps for progressing a specification through the various stages.

The input to this workflow is a proposed _feature_, as a change to the spec, and the output is a promoted _relase_.

* _feature_ --> `spec`: Features are imported into the spec branch as the starting point for an unpdate workflow.
  * Merge request into `spec`
* `spec` --> `impl-X`: The spec is merged into any number of `impl-X` branches and then agentically instantiated.
  * Automatic merge.
* `impl-A` + `impl-B` + ... --> `rcnsl`: Mulitple `impl-X` branches are copied (not merged) into the `recnsl` branch.
  * Cloned (not merged) versions of multiple implementations into a single workspace.
* `rcnsl` --> `spec`: If necessary, recommended spec changes from `rcnsl` are extracted and merged back into `spec`.
  * PR geneated against the `spec` branch (if necessary).
* `impl-X` --> _release_: As needed, a (one of several) implementation branch is promoted (exported) to an active state.
  * Tagged version as the explicit release (if testing complete).
