# 完整拾取与持物终态：开发证据

日期：2026-10-13。数值、全部参考尝试与证据哈希见 [summary.json](summary.json)。
这是一种原生目标、两个开发位置的接入验证，不是冻结的有效率或算法优势实验。

## 新增能力

原生 `general_pickup` 保持“物体原点相对初始位置抬高超过 10 cm”的成功条件。
参考操作使用现有 CuRobo 和真实夹爪，记录
`approach → descend → grasp → lift → hold`。任务结束时仍持物，不人为增加释放或返回要求。
规划表示中的附着关系不产生物理焊接，动作执行中不瞬移物体。

沿用已有 G2 全参考运动空间补全，加入六个真实 Infinigen 杂物，组成三对、对内间距 9 cm。
独立验证增加持续抬升、初始支撑和稳定保持，并继续检查实际接触、手物相对位姿、
初始化、请求符合度、杂物稳定性及独立几何。26 项检查均须通过才能导出。
原有堆叠实例重新运行验证仍通过；没有放宽堆叠判定来接纳拾取。

## 预先声明的输入与开发失败

选择原生 `can/00000`，保留其质量 0.32 kg、摩擦参数 0.25 与原始几何。
两个位置在执行前声明：种子 1200 的 `(0.22, -0.05)` m，种子 1201 的 `(0.28, 0.02)` m。
这不是原生全部拾取物体的覆盖，也不是原生 layout 0 的原样复现；只沿用其固定设施。

开发参考共四次，均保留：

1. 种子 1200 从上方抓物体中部：接近、夹持后抬升规划失败；记录还发现目标与 link6 接触。
2. 同一位置改为水平侧向抓取：低处的预抓取规划失败，没有完成操作。
3. 改为前下方 45° 夹持，同一位置完整抬升并保持；约抬高 14.974 cm。
4. 相同参考配置用于预先声明的第二个位置：完整抬升并保持，约抬高 14.969 cm。

这些修正来自开发诊断，不作为未经调参的总体成功率；没有换目标、减小质量或放宽原生阈值。
当前抓取配置针对直立罐体，不宣称实现通用抓取合成。
参考执行中的原生成功布尔值与策略成绩分开：该任务没有中间过程分，早期参考输出的
reward-manager 原始零值不是正式策略的 0/100。后续记录明确标记该区别。

## 场景生成、执行和导出

- 种子 1200：17 个几何候选完成六杂物布局；实际重放成功，26 项检查通过。
- 种子 1201：18 个几何候选完成六杂物布局；实际重放成功，26 项检查通过。
- 两个实例均保留至少 1 秒的持物阶段；具体最小抬升、接触和稳定性证据见数值汇总。
- 第一个 episode 复制到新目录后，重新加载资产并实际重放，再次通过全部检查。
  参考动作留在 bundle 外部，没有作为策略输入打包。

几何候选耗时不包含参考获取、资产生成或物理验证，不能作为端到端效率。
两个开发实例的通过也不能替代预先冻结的批量生成实验。

## 复现入口

使用与现有开发实验相同的 RoboDojo/Isaac 环境及已有三个 Infinigen 资产。
`SIM_PYTHON` 指向 Isaac Python，`ROBODOJO_ROOT` 指向固定版本，按已有运行说明配置
`LD_PRELOAD`、许可环境变量和 `PYTHONPATH="$PWD/src"`。以下输出目录须不存在。

```bash
PYTHONPATH="$PWD/src" .venv/bin/python scripts/pickup_development_inputs.py \
  --robodojo-root "$ROBODOJO_ROOT" --output outputs/pickup-reproduction/inputs

PYTHONPATH="$PWD/src" "$SIM_PYTHON" -m infinidojo.native_pickup \
  --robodojo-root "$ROBODOJO_ROOT" --seed 1200 --max-steps 10000 \
  --layout outputs/pickup-reproduction/inputs/layout-1200.json \
  --output outputs/pickup-reproduction/reference --headless --enable_cameras

PYTHONPATH="$PWD/src" .venv/bin/python -m infinidojo.completion \
  --robodojo-root "$ROBODOJO_ROOT" --reference outputs/pickup-reproduction/reference \
  --assets outputs/v1-development/assets/1200 outputs/v1-development/assets/1201 outputs/v1-development/assets/1202 \
  --request outputs/pickup-reproduction/inputs/request-6.json \
  --output outputs/pickup-reproduction/generated --seed 1200 --attempt-budget 1000

PYTHONPATH="$PWD/src" "$SIM_PYTHON" -m infinidojo.native_pickup \
  --robodojo-root "$ROBODOJO_ROOT" --seed 1200 --max-steps 10000 \
  --layout outputs/pickup-reproduction/generated/layout.json \
  --asset-map outputs/pickup-reproduction/generated/asset-map.json \
  --reference outputs/pickup-reproduction/reference \
  --output outputs/pickup-reproduction/execution --headless --enable_cameras

PYTHONPATH="$PWD/src" .venv/bin/python -m infinidojo.validation \
  --robodojo-root "$ROBODOJO_ROOT" --execution outputs/pickup-reproduction/execution \
  --generation outputs/pickup-reproduction/generated --output outputs/pickup-reproduction/validation.json

PYTHONPATH="$PWD/src" .venv/bin/python -m infinidojo.episode \
  --execution outputs/pickup-reproduction/execution --generation outputs/pickup-reproduction/generated \
  --validation outputs/pickup-reproduction/validation.json --output outputs/pickup-reproduction/episode
```

重新加载使用 `infinidojo.native_pickup --episode /absolute/path/to/episode`，其余运行参数同上。
开发输入构建脚本已验证能恢复本次执行的两份布局及请求，原生资产只记录哈希，不复制进 Git。

## 未完成项

拾取的语言编译、预算内 G1/G3 对照、更广几何/位置覆盖、冻结实验及真实策略成绩尚未完成。
预算执行器对拾取请求明确拒绝，不会误用堆叠配置。策略入口与配对脚本已按 episode
任务选择原生配置，堆叠/拾取配置路由已检查；拾取策略实际执行仍待验证。
`cover_blocks` 仍需要完整覆盖—揭开流程及杯腔物理证据。
