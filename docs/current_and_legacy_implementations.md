# Current implementations and legacy contracts

One maintained implementation owns each behavior family. A versioned schema,
scoring profile, geometry mode, or compatibility import is not another evaluator.

| Family | Maintained implementation | Retained differences |
|---|---|---|
| Scene evaluation | `src/benchmark/api/evaluation.py` and `evaluator/` | Canonical profile v1 has three L3 metrics; v2 has five. Old scoring weights remain data in `scoring_profiles.py`. |
| Model evaluation runs | `scripts/run_uniform_model_evaluation.py` | Open-space, rectangular and polygon inputs use geometry/input adapters. The wrapper fixes the Model protocol and records source identity. |
| Registered baselines | `scripts/run_floorplan_evaluator.py` | Explicitly selects the registry's historical source/run for each mode. This is separate from choosing the current package implementation. |
| Historical single-room evaluator | `evaluator_snapshots/single_room_sceneweaver_20260909_v1/` | Frozen acquisition, adjudication and report behavior; still required by the registered single-room baseline. |
| VLM judge/provider adapters | `visual_judge/adapters/provider_judge.py`, `provider_camera.py`, `provider_renderer.py`, `openai_camera.py` | Old `legacy_*` imports alias these modules. They contain no second implementation. Wire IDs and old class names remain compatible. |
| Two-stage generation | `tools/api3_anthropic_runner_v2/generation_core.py` | One implementation with strict JSON and single whole-response JSON-fence policies. The historical v2 folder remains a resource/import location. |
| Generation entrypoints | `tools/api3_anthropic_runner_v2/generation_runner.py` and sibling v3 entrypoint | Small policy-specific loaders with isolated module globals. v3 reuses v2 helpers and resources; it no longer ships a second full bundle. |
| Dedicated model launchers | `scene_generation/campaign/` plus compatibility launchers | Fresh-case semantic retries and timeout-ambiguity recovery remain distinct from ordinary transport retries. Do not substitute one for another. |
| Evidence resolution | `visual_judge/evidence_resolution.py` and `evidence_gap_v2.py` | Explicit adaptive/fallback policies retain different failure and terminal semantics; modules share mechanisms and do not represent complete duplicate evaluators. |
| Game track | `game_scene/` and `metric_profile_game_canonical_v1.yaml` | Collision, single-floor navigability and Style differ from furnished-room evaluation. Old Game profile remains replay material. |
| Legend | `src/benchmark/legend/` | Old VLM-as-Judge workflow, excluded from the wheel. Its v0 function is a compatibility alias, not a separately maintained method. |
| Nonrect execution tools | `scripts/nonrect_hardening_v1/`, `scripts/nonrect_fast_v1/` | Different campaign ownership, preparation, resource and retry policies; preserved with pinned releases. |

## Generation provenance

The consolidated implementation records `shared_two_stage_core_v1`, its JSON
policy and actual source hashes. New campaign workflow IDs explicitly identify
strict, fenced or multi-room shared-core execution. Campaign inputs, prompts,
model routes, retry limits and scoring settings are unchanged.

The transport-facing `RUNNER_VERSION=2.0.0` remains a compatibility value used in
request headers; it does not claim byte-identical code. Implementation identity
comes from the revision and source manifest. Old run/resume manifests have a
different source identity and must not be silently resumed using this checkout.

Original source hashes and contract contents are recorded in
`Support/legacy/frozen_replay/two_stage_pre_consolidation/source_lineage.json`,
including the Git commit from which the exact previous source can be recovered.
No historical report, local frozen release, or baseline registry was rewritten.
The frozen retrieval-v1 compatibility files retain their original bytes.

## Scope of cleanup

Removed duplicate v3 helpers, prompts, schemas, resources and launcher copies;
kept two small entrypoints so existing imports choose the same parsing policy.
Evaluator provider implementations were moved to responsibility-based names,
with old import aliases preserved. Files pinned by the historical E0 probe
contract retain their original bytes and use those compatibility aliases.

A pre-existing E0 lifecycle fixture/hash mismatch is tracked separately. Its
record was not rehashed to conceal the discrepancy. Unrelated local worktrees,
branches, experiment data and uncommitted edits are outside this cleanup.
