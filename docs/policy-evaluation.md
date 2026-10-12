# 原生与生成实例的策略评测

状态：开发接入；尚无本仓库两个策略 × 两类实例的正式成绩。
本文落实 [投稿约定](paper-contract.md) 第 4 项，不增加新的生成算法或评测创新主张。

## 共同执行入口

`infinidojo.policy_eval` 加载 RoboDojo 原生 `arx_x5` 的 `stack_blocks` 布局，
或经 `infinidojo.episode` 验证哈希的生成 bundle。两者均调用固定 RoboDojo 版本的
`create_eval_env`、原生 reset、观测、动作插值、奖励、终止条件和结果保存。
不复制评分逻辑，也不将参考控制器作为被测策略。

当前单环境入口保留 550 次策略动作上限、25 Hz 观测和 0.004 秒物理步长。
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

OpenWAM 权重和旧部署环境已在本机找到，但旧副本含 CPU 文本编码器迁移补丁；
需确定可复现的部署配置并核对内存，再运行真实权重。当前入口列出 OpenWAM 选项不等于
已验证它的执行链路。两策略四组比较、更广任务覆盖及正式冻结仍未完成。
