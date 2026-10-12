# Evaluation protocol — v0

Written before the first development or evaluation rollout, 2026-10-12.

## Questions

- RQ1: Does the LLM accurately translate requested counts, spatial bounds and
  task events into supported procedural constraints? Does feedback repair invalid
  proposals without weakening the request?
- RQ2: Does accounting for interaction phases and the entire robot–object motion
  improve executable-scene yield over static-layout generation?
- RQ3: What are the time, rejection, diversity and condition-coverage costs?

Generation quality is the primary evaluation. A privileged reference controller
tests feasibility and is not a generalist VLA policy. Its success is reported as
reference execution success, not as robot-model benchmark performance.

## First quantitative comparison

Use one task (`general_pickup`), one fixed RoboDojo embodiment, one pinned
Infinigen asset backend, matched task-object seeds, matched clutter counts and
the same native success predicate.

Baselines implemented in this repository:

1. `independent`: independent asset placement within the requested support
   region. Does not use interaction trajectories.
2. `static`: rejects initial object overlap and initial robot collisions, but
   does not reserve space for the subsequent interaction.
3. `interaction`: preserves the same count and support constraints and checks
   event-conditioned robot-link, gripper and manipulated-object occupancy over
   the reference interaction; repairs scene placement within the same budget.

These are controlled in-house baselines. `static` is not a reproduction of
InfiniBench's cluster optimizer; `independent` is not the original RoboDojo
randomizer. Exact labels must be preserved in reports.

The first intended evaluation grid is 6 held-out seeds (1100–1105), 3 clutter
counts (0, 6, 12), and 3 methods: 54 requested instances. Development seeds are
0–49. Any change to this grid or to the frozen method must be recorded before
collecting replacement evaluation data. No selective deletion of failed seeds.
All method variants use the same asset pool and request for each paired seed.
Generation has an explicit candidate-attempt budget, recorded per instance.

## Required metrics and denominators

- Requested vs generated clutter count; support-bound satisfaction; task-target
  identity and instruction consistency. A reduced object count is not success.
- Generation acceptance rate over all requested instances, with reasons and
  attempt counts for failures.
- Independent full-path collision audit, broken down into arm, gripper and
  manipulated-object involvement and interaction phase. State geometric
  approximations and temporal resolution explicitly; sampled checks are not a
  continuous-time collision guarantee.
- Native RoboDojo reference execution success over all requested instances.
  Also report success conditional on a generated and successfully initialized
  instance. Missing or infrastructure-failed rollouts are not policy failures;
  retain their counts and both denominators.
- Physical grasp/lift outcome, object displacement and recorded native score.
  Kinematic attachment in the generator does not prove a physical grasp.
- Generation wall time, simulator wall time, LLM calls and tokens when exposed,
  attempted proposals, and effective asset/layout diversity.
- Paired seed-level differences and binomial intervals. The small v0 sample is
  descriptive; do not claim statistical superiority from a few rollouts.

## Separating the two method components

The geometry comparison uses shared validated specifications so parser differences
cannot explain layout-method differences. Separately evaluate scene-description
translation, record raw LLM outputs and validator feedback, and compare one-shot
with bounded refinement. A deterministic template may be a parser baseline but
must never be described as a live LLM result.

## Relevant work and larger comparisons

- **InfiniBench**, https://arxiv.org/abs/2511.18200:
  procedural constraint refinement, static layout and visual-input generation.
  An adapted static procedural optimizer is a useful larger baseline, but its
  published VLM accuracy is not comparable to manipulation success.
- **Infinigen Indoors**, https://arxiv.org/abs/2406.11824:
  the asset/constraint backend. Hold its asset family and renderer fixed when
  isolating the interaction-aware optimization contribution.
- **Steerable Scene Generation**, https://arxiv.org/abs/2505.04831:
  a direct scene-generation comparison candidate. A fair experiment needs the
  same task, assets, requests, feasibility checker and budget; no result is
  claimed before an actual compatible implementation is run.
- **RoboTwin 2.0**, https://arxiv.org/abs/2506.18088:
  task scripts, domain randomization and robot data generation. Compare native
  randomization/trajectory-conditioned generation under matched tasks if ported;
  cross-simulator success rates alone are not a fair generation comparison.
- **RoboCasa365**, https://arxiv.org/abs/2603.04356:
  related benchmark/environment infrastructure. Discuss coverage and interfaces;
  do not treat an unmatched benchmark score as a method baseline.
- **Logic-Geometric Programming**,
  https://argmin.lis.tu-berlin.de/papers/15-toussaint-IJCAI.pdf:
  prior work for event-conditioned kinematic switches. Event segmentation and
  grasp attachment alone are not claimed as novel. The research question is
  their use in controllable procedural evaluation-scene generation.

## Reproducibility and reporting

Preserve source revisions, generator configuration, asset provenance, seeds,
accepted/rejected candidates, reference trajectory, per-instance native output,
runtime logs, and failure classes. Store small numeric evidence in Git; large
artifacts have hashes and reproducible generation commands. Freeze configuration
before held-out rollouts. Keep development and evaluation results separate.

The minimal release must include a real Infinigen asset generation, live LLM
constraint generation, interaction-aware placement, native RoboDojo execution
and a completed quantitative comparison. A geometry-only smoke test is not a
substitute for that release.
