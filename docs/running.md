# Running v0

Use two Python environments: a Blender 4.2 / Python 3.11 tooling environment
for asset export, and the pinned RoboDojo-compatible Isaac environment for
physical execution. Do not install the standalone `usd-core` wheel into the
Isaac runtime. The upstream simulators/assets must already be installed using
their own instructions and license terms.

## Dependencies

```bash
git clone --branch v1.19.0 https://github.com/princeton-vl/infinigen .deps/infinigen
git -C .deps/infinigen submodule update --init infinigen/infinigen_gpl infinigen/OcMesher
git clone https://github.com/RoboDojo-Benchmark/RoboDojo .deps/robodojo
git -C .deps/robodojo checkout 726e9aabfaa642203722eb126f5eaf0f37f3e1ad
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[assets,dev]'
```

Infinigen also needs its [upstream Python dependencies](https://github.com/princeton-vl/infinigen/blob/v1.19.0/pyproject.toml)
and [installation workflow](https://github.com/princeton-vl/infinigen/tree/v1.19.0);
this adapter does not replace that installation. Connect an existing
licensed RoboDojo asset installation at `.deps/robodojo/Assets`. The native
fixture adapter currently uses its `arx_x5/0/general_pickup_0.json` layout.
Use absolute paths when invoking the native runner, which changes directory
to the RoboDojo checkout for upstream relative-path compatibility.

The live LLM backend uses `codex exec` with a signed-in account, structured JSON,
an empty working directory, read-only sandbox and no-tool prompting. Invocation,
responses, JSON events, feedback and timing are retained. The six-case pilot
pins `gpt-6-sol`; each invocation records the explicit model argument. No token
or credential file belongs in the repository.

## Study stages

From this repository, set `TOOL_PY` to the tooling Python and `SIM_PY` to the
RoboDojo Isaac Python. Set `RUN`, `IG_ROOT`, and `RD_ROOT` to absolute paths.
For example, `RUN="$PWD/outputs/v0-pilot"`.

```bash
export PYTHONPATH="$PWD/src"
"$TOOL_PY" -m infinidojo.study freeze --output "$RUN"
"$TOOL_PY" -m infinidojo.study llm --output "$RUN" --model gpt-6-sol
"$TOOL_PY" -m infinidojo.study assets --output "$RUN" --infinigen-root "$IG_ROOT"
"$TOOL_PY" -m infinidojo.study references --output "$RUN" --robodojo-root "$RD_ROOT"
"$SIM_PY" -m infinidojo.native --robodojo-root "$RD_ROOT" \
  --asset "$RUN/assets/01100" --seed 1100 --output "$RUN/references" \
  --batch "$RUN/reference_cases.json" --headless --enable_cameras
"$TOOL_PY" -m infinidojo.study layouts --output "$RUN"
"$SIM_PY" -m infinidojo.native --robodojo-root "$RD_ROOT" \
  --asset "$RUN/assets/01100" --seed 1100 --output "$RUN/evaluation" \
  --batch "$RUN/evaluation_cases.json" --headless --enable_cameras
"$TOOL_PY" -m infinidojo.evaluation --output "$RUN"
```

Native runtime environment setup must follow the existing Isaac installation.
On the development host its Conda `libstdc++.so.6` must be preloaded to avoid
the older system C++ ABI being selected. That path is machine-specific.

Each output stage refuses to overwrite completed invocation/asset directories.
Use a fresh run directory for a new experiment. Inspect live process status
before retrying an interrupted command. Results distinguish no generated scene,
no completed execution, native task failure and failed geometric validity.

## Scope and interpretation

The reference controller uses native CuRobo planning for approach, descent and
lift, plus physical gripper closure. The reference is replayed unchanged for
paired layout methods. The control budget is about six seconds at 250 Hz; this
is a privileged feasibility test, not a generalist policy under the official
200-action benchmark interface.

All native robot collision bodies are bounded in their own frames, including
the fingers and wrist camera. The arm remains articulated. After grasp, the
generator propagates one measured gripper–object transform; the final audit
instead uses observed object poses. Generation uses axis-aligned boxes; audit
uses oriented boxes. Both are conservative and sampled every 20 ms. They do
not prove continuous collision freedom or measure contact forces.

The CPU reset adapter disables descendant collision shapes of retired objects
because hiding their root alone can leave child mesh collisions active. It also
restores actual robot joint state before placing the next scene. No object is
welded or teleported during execution.

```bash
"$TOOL_PY" -m pytest -q
"$TOOL_PY" -m ruff check src tests
```
