# v1 开发记录与复现

本阶段按 [投稿版本约定](paper-contract.md) 推进。
当前数据用于开发诊断，不是保留测试集结果，也不是 OpenWAM/GPT-6 Astra 策略成绩。

## 完整堆叠参考

`infinidojo.native_stack` 在原生 `stack_blocks` 上执行两次真实取放，再使机械臂返回初始位置。
它复用原生 CuRobo，不修改原生任务评分或物体质量/摩擦，不在操作过程中焊接或瞬移物体。
当前控制器限定原生布局 0 及附近右侧布局、三块同尺寸方块，由右臂完成操作。
这一步保留原生资产，用于打通完整交互；v1 的 Infinigen 场景补全尚未完成。

程序按 16 个 event 记录状态：每次取放的接近、下降、闭合、抬升、搬运、放置、释放、撤离。
event 标签表示控制阶段，不能单凭标签断言接触条件成立。
第二次取放继续记录第一块已放置物体及其支撑关系，实际物理状态独立于计划关系。

参考输出包括原生布局/配置、交互程序、实际对象和所有机器人连杆状态、控制序列、
逐物理步 PhysX 接触报告、视频、原生评分和来源哈希。参考信息不属于被测策略输入。
状态每 20 ms 采样，接触按 4 ms 物理步记录；这些不构成连续时间无碰撞证明。
原生评分外还需完成合法接触映射、独立几何检查、初始化符合度及稳定性阈值校准。

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

小型开发证据见 [开发结果](../results/v1-development/README.md)。完整验证器、场景补全、
生成对照和两个策略的两组实例评测仍未完成。
