# InfiniDojo v0 — first quantitative evaluation

Completed on 2026-10-12. Frozen implementation: `c62d46a`.
All 54 preregistered requests were generated and physically executed in native
RoboDojo. No seed was replaced, and no evaluated source file changed after
the [freeze](freeze.json).

![Native task success and conservative usable-scene yield](comparison.png)

## Main results

Each method has 18 requests: six held-out seeds × clutter counts 0, 6, 12.
All use the same six actual Infinigen assets and native pickup predicate.

- **Independent placement:** native lift success **14/18 (77.8%)**;
  conservative usable yield **9/18 (50.0%)**.
- **Static collision rejection:** native lift success **17/18 (94.4%)**;
  conservative usable yield **15/18 (83.3%)**.
- **Interaction-conditioned placement:** native lift success **18/18 (100%)**;
  conservative usable yield **18/18 (100%)**.

The usable metric requires accepted generation, clear reference and observed
trajectory bounding-box audits, initial XY positions preserved within 1 cm
after settling, and the native lift goal. Bounding boxes are conservative;
these percentages are not exact mesh-contact collision rates.

All zero-clutter requests passed both metrics. Considering only the 12
nonzero-clutter requests per method, usable yield is **3/12, 9/12, 12/12**;
native lift success is **8/12, 11/12, 12/12**, respectively.

Compared with static placement, interaction conditioning improved the declared
usable metric by 16.7 percentage points, but actual native task success differed
by only **one** instance. The usable improvement occurred in two of six paired
seeds. This is preliminary evidence for the direction, not statistical proof
of superiority or a paper-level benchmark result.

## Components and cost

- All 54 requests preserved the requested clutter count and support-region
  bounds, including after physical settling.
- Initial XY positions stayed within 1 cm in 12/18 independent, 18/18 static,
  and 18/18 interaction scenes. Independent placement can start with overlaps;
  physical depenetration can change the intended scene.
- Observed trajectory OBB audits were clear in 11/18, 15/18, and 18/18 scenes.
  Intended finger–target contact is excluded. The audit checks the full robot
  and manipulated object against added clutter; it is not a self-collision or
  contact-force certification.
- Six live `gpt-6-sol` requests, covering two phrasings for each clutter count,
  all passed on the first call. The bounded refinement mechanism is implemented,
  but this pilot supplies **no measured benefit from refinement**.
- Six seeds produced six distinct canonical meshes, with rectangular and
  circular can variants. See [geometry content hashes](mesh_content_hashes.json).
- Mean layout-only generation time was approximately **0.27 ms, 0.49 ms,
  11.49 ms**; mean candidate counts were **6.00, 8.11, 8.67**. These means include
  the zero-clutter cases. Reference acquisition cost is additional: **73.0 s**
  for six clean references, including simulator/planner startup.
- The complete physical evaluation took **1,049.8 s (17.5 min)**. Six LLM calls
  took **130.6 s** and reported 82,535 input tokens (12,032 cached) and 1,368 output
  tokens. These are Codex CLI totals including its harness context, not a direct
  API cost estimate. See [costs](costs.json) and [runtime versions](runtime.json).

Descriptive 95% Wilson intervals for usable yield are 29.0–71.0%, 60.8–94.2%,
and 82.4–100%. Conditions share six seeds, so the 18 requests per method are not
18 independent task draws. [Per-seed paired differences](results/summary.json)
are provided rather than a significance claim.

## What this evaluates

This is a generator feasibility study using a privileged reference controller,
not an evaluation of a generalist VLA model. The robot physically closes its
gripper and lifts the target; no object is welded or teleported during actions.
The native `general_pickup` goal is retained (height gain strictly above 10 cm).

The three baselines are implementations in this repository. They are **not
reproductions of InfiniBench, RoboTwin or Steerable Scene Generation**.
See [research positioning](../../docs/research-positioning.md) for fair related
work comparisons and the larger evaluation plan.

v0 is limited to one task and one asset family, using a frozen reference motion
to condition clutter generation. It does not implement arbitrary trajectory–
scene joint optimization. Collision geometry is approximated by boxes sampled
every 20 ms; this cannot prove continuous collision freedom. Asset shaders use
USD PreviewSurface, so this release does not claim photoreal rendering quality.

## Evidence and reproduction

- [Per-instance metrics](results/instances.json) and [summary](results/summary.json).
- `generated/`: all accepted layouts, candidate/rejection records and reference audits.
- `evaluation/`: all 54 native execution results and observed-trajectory audits.
- `references/`: six native reference outcomes and batch timing.
- `llm/`: real prompts, structured responses, feedback and backend JSON events.
- [Request fidelity checks](request_fidelity.json), [archive checks](archive_check.json),
  [raw artifact hash index](raw_artifact_index.json).

The public archive contains small numeric evidence. Full trajectories, controls,
Blender source scenes and mesh exports remain in the local run directory
`outputs/v0-pilot`; their relevant hashes/provenance are retained here. Rebuild
them using [the run instructions](../../docs/running.md). No licensed native
RoboDojo robot/environment asset is copied into this repository.

After the study stages, package and plot with:

```bash
python scripts/check_request_fidelity.py --run outputs/v0-pilot
python scripts/archive_results.py --source outputs/v0-pilot --destination results/new-run
python scripts/plot_results.py --results results/new-run
```

The archive script refuses a missing rollout or a changed frozen implementation.
Use a fresh output/destination directory for reproduction.
