# Fenced JSON compatibility entrypoint

The implementation, prompts, contracts, transport and resources are shared with
`../api3_anthropic_runner_v2/generation_core.py`. This directory only selects
`single_json_code_fence_v1`; the v2 entrypoint selects `strict_json`.
Both load private module instances so their parsing/retry settings cannot leak
between concurrent campaigns. Raw emissions and normalization audits are retained.

The original two complete bundles are recoverable from the Git commit recorded in
`Support/legacy/frozen_replay/two_stage_pre_consolidation/source_lineage.json`.
New runs identify `shared_two_stage_core_v1`, not the old implementation bytes.
