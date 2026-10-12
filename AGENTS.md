# InfiniDojo research and implementation agreements

The user-approved research contract is `docs/paper-contract.md`; the staged
implementation plan is `docs/next-stage.md`. Follow the contract when those
documents differ in emphasis. Update evidence and status as work progresses.

- The main contribution is controllable generation of physically validated,
  runnable RoboDojo evaluation instances. Efficiency is a secondary measurement.
- Keep the method minimal: event-dependent contact/support relations and
  interaction-conditioned clusters. Add mechanisms only for demonstrated gaps.
- Reference controllers are privileged generation/validation tools. Do not leak
  trajectories, event annotations or scene ground truth to evaluated policies.
- Compare OpenWAM and GPT-6 Astra on native and generated instances using the
  same RoboDojo runtime and task semantics. Record their different control stacks.
- Preserve native task success predicates. Supplement them with independent
  physical checks; never silently weaken a task or rename a diagnostic success.
- Freeze experimental conditions before held-out execution. Preserve failures,
  infrastructure errors, cost, provenance and denominators. No selective seeds.
- `results/v0-pilot/` and its protocol are historical evidence. Keep them intact;
  use new output directories, and pin source revisions for every later study.
- Do not modify upstream dependency checkouts or copy licensed assets into Git.
  Keep host paths, credentials and large runtime artifacts outside tracked files.
- Stop at the declared paper scope. Negative results require honest claims, not
  an unbounded search for favorable tasks, models or success thresholds.
