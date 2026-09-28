# Shared two-stage generation core

`generation_core.py` is the single maintained implementation. The historical
folder name is retained for callers and the frozen retrieval v1 interfaces.
`generation_runner.py` selects strict JSON. The sibling v3 entrypoint selects
whole-response JSON-code-fence normalization. Neither policy repairs JSON,
changes semantic validation, or adds generation retries.

Prompts, schemas, transport and default resources live here once. The four
retrieval v1 compatibility files retain their original bytes; current campaigns
use the separately registered shared retrieval runtime.

New runs record `shared_two_stage_core_v1`, the parsing policy and current source
hashes. Pre-consolidation source/config identities are recorded under
`Support/legacy/frozen_replay/two_stage_pre_consolidation/source_lineage.json`;
old reports and external releases are unchanged.
