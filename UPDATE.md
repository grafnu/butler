To update a build:

This should NOT be performed on the `main` or `gemerger` branch.
This sequence is for implementation branches only.
Only execute this if the `butler` directory already exists (and has code in it).

Fetch from `origin` remote.
Merge `origin/main` into this local branch.
Update the code to comply with `spec/`, focusing on any recent changes.
Test, commit, and push the changes for review.

Do not ask how to complete the task, and do not check if you should proceed.
Do not ask for clarification on any missing elements. If necessary,
add a "ASSUMED:" tag in the spec file where it was ambigous and you
needed to make a guess at the right thing to do.
Your job is to figure everything out yourself and then generate a commit
or PR that encapsulates the end result. Don't ask for permission or
if anything is acceptable.
