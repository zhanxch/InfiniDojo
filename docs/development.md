# v1 开发记录与复现

本阶段按 [投稿版本约定](paper-contract.md) 推进。
当前数据用于开发诊断，不是保留测试集结果，也不是 OpenWAM/GPT-6 Astra 策略成绩。

## 完整堆叠参考

`infinidojo.native_stack` 在原生 `stack_blocks` 上执行两次真实取放，再使机械臂返回初始位置。
它复用原生 CuRobo，不修改原生任务评分或物体质量/摩擦，不在操作过程中焊接或瞬移物体。
当前控制器限定原生布局 0 及附近右侧布局、三块同尺寸方块，由右臂完成操作。
任务物体保留原生资产。G2 已围绕完整参考补全 Infinigen 杂物；关联任务位置调整的 G3 仍待实现。

程序按 16 个 event 记录状态：每次取放的接近、下降、闭合、抬升、搬运、放置、释放、撤离。
event 标签表示控制阶段，不能单凭标签断言接触条件成立。
第二次取放继续记录第一块已放置物体及其支撑关系，实际物理状态独立于计划关系。

参考输出包括原生布局/配置、交互程序、实际对象和所有机器人连杆状态、控制序列、
逐物理步 PhysX 接触报告、视频、原生评分和来源哈希。参考信息不属于被测策略输入。
状态每 20 ms 采样，接触按 4 ms 物理步记录；这些不构成连续时间无碰撞证明。
独立验证器已检查合法接触、实际持物关系、初始化符合度、支撑/稳定性及保守 OBB 几何。
阈值仍是开发值，正式实验前必须校准并冻结。

复用 [运行说明](running.md) 的环境变量，使用新的输出目录：

```bash
export PYTHONPATH="$PWD/src"
"$SIM_PY" -m infinidojo.native_stack --robodojo-root "$RD_ROOT" \
  --output "$PWD/outputs/v1-development/stack-1200" --seed 1200 \
  --headless --enable_cameras
```

可用 `--layout /absolute/path/layout.json` 加载原生格式布局。
`--max-steps` 默认 20,000，指参考控制器的原始物理步数，不能与原生 550 次策略动作等同。
原生任务契约见 [stack_blocks.json](../specs/tasks/stack_blocks.json)，开发种子与配置见
[v1-development.json](../specs/v1-development.json)。正式实验的预算和参数尚未冻结。

## 已发现并处理的接线问题

第一次开发运行通过原生评分，但接触文件为空。原因是 IsaacLab 默认关闭接触报告处理；
没有接触记录不能解释为没有接触。修正后显式启用 `/physics/disableContactProcessing = false`，
保留第一次运行并重跑相同场景，核实双指接触与释放后的方块支撑接触确实存在。

完整参考证据见 [参考开发结果](../results/v1-development/README.md)。

## G2 补全、独立验证与 episode 导出

先生成三个真实 Infinigen 资产，以下路径变量均使用绝对路径。
`REF` 指向一个完整 `native_stack` 参考输出，例如前节的 `stack-1200`。

```bash
DEV="$PWD/outputs/v1-example"
"$TOOL_PY" -m infinidojo.assets --infinigen-root "$IG_ROOT" --output "$DEV/assets/1200" --seed 1200 --radius .018 --height .05
"$TOOL_PY" -m infinidojo.assets --infinigen-root "$IG_ROOT" --output "$DEV/assets/1201" --seed 1201 --radius .025 --height .09
"$TOOL_PY" -m infinidojo.assets --infinigen-root "$IG_ROOT" --output "$DEV/assets/1202" --seed 1202 --radius .032 --height .13
"$TOOL_PY" -m infinidojo.completion --robodojo-root "$RD_ROOT" --reference "$REF" \
  --assets "$DEV/assets/1200" "$DEV/assets/1201" "$DEV/assets/1202" \
  --request "$PWD/specs/requests/stack-paired-6.json" --seed 1200 --output "$DEV/generated"
"$SIM_PY" -m infinidojo.native_stack --robodojo-root "$RD_ROOT" \
  --layout "$DEV/generated/layout.json" --asset-map "$DEV/generated/asset-map.json" \
  --reference "$REF" --seed 1200 --output "$DEV/execution" --headless --enable_cameras
"$TOOL_PY" -m infinidojo.validation --robodojo-root "$RD_ROOT" --execution "$DEV/execution" \
  --generation "$DEV/generated" --output "$DEV/validation.json"
"$TOOL_PY" -m infinidojo.episode --execution "$DEV/execution" --generation "$DEV/generated" \
  --validation "$DEV/validation.json" --output "$DEV/episode"
"$SIM_PY" -m infinidojo.native_stack --robodojo-root "$RD_ROOT" --episode "$DEV/episode" \
  --reference "$REF" --output "$DEV/reload" --headless --enable_cameras --no-video
```

生成失败时不会写 `layout.json`，应记录失败并停止该请求的后续执行；不能把部分布局当成功。
episode 导出要求全部声明检查通过，并重新核对验证输入和资产哈希。重新加载只依赖包内的
相对路径、单独安装的原生资产及指定 RoboDojo 版本。上例参考轨迹由外部验证调用提供，
没有装入 episode；被测策略适配器尚未接入，未来仍只向策略提供原生观测。

`--no-video` 仅关闭图像编码，保留控制、状态和接触记录。输出目录拒绝覆盖。
复现图片对应旧贪心配置，可增加 `--cluster-budget 1000`；默认 128 会在单个 cluster
难以放置时重新排布，但不增加总候选预算，不更换资产或放宽请求。

最新证据见 [场景补全开发结果](../results/v1-completion-development/README.md)。
G1/G3、验证阈值校准、LLM 对完整任务的接入、其余任务及两策略评测仍未完成。

## 场景感知规划与 G1 初始布局候选

`native_stack --scene-aware` 使用共用 CuRobo 在线规划，逐阶段更新实际物体、固定设施、
桌面及未操作机械臂的碰撞几何。夹指使用当前实际开合状态；持物几何只附着在规划模型中，
释放后物体重新进入场景。真实执行仍由物理夹持完成。该接口限于当前右臂堆叠控制器。

原生 wrapper 的碰撞场景只有桌面，机器人球模型也未包含夹指。新接口复用原生规划方法，
补充碰撞输入、检查 FK 与实际末端坐标的一致性，并只创建单环境所需的规划器。
固定底座球的下沿限制在底座支撑平面上方 0.1 mm，处理球拟合及浮点误差造成的桌面重叠。
这属于共用规划器接入，不是新的运动规划算法。

```bash
"$TOOL_PY" -m infinidojo.completion --robodojo-root "$RD_ROOT" --reference "$REF" \
  --assets "$DEV/assets/1200" "$DEV/assets/1201" "$DEV/assets/1202" \
  --request "$PWD/specs/requests/stack-paired-6.json" --seed 1200 \
  --initial-state-only --output "$DEV/static-candidate"
"$SIM_PY" -m infinidojo.native_stack --robodojo-root "$RD_ROOT" \
  --layout "$DEV/static-candidate/layout.json" --asset-map "$DEV/static-candidate/asset-map.json" \
  --scene-aware --seed 1200 --output "$DEV/online-execution" --headless --enable_cameras
"$TOOL_PY" -m infinidojo.validation --robodojo-root "$RD_ROOT" \
  --execution "$DEV/online-execution" --generation "$DEV/static-candidate" \
  --output "$DEV/online-validation.json"
```

`--initial-state-only` 只读取状态源的第一个 reset 状态，不要求参考成功，不使用其后续动作。
它仅生成 G1 的静态候选；`generation.json` 的 `accepted` 只表示布局采样满足约束，
必须通过后续规划、执行和共同验证才能成为可用实例。下节的请求执行器提供预算内重试。
上例开发诊断复用了已有状态快照，正式成本实验必须另计 reset/状态获取成本。

规划使用 OBB 和覆盖球近似。允许持物目标接触支撑面时，该支撑暂不进入优化器；
候选控制序列随后对所有世界物体逐点复查，只豁免指定持物—支撑接触及固定底座—桌面。
物理验证仍检查接触是否合法、穿透是否过深。包围近似可能误拒绝可行解，不能视为连续几何证明。
`planner-scenes.json` 保存每阶段场景、持物几何、优化器豁免、候选检查及耗时。

可在完成 `00/transport` 的场景感知执行上运行持物探针（仅使用 GPU 规划器）：

```bash
"$SIM_PY" -m infinidojo.planning_probe --robodojo-root "$RD_ROOT" \
  --execution "$DEV/online-execution" --output "$DEV/payload-probe"
```

探针在持物目标处添加虚拟障碍，依次检查无障碍成功、持物受阻失败、诊断性关闭持物几何后
成功、移除障碍并恢复持物几何后成功。它不修改真实场景，关闭几何仅是探针的负对照。
开发证据与未完成项见 [规划接入结果](../results/v1-planning-development/README.md)。

## 有界请求执行：G1 与 G2

`infinidojo.request_run` 为每个请求创建新目录，自动执行状态获取、候选生成、物理执行、
独立验证和 episode 导出。G1 先获取一个新的 reset 状态，再为每个静态候选在线规划；
G2 先取得一个新的完整参考，固定它并补全场景。两种方法都计入状态/参考获取及每次启动成本。

```bash
"$TOOL_PY" -m infinidojo.request_run --method g1 --robodojo-root "$RD_ROOT" \
  --sim-python "$SIM_PY" --assets "$DEV/assets/1200" "$DEV/assets/1201" "$DEV/assets/1202" \
  --request "$PWD/specs/requests/stack-paired-6.json" --seed 1200 \
  --wall-seconds 240 --max-layouts 4 --total-proposals 4000 \
  --proposals-per-layout 1000 --cluster-proposals 128 --max-steps 20000 \
  --output "$DEV/g1-request"
```

用 `--method g2` 和另一个新目录执行固定参考补全。`--video` 可开启视频，比较时应统一设置。
可用 `--layout` 提供原生初始布局。这里的 `--max-steps` 仍是参考控制器的物理步上限，
不等同于策略动作预算。所有约束在请求开始时复制，资产身份按请求种子只选择一次；
重试只改变布局采样种子，不能更换失败资产或削减数量。首个候选与独立生成入口可复现一致。

`run.json` 记录预算、每次尝试、每个子进程的耗时与退出状态。超过墙钟预算会终止该请求
启动的子进程组；清理超过截止时间的部分另行记录，不接受逾期产物。布局候选数和布局采样
提案数分别计数。若提案进程在写出结果前超时，实际提案数未知，记录其预分配上限，不能当作零。
启动失败、缺少结果文件或仿真 `error.json` 都归入基础设施错误；不能仅凭进程返回 0 接受结果。

只在共同验证的全部检查通过且导出在预算内完成后，状态才为 `accepted`，实例位于 `episode/`。
其他终态如 `layout_budget_exhausted`、`budget_exhausted`、`reference_unverified` 都不表示场景无解。
所有失败的生成目录和执行证据保留。请求开始后修改源码会使运行失效，需另开新目录。

当前输入复用已生成的 Infinigen 资产；历史资产生成耗时不可得，因此此计时明确不包含资产创建，
不能直接称为从资产生成开始的总成本。正式实验须补记这部分成本，并区分冷启动与复用。
这套入口也尚未实现 G3 的允许任务参数调整，不提供通用任务搜索。
开发配置见 [v1-retry-development.json](../specs/v1-retry-development.json)，
结果见 [预算内生成开发结果](../results/v1-retry-development/README.md)。
