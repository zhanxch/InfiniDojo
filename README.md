# InfiniDojo

Task-conditioned procedural scene generation for robot manipulation evaluation,
built on **Infinigen** and **RoboDojo**.

## Method

1. An LLM agent translates scene descriptions and a supported RoboDojo task
   contract into procedural constraints and interaction events. Structured
   generation feedback drives bounded refinement.
2. An interaction-centric generator coordinates procedural assets and scene
   layouts with event-conditioned robot–object motion. Grasp establishment,
   lift-off, and release change the constraints between bodies. Robot links
   remain articulated; only a stable grasp permits a fixed hand–object transform.

Reference motions are internal generation and validation tools. Evaluated
policies remain free to choose their own actions. Failure diagnosis and RSI
evaluation are downstream applications, not the core method.

## Initial implementation scope

The first experiment targets RoboDojo's `general_pickup` task: physically grasp
and lift a procedurally generated rigid object by more than 10 cm. The native
task predicate is retained. This is a benchmark extension, not an official
RoboDojo leaderboard run.

The prototype and its first evaluation are **in development**. No experimental
results are claimed until simulator outputs and their provenance are committed.

See [evaluation protocol](docs/evaluation.md) and
[dependency provenance](docs/dependencies.md). Runtime assets, environments,
credentials, and large videos are excluded from Git.
