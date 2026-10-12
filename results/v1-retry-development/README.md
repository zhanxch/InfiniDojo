# Bounded generation and validation: development evidence

These are implementation checks on one development seed (1200), not a frozen
benchmark or evidence of a population-level advantage. The paper's primary
claim remains configurable generation of runnable, physically validated
evaluation instances. These runs check that requests can reach validation and
episode export, with failed attempts and costs retained.

## Conditions

The native `stack_blocks` task uses the original three cubes, both `arx_x5`
arms and the unchanged complete-task predicate. Requests add six or twelve
Infinigen cans inside a fixed region, with fixed clearance and paired spacing.
The three precomputed `CanFactory` assets have different dimensions and circular
or rectangular cross-sections. Both methods select the same asset identities
for each request and keep them fixed across layout retries.

- G1 captures a fresh reset state, samples a static layout, then attempts the
  full task using the shared scene-aware planner. Each retry samples a new layout.
- G2 acquires a fresh complete reference, reserves the full observed motion,
  samples surrounding clutter and physically replays that reference.
- Both use the same independent development validator and export only after
  all checks pass. G3 is not implemented in these runs.

Each method request allows 240 seconds, four layouts, 4,000 placement proposals
total, 1,000 proposals per layout and 128 proposals per cluster, with at most
20,000 physics steps per execution. The timer includes reset/reference
acquisition, process startup/shutdown, generation, planning, execution,
validation and export. Precomputed asset creation is excluded and its cost is
recorded as unknown, not zero. These are not complete cold-start cost figures.
See the [development manifest](../../specs/v1-retry-development.json) and
[reproduction commands](../../docs/development.md).

## Observed requests

All four planned method requests are included in [request-rows.json](request-rows.json).

- **G1, six clutter objects:** four static layouts found; none verified within
  the layout limit. Three executions failed at placement and one at approach.
  Total: 138.76 seconds and 82 placement proposals.
- **G2, six clutter objects:** the first layout passed all 24 development checks
  and was exported as a portable episode. Total: 121.47 seconds and 307 proposals,
  including a newly acquired complete reference. Candidate execution used 5,613
  physics steps and achieved native task success.
- **G1, twelve clutter objects:** three static layouts reached execution and
  failed at descent; one retry did not produce a complete layout. Total: 93.10
  seconds and 2,618 proposals. The four-layout limit was reached.
- **G2, twelve clutter objects:** a complete reference was acquired, but none
  of four 1,000-proposal retries found a layout meeting the full request.
  Total: 69.64 seconds and 4,000 proposals. No candidate was physically executed.

Partial layouts are not exported. Controller failure means unverified by this
controller and budget; it does not establish physical impossibility. The
controller still uses a fixed grasp orientation and transfer order, so these
results also depend on that limitation. G1's first six-object candidate exactly
reproduces the earlier static candidate; this is a regression check, not a new
independent sample.

## Reload and deadline checks

The accepted six-object G2 episode was loaded in a fresh simulator and completed
with online scene-aware planning, without supplying the saved reference control
file. It again passed all 24 development checks: see the
[reload validation](exported-episode-online/validation-summary.json).
This is a second execution of the same generated episode, not another generated
instance or an OpenWAM/GPT-6 policy result. The privileged planner still receives
scene geometry internally.

Two separate three-second deadline probes stopped during simulator startup,
produced no candidate episode and recorded `budget_exhausted`. Actual elapsed
times were 3.216 and 3.218 seconds, including process cleanup. They are diagnostic
interruptions, not failed generation samples. Unit checks also cover interruption
cleanup and rejection of a pre-existing output directory.

## Evidence and limits

Each request folder contains its compact run record, per-attempt checks and a
hash index of the local raw artifacts. Absolute command paths and process IDs
are omitted from public run records; raw-record hashes are retained. Large
trajectory/contact logs, licensed native assets and runtime meshes remain outside
Git. The accepted episode manifest records its relative file names and hashes.

The recorded Git revision is the base revision; per-file source hashes identify
the uncommitted implementation actually executed. Source snapshots are retained
with local raw runs under `outputs/v1-retry-development/`. The four method runs
share one source version. Later changes strengthened process cleanup and output
directory protection; the second deadline probe covers the cleanup revision.
These subsequent changes do not rerun or replace the four method results.

One seed cannot support an efficiency ranking or overall generation success
rate. The checks are developmental and do not prove continuous collision freedom.
Remaining paper evidence includes the event-conditioned adjustment mechanism,
broader task/condition coverage, frozen validation and held-out experiments,
and both policies on original and generated RoboDojo instances.
