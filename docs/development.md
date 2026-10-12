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
