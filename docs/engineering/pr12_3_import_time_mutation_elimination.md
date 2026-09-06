# PR12.3 — Import-Time Mutation Elimination

PR12.3 continues the post-0.3 architecture cleanup by removing package-import-time class mutation from the public Python runtime topology while preserving already-reviewed product behavior.

## Static ownership

The public runtime classes are now assembled where they are defined:

- `client.py` composes `ChatGPTWebClient` over the frozen historical client core;
- `browserless_request_transport.py` composes browserless guards over its frozen core;
- `browser_owned_product_transport.py` composes proven web-search/rich-input capability declarations over its frozen core;
- `product_runtime.py` composes structured-observation behavior and the first-class submission/liveness methods over a frozen runtime core.

The package `__init__.py` is an export surface only. Importing or reloading the package no longer assigns methods or constants onto these runtime classes.

## Frozen-core rule

Historical cores are not grandfathered merely because they were moved. `tools/engineering_quality_gate.py` exempts a relocated core from the touched-file Ruff baseline only when its bytes exactly equal the corresponding file from the PR base commit.

Any edit to a frozen core therefore removes the exemption and subjects that file to the current quality policy.

## Observation gate

`product_runtime_observation_gate.py` is now side-effect-free at import/composition time. It no longer installs activity precedence, capability declarations, submission methods, or UI-liveness methods as an import consequence.

Canonical source/citation observation still uses the historical compatibility installer when an observed execution actually runs. That is call-time mutation rather than import-time mutation and is intentionally left for a later cleanup so PR12.3 does not mix topology migration with canonical-observation behavior changes.

## Preserved contracts

PR12.3 does not change:

- Browser Authority semantics;
- canonical-finality authority;
- no automatic replay after an ambiguous write;
- Temporary Chat semantics;
- rich-input authority;
- browserless support tier;
- observation/approval separation;
- public capability states.

The intent is topology-only: make ownership readable and deterministic without rewriting proven runtime behavior.
