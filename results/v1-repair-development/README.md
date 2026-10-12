# Event-guided anchor repair: development evidence

The repair mechanism runs end to end, but this diagnostic does **not** show a
generation benefit. The twelve-clutter request remains unverified with G1, G2,
G3 and the repair-disabled ablation. A six-clutter request is accepted without
using repair. These are five method requests on one development seed, not
held-out performance estimates or policy evaluations.

## Common conditions

All runs use native complete `stack_blocks`, `arx_x5`, three unchanged task cubes,
the same three precomputed Infinigen assets and the shared planner/validator.
Requests preserve clutter count, region, clearance and paired spacing.
`block_0` may occupy its original XY position or one of four axis offsets of
6 cm; `block_1` and `block_2` have one allowed initial position each.
All methods start at the same allowed original layout. This additional task
freedom makes these requests different from the earlier fixed-position study.

G1 can resample from the same position options on layout retries. G2 retains
its first complete reference. G3 can make one event-guided anchor update after
failed scene completion, then acquires a new complete physical reference.
G3 with `--disable-repair` retains its first reference.
See the [method and limitations](../../docs/interaction-repair.md),
[request configuration](../../specs/v1-repair-development.json) and
[commands](../../docs/development.md).

Each request has a 240-second limit, at most four layouts and 4,000 placement
proposals, with 1,000 proposals per layout and 128 per cluster. Each execution
has a 20,000-physics-step limit. The clock includes fresh state/reference
acquisition, startup/shutdown, generation, execution, validation and export.
Precomputed asset creation is excluded and its historical cost is unknown.

## All method requests

The complete request denominator is in [request-rows.json](request-rows.json).
All twelve-object runs reach the layout limit; none exports a partial layout.

- **G3, twelve objects:** 138.31 seconds and 4,000 proposals. The first layout
  attempt fails, triggering a 6 cm negative-Y update of `block_0`. The new
  reference completes both transfers and return in 5,713 physics steps. Remaining
  layout attempts still fail. Best partial counts per attempt are 8, 8, 10, 8.
- **G1, twelve objects:** 79.18 seconds and 3,062 proposals. The first complete
  static layout reaches execution but fails at descent. The next three retries
  change the base-block position and each produce only ten-object partial layouts.
- **G2, twelve objects:** 67.31 seconds and 4,000 proposals. No complete layout
  is found. Best partial counts are 8, 8, 10, 8.
- **G3 with repair disabled, twelve objects:** 67.43 seconds and 4,000 proposals.
  Its reference controls, measured trajectory and all non-timing generation
  fields match G2 in this request. See [ablation checks](ablation-equivalence.json).
- **G3, six objects:** 116.58 seconds and 307 proposals. The first candidate
  completes the full task, passes all 25 development checks and is exported.
  Repair is never triggered. The portable bundle's 18 file hashes were checked
  through the episode loader; this is not an additional physical reload run.

Equal partial counts do not imply equal layouts. These costs do not establish
a general efficiency ranking. Controller failure means unverified with the
declared controller and budget, not physically impossible.

## Repair and independent checks

The first twelve-object attempt attributes 183 rejected cluster proposals to
reference occupancy. All 183 involve the active arm; 39 also involve the passive
base block and 140 involve either moved task block. These categories overlap.
The support/event dependency assigns 103 proposals to the adjustable base
anchor. The directional heuristic selects negative Y from the allowed choices;
the full [ranking](g3-1200-12/repair-ranking-00.json) is retained.

The changed reference starts with identical robot poses, uses a different
control sequence, and places both moved blocks over the changed base location.
A separate [minimal-scene audit](reference-validation.json) passes all 25 checks.
This audit has **zero clutter** and is a check of the changed reference only;
it does not satisfy or replace the failed twelve-object request. It reuses the
recorded execution and is outside the request's timed generation stages.

A [negative control](position-negative-control.json) audits that same execution
under a request allowing only the original base position. It fails exactly the
task-position check. The acceptance gate therefore rejects an otherwise completed
operation outside the request's allowed positions.

## Interpretation and provenance

The repair demonstrates support propagation and physical replanning, but this
one request provides no evidence that the heuristic improves completion.
The six-object result supports the existing generation/validation/export chain;
it does not establish the added repair's value. The remaining failures include
packing limitations as well as motion occupancy, so this result does not justify
adding more trajectory machinery to obtain favorable numbers.

This completes the first minimal repair implementation. Broader development
calibration, other agreed tasks, the full-task language frontend, held-out
experiments and the two real policies remain unfinished. Formal claims must
follow those results; no repair advantage or continuous collision guarantee is
claimed here.

Per-request folders contain sanitized run records, all attempt summaries,
state/reference acquisition summaries and hashes of local raw artifacts. Raw
logs, trajectories, contacts and source snapshots are retained under
`outputs/v1-repair-development/`. Public run records omit absolute command paths
and process IDs. The recorded Git revision is the base revision; per-file hashes
identify the implementation executed. All five requests used the same source
version. No upstream checkout or native asset was modified.
