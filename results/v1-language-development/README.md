# Complete-stack language frontend: development evidence

Eight supported descriptions compile into faithful constraints, and four
unsupported descriptions are explicitly declined. All twelve live model calls
succeed on the first response. One compiled description also drives actual
scene generation, complete physical execution, independent validation and
episode export. This verifies the restricted interface and its integration;
it provides **no evidence that iterative refinement improves generation**.

## Inputs and evaluation

The [development descriptions](../../specs/language/stack-development.json)
cover Chinese and English, metre/centimetre/millimetre conversion, zero/unpaired/
paired clutter, explicit or relative task-position options, a narrow region and
selection of a matching asset subset. Four unsupported inputs require odd-count
disjoint pairs, thirty objects, a different task, or water-filled open containers.
The [capability context](../../specs/language/stack-context.json) is derived from
the actual native layout and three precomputed Infinigen assets.

The requested compiler model is `gpt-6-sol`, with at most three calls per
description and 90 seconds per call. This is the existing signed-in structured
CLI backend. The recorded model argument is not a verified immutable service
snapshot. Calls run in an empty working directory; no tool actions are observed
in their event logs.

Expected answers are used only after the compiler finishes. They are not passed
to its prompt, capability context or validator feedback. The validator checks
supported constraints, asset identities and the complete two-transfer/return
contract. Fidelity scoring separately compares the emitted request with the
annotated description, including numeric tolerance of 1e-9 m and unordered
position-option/asset sets. Decline explanations were also inspected.

See [case rows](case-rows.json) and [raw model responses and histories](responses.json).

## Results

- Supported descriptions: **8/8** faithful on the first response; all compile.
- Unsupported descriptions: **4/4** correctly declined on the first response;
  none produces an executable compilation manifest.
- Total: twelve calls, **zero feedback rounds**, 211.12 seconds of summed compiler
  wall time. First-response and final-response scores are identical.
- An injected invalid transfer in a unit test exercises correction and export
  gating. That test is wiring evidence, not a live-model refinement result.

The first description requests six clutter cans in three pairs, 9 cm pair
separation, 5 mm clearance, the declared XY region and unchanged task positions.
Its [compiled request](compiled-example/generator-request.json) and
[event program](compiled-example/interaction-program.json) are consumed by
`request_run --compiled` without hand-editing the model output.

The G3 backend accepts its first layout after 307 placement proposals. Real
execution completes the native three-block stack and return in 5,613 physics
steps. All **24 applicable development checks** pass; no position repair is
triggered because positions are fixed and the first candidate succeeds.
The exported episode's 18 file hashes are verified by the loader. This run is
an integration check on a previously studied seed/condition, not an additional
independent scene family or a policy evaluation.

Compiler time for this example is 21.83 seconds; backend time is 115.37 seconds.
Their sum is 137.20 seconds, excluding precomputed asset creation and the
between-stage human/orchestration interval. The backend's 240-second limit
does not include language calls. See [physical run](physical-run.json),
[independent validation](physical-validation.json) and [episode manifest](episode-manifest.json).

## Scope and reproducibility

The language profile currently supports the implemented `stack_blocks` template
and selection from an explicit CanFactory pool. It does not implement arbitrary
task planning or new asset generation from text. Compiled events describe
constraints; only physical execution supplies feasibility evidence.

Twelve development descriptions do not establish general language robustness.
Only the first accepted description is physically executed here; schema-valid
requests may still fail generation. Other agreed tasks, broader calibration,
held-out experiments and OpenWAM/GPT-6 Astra policy comparisons remain pending.
The compiler model is separate from those evaluated policies.

[Usage and reproduction](../../docs/language-frontend.md) documents both stages
and the study command. Per-call source snapshots, prompts, responses, event logs
and invocation records remain under `outputs/v1-language-development/`.
[Raw artifact hashes](raw-artifacts.json) index those files; public records omit
absolute command paths and process IDs. All calls and the physical pipeline
use the same compiler/generator source version, identified by per-file hashes
over the recorded base commit. No credentials or native meshes are included.
