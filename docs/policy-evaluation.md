# 原生与生成实例的策略评测

状态：开发接入；尚无本仓库两个策略 × 两类实例的正式成绩。
本文落实 [投稿约定](paper-contract.md) 第 4 项，不增加新的生成算法或评测创新主张。

## 共同执行入口

`infinidojo.policy_eval` 加载 RoboDojo 原生 `arx_x5` 的 `stack_blocks` 布局，
或经 `infinidojo.episode` 验证哈希的生成 bundle。两者均调用固定 RoboDojo 版本的
`create_eval_env`、原生 reset、观测、动作插值、奖励、终止条件和结果保存。
不复制评分逻辑，也不将参考控制器作为被测策略。

入口另已接入 `general_pickup` 的任务选择：生成实例读取 manifest 中的任务，原生实例用
`--task general_pickup`；配对脚本据 episode 选择两组的同一原生任务。
OpenWAM 服务需使用匹配的 `--task`。该任务的原生预算为 200 个策略动作，来自原生任务类，
不能沿用堆叠的 550。真实 OpenWAM 已完成原生拾取 layout 0 和新的程序化目标 episode：
两局各执行 200 个动作、均为 0/100，评分与实际输入输出审计通过。
生成输入从独立目录加载，见 [v2 接口证据](../results/v2-language-route-development/README.md)。

当前单环境入口保留各任务的原生动作上限（堆叠 550、拾取 200）、25 Hz 观测和 0.004 秒物理步长。
一个策略动作由原生 evaluator 执行 10 个物理步；模型调用与动作数分别记录。
输入布局和输出目录通过运行时对象配置，未改动上游源码或资产。
继续使用已有的 reset 清理：只禁用被上游退役的隐藏物体碰撞体。

策略输入只允许三路原生 RGB、形状、任务指令、本体状态、前次控制和频率元数据。
意外出现深度、物体真值或其他字段时直接报错。观测记录保存实际返回的数据与 RGB PNG，
不额外推进仿真或取样。参考轨迹、event 和生成请求保留在 harness，不传给策略。
此通道检查不替代适配器审查：策略的运动规划器访问范围仍须单独记录。

运行需要匹配 Isaac 的 Python，以及已启动的 XPolicyLab WebSocket 服务：

```bash
PYTHONPATH="$PWD/src" "$SIM_PYTHON" -m infinidojo.policy_eval \
  --robodojo-root "$ROBODOJO_ROOT" --xpolicylab-root "$XPOLICYLAB_ROOT" \
  --output outputs/policy-native-example --policy GPT_6_Astra_Codex_EEF \
  --port 6123 --native-layout 0 --headless --enable_cameras
```

生成条件将 `--native-layout 0` 替换为 `--episode /absolute/path/to/episode`。
`--observe-only` 只检查重置及相机，结果明确为 `observation_diagnostic`，没有策略分数。
已有输出目录不可覆盖；执行错误单列为 `execution_error_not_a_policy_score`。

## Astra 开发配对配置

`scripts/astra_pair.py` 固定选择原生 layout 0 和已有六杂物语言生成 episode，
在看到策略结果前确定两组输入，顺序运行，不因失败加预算或修改提示。

- XPolicyLab：`f6e309e8ece20bfa33c0e5d7b05a5f3287522757`。
- 适配器：已有本地 `GPT_6_Astra_Codex_EEF`；模型 `gpt-6-astra`，reasoning `medium`。
- 每局最多 100 次模型调用，保留两次观测的图像历史，不提供深度，每次服务调用超时 240 秒。
- EEF 目标由上游本地规划器转为关节动作；不接入生成器的场景感知参考规划器。
- 原生与生成条件共用模型、提示、观测与动作链路，分别保留轨迹、视频及原生评分。

```bash
PYTHONPATH="$PWD/src" .venv/bin/python scripts/astra_pair.py \
  --adapter-workspace "$ASTRA_ADAPTER_WORKSPACE" \
  --robodojo-root "$ROBODOJO_ROOT" --sim-python "$SIM_PYTHON" \
  --episode outputs/v1-language-development/generated-from-description/episode \
  --output outputs/v1-policy-development/astra-pair-01
```

`ASTRA_ADAPTER_WORKSPACE` 当前指向先前复现所用的本地工作区，需包含上述 XPolicyLab、
`codex_bridge.py` 和本地适配器。运行归档其代码及哈希，尚未将这个本地适配器作为本仓库
独立发布物；正式可复现发布仍需补齐该依赖。Codex 传输增加基础提示，将角色序列化，
以结构化 JSON 选择工具，每次重新调用；此条件不能当作官方 API 或排行榜的等价复现。

## 当前证据和缺口

原生 layout 0 的重置/相机诊断正常退出，保存三路 RGB 与本体状态，执行 0 个策略动作。
测试验证观测通道拒绝、RGB 无交换、调用/返回值保持和异常后恢复。
这些只证明接线与记录功能，不能证明模型已完成评测。

OpenWAM 已使用固定版本的干净副本完成真实权重动作推理，部署配置见下文；
随后完成原生 layout 0 与六杂物生成实例的开发配对，两局均执行 550 个有效动作，
原生过程分均为 15/100，完整任务均未成功。实际原生结果与输入输出审计一致，
见 [开发证据](../results/v1-policy-development/README.md)。这证明接入与评分流程运行，
不支持难度或模型优劣结论。两策略四组比较、更广任务覆盖及正式冻结仍未完成。

Astra 首次开发配对遇到账号调用额度耗尽：原生局完成 74 次调用和 295 个动作，
第 75 次调用失败；生成局首次调用即失败。两局都没有完整成绩，不能计为任务失败。
Kit 关闭流程可能在 Python 异常传播前以 0 退出，进程退出码不能代替结果证据。
`infinidojo.policy_results` 检查显式错误、原生评分、实际动作计数、输入/源码哈希及
模型调用记录；故障局保留已经消耗的计算，未报告的失败调用用量保持未知。

## OpenWAM 的显存约束与部署诊断

新服务入口 `infinidojo.openwam_service` 从上述固定 XPolicyLab 的干净副本加载权重，
只在本进程中将文本编码器留在 CPU，并将其输出送回 GPU。三个相关上游文件的哈希须匹配；
不会修改旧副本、上游文件或 checkpoint。临时路径映射让适配器读取同一 RoboDojo 的机器人配置。

该 variant 名为 `OpenWAM-step60000-cpu-text-bf16`，保留 BF16、10 次同步去噪和完整动作 chunk；
上游适配器禁用 compile、DiT cache 与视频解码。CPU/GPU 计算不声称逐位等价，正式两组
评测必须共用这个部署配置。模型仍采用真实权重，不能开启 `allow_dummy_policy`。

已单独加载真实文本编码器的 5,680,910,336 个参数，在 CPU 上执行原生堆叠指令：
输出为有限值的 `[1, 512, 4096]` BF16 张量，8 线程下该次约 7.25 秒。
随后在已记录的原生三路 RGB/本体观测上完成整个模型的一次推理：32 步双臂 EEF 动作，
约 6.91 秒，PyTorch 记录的峰值分配约 13.64 GB。该值不含同时运行仿真的显存，
也不是整个 GPU 进程占用。此项没有推进仿真，仍不构成策略评分。
首次完整模型诊断因归档代码尝试解析未使用的训练日期占位符而中止，原记录保留；
归档改为保留原始表达式，未修改模型推理配置。
checkpoint SHA256：`78ca4ef5d2f6c8fa877bcae8b40c36b3d8700547fd5a708b472c4564e5f40abb`。

```bash
PYTHONPATH="$PWD/src" "$OPENWAM_PYTHON" -m infinidojo.openwam_service \
  --xpolicylab-root "$XPOLICYLAB_ROOT" --robodojo-root "$ROBODOJO_ROOT" \
  --checkpoint "$OPENWAM_CHECKPOINT" --output outputs/openwam-service-example --port 6124
```

服务入口归档实际配置、权重哈希、模块放置与显存。
`--probe-observation /absolute/path/to/policy-io.jsonl` 可改为单次真实动作推理诊断，
不启动仿真、不产生任务成功率。正式性能评测仍必须经过完整 evaluator。

`scripts/openwam_pair.py` 在看到结果前保存原生布局及生成 episode 的哈希，启动同一个
真实权重服务，依次运行两组条件。每局复制服务配置与权重来源，禁止 dummy policy；
两局必须保持部署记录相同。OpenWAM 的直接 evaluator 调用必须显式传入
`--policy-service-record /absolute/path/to/service.json`。

```bash
PYTHONPATH="$PWD/src" .venv/bin/python scripts/openwam_pair.py \
  --xpolicylab-root "$XPOLICYLAB_ROOT" --robodojo-root "$ROBODOJO_ROOT" \
  --sim-python "$SIM_PYTHON" --model-python "$OPENWAM_PYTHON" \
  --checkpoint "$OPENWAM_CHECKPOINT" \
  --episode outputs/v1-language-development/generated-from-description/episode \
  --output outputs/v1-policy-development/openwam-pair-01

PYTHONPATH="$PWD/src" .venv/bin/python -m infinidojo.policy_results \
  --pair outputs/v1-policy-development/openwam-pair-01 \
  --output outputs/v1-policy-development/openwam-pair-01-audit.json
```

审计只在启动器确认进程已退出后给出最终状态；未完成、故障和有效的策略失败分别保留。
两个实例的开发配对仅用于验证接入，不支撑两种策略或两个实例分布的总体优劣结论。
