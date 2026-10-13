# 自然语言约束与生成入口

## scene-v2：三个原生任务的统一入口

`infinidojo.route --description` 连接真实 LLM 编译、按需 Infinigen 资产生成、
基于实际任务几何的参考操作、场景补全、物理执行与 episode 导出。
支持完整 `general_pickup`、`stack_blocks`、`cover_blocks`；event 和评分程序由后端固定模板负责。
首条真实拾取描述已跑通，其他声明条件的开发验证正在执行，不能据接口存在就认定全部通过。

当前参数范围：

- 外观：红、蓝、绿、黄、灰五种具名颜色的简单 USD 材质。相同种子和几何参数可只改变颜色；
  不声称已有纹理烘焙、写实材质或 InfiniBench 的视角/遮挡控制。
- 关系：完整占地区域、成对中心距离，以及相对指定任务物体初始包围盒中心的区域。
  相对坐标使用世界 XY 轴；绝对区域与相对区域同时成立，不通过放宽请求获得结果。
- 组合：0–20 个干扰物和一个共同的罐体规格。混合资产类别或逐物体规格尚不支持。
- 操作目标：拾取使用新的 CanFactory 圆罐/圆角方罐，半径参数 1.8–3 cm、高度 5–9 cm，
  右臂目标位置范围 x=12–35 cm、y=-14–10 cm。此范围是请求接口范围，非每个参数组合均可抓取的证明。
  堆叠和覆盖保留原生任务物体、颜色、位置及完整评分语义。

生成顺序是先确定任务物体及几何、取得相应完整参考，再增量放置干扰物。
每个放置提案同时满足请求关系与完整参考运动空间约束。G3 不参与此入口。
未找到参考或布局时明确返回未验证/预算耗尽；不会自动减少物体数、改变任务或修改请求颜色。

```bash
PYTHONPATH="$PWD/src" "$TOOL_PY" -m infinidojo.route \
  --description request.txt --robodojo-root "$ROBODOJO_ROOT" \
  --infinigen-root "$INFINIGEN_ROOT" --sim-python "$SIM_PYTHON" \
  --output outputs/my-scene --seed 1204 --model gpt-6-sol --video
```

沿用现有 Isaac 运行环境设置。`TOOL_PY` 需要已有 Blender/Infinigen 依赖；不要向 Isaac 的
原生 Python 安装不兼容的 USD 包。入口会在独立子进程创建资产。

输入描述需要明确资产尺寸、形状、颜色和场地区域；未支持的要求会明确拒绝。
示例描述及独立评测标签见 [scene-route-development.json](../specs/language/scene-route-development.json)。
标签只在模型调用结束后用于核对，未进入编译提示或反馈。

`--compiled /path/to/scene-v2/compiled` 可继续已经接受的编译结果。它会复核原始描述、
结构化约束、上下文、模型记录与内容哈希，并检查原生模板未改变；不能拿修改过的请求绕过约束。
默认总时限 1800 秒，包含本次语言调用、资产创建、仿真启动、参考获取、补全、执行和导出。
使用既有编译时，原语言成本明确排除并保留其原记录。输出 `route.json` 记录阶段、耗时和最终状态。
只有状态为 `accepted` 的路线才表示在总预算内完成；episode 位于 `generation/episode`。

程序化资产种子固定为场景种子乘 16 后加角色编号（目标 0、杂物 1）；同一场景族的外观变化
保持生成几何种子相同。目标和杂物尺寸/颜色完全相同而无可辨线索的请求在当前接口中拒绝。
操作目标描述包含颜色、形状及尺寸，继续由原生 `<target>` 指令解析。

此接口不支持任意任务规划或新的 cluster 优化算法，也不把物理回放声明为形式可行性证明。

## stack-v1：历史堆叠前端

`frontend --profile stack-v1` 将描述转成现有生成器支持的约束、资产选择和完整任务程序。
它扩展了 v0 拾取前端，复用真实结构化模型调用和有界校验反馈。
当前只支持完整 `stack_blocks` 模板，不能据此声称任意操作任务都能从语言自动规划。

## 输入与能力边界

输入包括自然语言描述，以及从实际原生布局和资产文件生成的 capability context。
context 记录任务物体初始位置、可用资产参数/尺寸、资产来源哈希和原生布局哈希。
这使模型可以换算单位、表达数量/区域/成对间距、列出允许的任务位置，并从可用资产中选择子集。

当前语言接口选择预先生成的 `CanFactory` 资产，不生成任意新形状，也不替换原生任务方块。
未提供的资产类型、连续任务位置范围、其他任务/机器人或与完整原生任务冲突的描述应显式拒绝。
生成失败和语言不支持是不同状态；合法约束也可能在有限生成预算内没有找到有效场景。

模型输出两个有序取放关系。校验器要求符合已实现的任务契约，后端将其展开为 16 个
接近、抓握、搬运、释放等 event，并传递支撑关系。机器人返回与完整原生评分保持不变。
这些 event 是计划约束，不能代替真实抓握、接触、稳定性与执行检查。

## 接入生成流程

先按开发说明生成资产；下面从实际文件生成 context，路径变量与
[开发运行说明](development.md) 一致：

```bash
"$TOOL_PY" - "$RD_ROOT" "$DEV" <<'PY'
import sys
from pathlib import Path
from infinidojo.io import write_json
from infinidojo.stack_specification import make_context
root, dev = map(Path, sys.argv[1:])
context = make_context(
    root / "Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_blocks_0.json",
    [dev / "assets" / str(seed) for seed in (1200, 1201, 1202)],
)
write_json(dev / "language-context.json", context)
PY

"$TOOL_PY" -m infinidojo.frontend --profile stack-v1 \
  --context "$DEV/language-context.json" --request "$DEV/description.txt" \
  --model gpt-6-sol --attempts 3 --timeout 90 --output "$DEV/compiled"

"$TOOL_PY" -m infinidojo.request_run --method g3 --robodojo-root "$RD_ROOT" \
  --sim-python "$SIM_PY" --assets "$DEV/assets/1200" "$DEV/assets/1201" "$DEV/assets/1202" \
  --compiled "$DEV/compiled" --seed 1200 --wall-seconds 240 \
  --max-layouts 4 --total-proposals 4000 --proposals-per-layout 1000 \
  --cluster-proposals 128 --max-steps 20000 --output "$DEV/generated-from-description"
```

只对 `result.json` 中 `accepted=true` 的编译结果执行第二步。
`unsupported` 会保留说明，但不写生成请求或可加载的编译清单。
生成器核对编译产物、原生布局和所选资产哈希，使用模型选中的资产子集，并保留编译来源。
修改编译后的数量或替换资产不能绕过这些检查。

语言阶段与几何生成阶段分别计时。上述 `--wall-seconds` 只约束后者；
不得把它称为包含语言模型和资产创建的总预算。前端记录每次模型调用和总耗时，
成本分析应另报两阶段及资产创建成本。等待人工操作的间隔不是计算成本。

## 检查与实验口径

校验反馈只包含 JSON/能力边界、资产身份和任务语义错误，不包含开发样例的正确答案。
`stack-v1` 禁止将 `--expected` 标签送入修正循环。标签在模型结束后另行评分，
从而区分“通过内部约束检查”和“忠实表达原描述”。
内部校验无法独立保证所有自然语言要求都被正确理解；新增表述和约束仍需忠实度评测。

模型在空工作目录中以只读模式调用，提示要求只返回 JSON；新接口检查事件日志，
若出现工具动作则不接受该次编译。它不需要访问仓库、实验标签或被测策略。
保存输入、提示、原始回答、错误反馈、耗时、源码和产物哈希。

开发描述清单见 [stack-development.json](../specs/language/stack-development.json)，
包含中英文、cm/mm/m、成对/非成对布局、位置选项、已有资产子集，以及明确不支持的要求。
运行：

```bash
"$TOOL_PY" scripts/language_study.py \
  --spec specs/language/stack-development.json \
  --context "$DEV/language-context.json" --output "$DEV/language-study"
```

只有检查过已完成目录与当前输入一致时才使用 `--resume`；不覆盖未完成或不同配置的调用。
首轮答案与最终答案在同一描述上分别评分，单位换算允许 1e-9 m 数值误差，
位置选项及资产子集按集合语义比较。支持/拒绝的解释还需人工抽查。
若实际调用都一次成功，应报告没有触发反馈，而不能用人工注入错误的单元测试证明模型修正收益。
