# InfiniDojo

Task-conditioned procedural scene generation for robot manipulation evaluation,
built on **Infinigen** and **RoboDojo**.

## Research direction

InfiniDojo aims to generate RoboDojo evaluation episodes with customizable scene
complexity through natural-language requests. A minimal task scene and reference interaction guide procedural
scene completion, so requested variations can preserve a physically executable
task. The current route has two main parts; the historical v0 evidence is listed separately.

1. An LLM compiles descriptions into bounded appearance, spatial-relation and
   scene-composition constraints. The backend retains complete native task and
   event programs; unsupported requirements are explicitly declined.
2. Procedural task assets are instantiated before obtaining a reference interaction.
   Event-dependent contact, attachment and support relations describe the operation;
   scene completion preserves its full motion space while satisfying requested layouts.
   Robot links remain articulated; only a stable grasp permits a fixed hand–object transform.

Reference motions are internal generation and validation tools. Evaluated
policies remain free to choose their own actions. Failure diagnosis and RSI
evaluation are downstream applications, not the core method.

## Current development and next stage

The finite engineering route is now connected end to end. The
[`scene-v2` entry point](docs/language-frontend.md) accepts natural-language
requests, creates Infinigen assets on demand, obtains a reference for the actual
task geometry, completes the scene under motion and layout constraints, and
exports a portable RoboDojo episode.

The [route development evidence](results/v2-language-route-development/README.md)
contains nine live language calls: seven supported requests compiled faithfully
and two unsupported requests were declined. All seven declared generation
conditions passed complete native task execution on their first full layout;
all seven bundles passed loading checks after relocation. The tasks are pickup,
stacking, and covering/uncovering. Five pickup conditions share one seed and
isolate appearance, relations, count, and their combination around a baseline;
there are three base seeds in total, not seven independent samples.

Current controls are deliberately bounded:

- **Appearance:** named material colors and target/distractor color similarity.
  The appearance pair preserves geometry, final layout, and reference motion.
- **Relational:** paired placement and whole-footprint target-relative regions.
- **Compositional:** distractor count with one common can profile. Mixed asset
  categories and unrestricted composition are not implemented.

Pickup uses an actual generated CanFactory manipulation target. Stacking and
covering retain native task objects and full task semantics. New geometry is
instantiated before reference construction. Materials use USD PreviewSurface;
this is not a claim of photorealistic shader export or camera control.

A real OpenWAM checkpoint completed the unchanged RoboDojo evaluator on native
pickup layout 0 and the relocated generated baseline. Both ran 200 valid actions
and scored 0/100. These are completed policy failures, demonstrating evaluation
integration without implying success, comparative difficulty, or model superiority.
Reference actions and event annotations are not policy inputs.

Construction preserves complete reference occupancy; event/contact/support
semantics organize the interaction. This does not establish an independent new
cluster optimizer or a formal continuous-time feasibility guarantee. G3 repair
is not used by this route. Its [existing development results](results/v1-pickup-retry-development/README.md)
show it works, without an efficiency advantage; further repair heuristics are frozen.

The [paper scope and stopping contract / 投稿版本约定](docs/paper-contract.md) and
[next-stage plan / 下一阶段任务](docs/next-stage.md) separate the completed engineering
route from the still-unfinished held-out paper experiments and two-policy
comparison. The next research work is a finite evaluation of controllability,
construction validity, and native/generated policy behavior, followed by writing.
Higher throughput and policy ranking reversals are not required conclusions.

Earlier evidence remains available: [full-stack references](results/v1-development/README.md),
[scene completion](results/v1-completion-development/README.md),
[planning integration](results/v1-planning-development/README.md),
[bounded generation](results/v1-retry-development/README.md),
[repair](results/v1-repair-development/README.md),
[language compilation](results/v1-language-development/README.md),
[policy integration](results/v1-policy-development/README.md),
[pickup](results/v1-pickup-development/README.md),
[cover/uncover](results/v1-cover-development/README.md), and
[procedural-target integration](results/v2-route-development/README.md).
These are dated development records, not additional held-out studies.
See [development and reproduction](docs/development.md) and
[policy evaluation](docs/policy-evaluation.md).

## Initial implementation scope

The first experiment targets RoboDojo's `general_pickup` task: physically grasp
and lift a procedurally generated rigid object by more than 10 cm. The native
task predicate is retained. This is a benchmark extension, not an official
RoboDojo leaderboard run.

The v0 prototype includes real Infinigen can meshes, a live structured LLM
compiler with validator feedback, three matched layout generators, native
RoboDojo reference execution, and independent collision-box audits.

The [first quantitative study](results/v0-pilot/README.md) is complete: all 54
held-out scene requests were physically executed. Native lift success was
14/18 (independent), 17/18 (static), and 18/18 (interaction). Conservative usable
scene yield was 9/18, 15/18, and 18/18, respectively. These are small controlled
in-house baselines, not reproduced results from other papers or VLA scores.

See [running the prototype](docs/running.md) and
[research positioning](docs/research-positioning.md).

![First evaluation](results/v0-pilot/comparison.png)

v0 conditions clutter placement on a validated reference interaction. It does
not yet jointly optimize arbitrary manipulation trajectories and scenes. Its
LLM feedback checks supported task/scene constraints. The asset family, task
and event vocabulary are deliberately small; release/placement tasks are future
extensions. USD exports use a preview material, not baked photoreal shaders.

See [evaluation protocol](docs/evaluation.md) and
[dependency provenance](docs/dependencies.md). Runtime assets, environments,
credentials, and large videos are excluded from Git.
