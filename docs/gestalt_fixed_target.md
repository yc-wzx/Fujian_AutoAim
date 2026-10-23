# 17 mm 固定旋转靶：仿真接入检查与命中率验证

> 2026-10-24：当前源码新增正式高速姿态细化和共享预测有效性检查，已重新构建仿真程序。下文命中统计均属于各自冻结的历史版本；当前提交未重新进行有弹量仿真，不能沿用这些数字宣称效果。具体改动及离线验证见 [当前状态](status.md)。探针共用Solver和解校验，其他射击资格仍不等同正式 `standard` 全链路。

检查日期：2026-10-22～23。源码基线：`ec29c1194e763519def2e0b5490a89b6e74aa113`（`fujian/main`）。
本机工程：`C:\Users\Hasumi\Documents\ChatGPT\RM导航\Fujian_AutoAim`。

当前已完成接口诊断、Training 自定义旋转、模型/依赖接入、持续视觉瞄准及有限弹量射击诊断。
射击统计与未完成的几何验收分别记录；诊断结果不直接代表比赛工况或实车命中率。
本次增加仿真诊断、构建脚本及依赖测试；未修改正式配置或实体电控，未提交或推送。
实车需要提供的数据、测量定义及离线请求/出膛汇总见 [实测资料说明](field_measurements.md)。

## 工况和规则

用户确认：17 mm 步兵；固定中心旋转靶；距离 3 m；角速度幅值 4.87 rad/s。
PDF 附件 A 确认 3 m，但旋转速度、方向、相位仍标为待定；4.87 取自用户要求。
装甲板数量、尺寸、旋转半径、高度和距离测量基准仍需与实际官方靶一致，不能由前哨站配置推定。

规则第 1.2.2/1.2.4 节：17 mm 配额 50 发，初始 310 血，每次有效受击扣 10 血；
第 3.1 节却写“30 次有效命中”。按前述算式需要 31 次，二者有冲突。
验证中保留出膛、受击和剩余血量原始值，击毁以最终确认的裁判设置为准。
本任务优先比较“50 发内有效击毁比例、耗弹和用时”，再比较命中率；比赛总时限还包含往返。

## 本机实测结果

### 2026-10-23：固定旋转中心的预测约束

> 本节记录的是 10-23 当时的实验版本。`26.10.23` 分支基于 `3910ede`（Release 26.10.22），
> 主线已用更完整的 `with_fixed_center_prediction()` / `fixed_center_model.hpp` 取代该实现，
> 因此本分支不携带这里的 `AimGeometry::whole_ekf_fixed_center` 与 `gestalt_prediction_test`，
> 探针和启动器也不再提供 `-FixedCenterPrediction`。四轮 200 发的对照结论仍然有效。

新增显式选项 `-FixedCenterPrediction` / `--fixed-center-prediction`，默认关闭，
仅在 CenterHold 仿真跟随中允许。Aimer 的新几何选项只在显式高速时序、有效的四板非前哨刚体几何、
角速度至少 3 rad/s 时，把预测副本的三个中心平移速度设为零；中心位置、转动和装甲几何仍来自视觉跟踪。
原 Tracker 状态不改，不读取模拟器靶位置/转速构造瞄准点。
共享 Aimer/Target 增加可选接口，实车调用未启用该枚举；默认路径和配置保持原样。

Windows 编译和 `gestalt_prediction_test` 通过：原跟踪状态不变、固定中心与旋转保留、
13 个静止/低速/其他时序用例与原路径一致、8 个实际高速解算点与约束后的未来几何一致。
探针新增中心速度和是否实际启用约束的诊断字段，避免把开关请求当作模型已生效。

仿真重开后的两次零发启动失败保留为 `trial-center-pilot-01`、`02`，
分别是新实例未创建命中统计项、场景恢复了人工控制及底盘移动模式；不是未命中的弹丸。
原生出膛及伤害均为零时显式初始化并观察受击计数，不能重置已使用的统计量。
启动器补上此零计数保护；bench 接管会恢复此前已使用的 `IsAI=1`、`MoveMode=0`，
防止人工控制/底盘跟随与外部瞄准互相影响。FOV 实测仍为 25°，不把启动失败归因于视场变化。
第三次 10 发检查计划和失败记录在 `center-pilot-plan.json` 及 `center-pilot-protocol-events.jsonl`，
检查样本不进入后续固定弹量比较；是否采用以独立的完整对照结果为准。
第三次检查实际出膛 10 发、有效受击 8 次，原生计数/伤害/HP/零余弹和另行逐发复算通过；
满足预登记的至少 8 次条件后才进入 `center-paired-plan.json` 的关闭、开启、开启、关闭四轮各 50 发比较。
所有轮次均关闭判伤期间装填，其他补偿、弹速、相位门和控制设置相同，采用标准与前一组一致。
四轮已全部完成，逐轮原生审计和不导入原统计模块的逐发复算均通过：

| 固定中心预测 / 轮次 | 有效受击 / 出膛 | 第 31 次有效受击对应出膛回执 / s |
|---|---:|---:|
| 关闭 1 | 50/50 | 62.76 |
| 开启 1 | 48/50 | 87.96 |
| 开启 2 | 48/50 | 80.92 |
| 关闭 2 | 46/50 | 91.84 |

两组均为 **96/100**；时间中位数开启 **84.44 s**、关闭 **77.30 s**，开启反而慢约 **9.2%**。
未通过预登记采用条件，**继续默认关闭固定中心预测和判伤期间装填**；不宣称命中率或完成时间已提高。
关闭组两轮差异也较大，不能把单轮 50/50 或最快用时当成稳定保证。
这说明该预测约束在当前场景里未取得预设收益，不能据此推断固定中心假设在其他完整标定系统中的效果。
本轮保留的实际修复是重启后 bench 控制模式恢复、仅在零发/零伤害时初始化缺失统计项、状态文件读取重试，
实车参数没有改动，新增预测模式未接入实车默认调用。

证据：`center-paired-plan.json`、`center-paired-summary.json`、`center-paired-manifest.json`，
以及 `center-paired-count-audit.json/.csv`。新游戏进程 12840 / 端口 26505 的统计区间为 10→210，
200 个唯一出膛编号无重复/缺口，共 192 次有效受击；试运行的 8/10 单列，零发失败保留。
前一组实验在另一游戏进程，不能把两个计数区间直接连接；两项对照共 400 发、功能检查共 20 发，按设置分别报告。

### 2026-10-23：判伤期间准备下一发的对照

新增仿真诊断选项 `-PrepareDuringSettle`（原生 `--prepare-during-settle`），当前默认关闭。
上一发实际出膛回执到达后，仍等待 300 ms 判伤；等待期间仅在新鲜视觉/模型合格时准备下一发。
必须先结束上一发结算、看到单发库存、再通过原相位和姿态门限才允许下一次发射。
不在最后一发或可能达标的最后一次受击期间预装，原生击毁/预算/达标结束均清空余弹。
延迟、弹速、俯仰、相位门限和实车配置未修改。

状态机从原探针移至唯一实现 `io/gestalt/shot_trial.hpp`，探针和离线测试共用它。
`gestalt_shot_trial_test` 使用假属性与虚拟时钟，验证延迟伤害仍归属上一发、结算前不发第二发、
预算/命中目标/原生击毁清空余弹、默认路径隔离、无效准备、异常额外出膛及库存超时。
Windows 构建及这些离线检查通过。启动器读取动态 JSON 时增加空值/部分写入重试。

10 发功能试运行 `overlap-pilot-01` 为 9 次有效受击，9 次下一发准备发生于上一发判伤期间；
原生出膛/受击/两侧伤害/HP 和停止后零余弹核对通过，另行独立复算也通过。
这是功能检查，不纳入性能对照。后台启动器最初被子进程继承的管道阻塞，
该轮满 10 发后只停止已完成的跟随进程以关闭管道，靶旋转保留，原生后验核对通过；
随后驱动器改为文件重定向，事件保存在 `overlap-pilot-protocol-events.jsonl`。

正式对照预登记在 `logs/gestalt/overlap-paired-plan.json`：关闭、开启、开启、关闭，
每轮 50 发，同一程序和参数，仅切换准备选项；每轮补回诊断靶 10000 HP，结束后做 3 s 原生计数和转速检查。
采用条件为完整审计全部 200 发、开启组达到 31 次有效受击的出膛回执时间中位数至少缩短 5%，
开启组有效比例至少 90% 且比关闭组低不超过 3 个百分点；小样本结论只适用于当前仿真。
当前比较结果见 `overlap-paired-summary.json`，结束时冻结清单为 `overlap-paired-manifest.json`。
四轮已经完成并通过逐轮原生审计及独立逐发重算：

| 选项 / 轮次 | 有效受击 / 出膛 | 第 31 次有效受击对应出膛回执 / s |
|---|---:|---:|
| 关闭 1 | 45/50 | 62.22 |
| 开启 1 | 47/50 | 52.46 |
| 开启 2 | 47/50 | 70.36 |
| 关闭 2 | 47/50 | 55.78 |

开启组 94/100，关闭组 92/100；时间中位数 61.41 / 59.00 s，开启组反而慢约 4.1%，
未通过预设采用条件，保持默认关闭。两发比例差不能证明命中率提高，也不选择性只报 52.46 s 的快轮。
装填等待减少，但重新获取、预测无效及相位门等待存在波动，不能据局部等待减少推定端到端提速。
本轮实际出膛区间为 10→210，200 个逐发编号无重复/缺口，共 186 次有效受击；
两种设置分组统计，10 发试运行未混入。
独立计数复核：

```powershell
.\build\python\Scripts\python.exe scripts/audit_gestalt_counts.py --plan logs/gestalt/overlap-paired-plan.json
```

新训练回合已经重置出膛/伤害累计量，不能与之前 482 发的计数区间直接拼接。
输出中的装填时间只描述请求和库存观测，不是机械装填完成真值；真实离膛/碰撞和正式装甲有效区仍未验收。

### 2026-10-23：原始计数复核及结果边界

针对命中率偏高的疑问，新增 `scripts/audit_gestalt_counts.py`，不导入原统计模块，
直接读取最终逐发列表及原始 WS 属性包，重做 HP 前后相减、两侧伤害、受击计数和出膛编号核对。
五轮重复验证仍为 **228/250（91.2%）**，快速两轮仍为 **94/100（94%）**。
相应原生伤害增量为 4560 / 1880，原生每次扣 20，分别对应 228 / 94 次；
未用 PDF 的每次 10 点去除原生伤害，没有因此将命中数翻倍。

审计覆盖的连续计数区间为 0→482，逐发编号共 482 个且无重复或缺口；
原生命中计数从 248→694，差值 446 与逐发扣血事件合计一致。
这些不同设置的试验仅用于查漏，不合并成一个性能结果。
慢模式实际保留 93/99，其中一轮行政中断于 49 发；31 次达标即停的 31/33 单独列出。
第五轮先前的零发获取失败记录保留，不能据命中率声称每次启动都能顺利捕获。

这里的比例是当前仿真辅助程序通过相位、姿态、图像时龄等门限后，
实际发出弹丸的模拟器有效受击比例。逐发列表与另开进程的 WS 订阅都来自同一游戏引擎，
不是两套独立的弹着真值。尚未取得逐弹碰撞对象、录像弹着或正式装甲判伤的独立核验，
也没有验证高血量目标、规定姿态旋转及名义 3 m 几何与现场完全一致。
仿真辅助程序的补偿参数为 200 ms，实车粗估的指令至出膛约 320 ms；
参数与测量定义尚未对齐，不能按这两个数直接换算命中率。
因此，这些计数核对通过仍不能说明实车已由约 30% 提高到 90% 以上。

同时修复统计模块的潜在分母问题：有未结算发射时不再给出整轮 `hit_rate`，
只另列已结算部分的比例；检查直接 HP 相减和连续出膛编号。
上述已公布的固定弹量样本均已全数结算，此修复不改变它们的结果。
复核输出为 `logs/gestalt/independent-count-audit.json` 及逐发 `.csv`：

```powershell
.\build\python\Scripts\python.exe scripts/audit_gestalt_counts.py
```

### 2026-10-23：快速发射对照

在五轮固定参数验证后，加入仿真专用的提前装填、可配置判伤等待和 31 次有效命中停止条件。
保持 200 ms、+1.5°、CenterHold、±8° 门限及模型不变，使用同一修复版程序按慢、快、慢、快顺序比较。
慢模式等待 1300 ms 且只在开火资格满足时开始装填；快模式等待 300 ms，
在视觉/模型/时龄合格时先准备一发，实际发射仍须通过原相位、姿态误差和图像时龄门。
判伤等待至少覆盖当前解算飞行时间加 100 ms；每次仍只允许一发，按实际出膛计数确认。

| 模式 / 轮次 | 有效命中 / 实际出膛 | 第 31 次有效命中对应出膛回执 / s |
|---|---:|---:|
| 慢 1 | 45/49 | 170.05 |
| 快 1 | 49/50 | 82.13 |
| 慢 2 | 48/50 | 162.82 |
| 快 2 | 45/50 | 85.37 |

快模式合计 **94/100（94%）**；慢模式 **93/99（93.94%）**。
对应时间中位数为 **83.75 s / 166.43 s**，本次小样本对照中缩短约 **49.7%**。
数据没有证明命中率提高，也不保证其他帧率/硬件/场景下仍有相同提速。

后续离线排查将这 100 发逐发关联到每个请求的首个 `fire=1` 帧，全部找到对应。
6 次未命中的请求帧时龄为 64.30～88.84 ms，命中请求帧范围为 61.41～120.40 ms；
未命中中也有模型预测相位对齐约 -1.38°、-1.84° 的请求，并非全部位于 ±8° 门限边缘。
这不足以证明漏弹由旧图像或门限过宽导致；请求之后指令仍会变化，真实出膛/碰撞时刻未知。
下一步优先核验时序和弹着，不能据此直接缩窄门限并宣称收益。
明细为 `logs/gestalt/fast-miss-diagnostics.json`；两份逐帧流各有一条不完整 JSON，
已记录行号并排除，100 个请求的关联仍完整；射击计数复核使用完整的最终逐发记录。

时间从首个射击请求到“之后被确认为第 31 次有效命中”的出膛回执，
不是精确命中时刻，不包含初始化、停止确认、往返或完整比赛用时。

原计划四轮各 50 发。慢 1 的监控脚本在读取状态文件短暂为空时误判退出，
在已经完成第 31 次命中终点之后，行政停止于实际 49 发，原始记录完整保留。
因此不能把这组描述为完整的 200 发验证；速度终点均已完成，实际统计为 199 发。
后续监控对空值和部分读取进行重试。所有四轮均在任何血量重置前，
用独立的 3 s 属性订阅核对累计出膛、碰撞计数、己方伤害、靶受伤和 HP，全部一致，
确认停止装填后没有额外出膛，且无未计入的迟到伤害。

证据位于 `logs/gestalt/speed-paired-plan.json`、`speed-paired-summary.json`、
`speed-paired-manifest.json`、`speed-paired-protocol-events.jsonl`；逐轮记录为 `trial-speed-*`。
离线重算：

```powershell
.\build\python\Scripts\python.exe scripts/gestalt_speed_report.py --plan logs/gestalt/speed-paired-plan.json --output logs/gestalt/speed-paired-summary.json
```

共同保留的控制修复只用于仿真固定中心靶：接管后恢复名义摆位和初始俯仰；
不执行无效、未收敛、不可信或预测点明显偏离当前旋转体几何的指令。
被拦截时保持上次有效指令并禁发，诊断字段 `aim_validation` / `rejected_aim_frames` 可核查。
实车 `standard.cpp`、实车补偿及高速最小请求间隔没有因该试验而修改或部署。

规则阈值流程（须已有高血量诊断靶，仍只控制本机仿真）：

```powershell
.\scripts\run_gestalt_live.ps1 -Action trial -TrialName rule-threshold -ShotBudget 50 -SettleMs 300 -Prearm -HitGoal 31
```

`HitGoal=31` 按经过 HP 和两侧伤害确认的有效受击计数，虚拟规则血量为 `max(0,310-10×命中数)`；
达到目标或弹量上限后停止装填/发射，保留视觉跟随。该设置不是把原生每次 20 的伤害改为 10，
也没有执行返程、关闭真实摩擦轮等完整比赛流程。达到命中目标即停的样本不混入固定弹量命中率比较。

本机流程实测 `trial-speed-goal-31`：**33 发实际出膛、31 次有效命中**；
从首个射击请求到控制器确认达标 **74.79 s**，随即停止装填/发射，虚拟规则血量为 0。
独立 3 s 订阅中实际出膛累计固定为 482（包含此前所有试验）、双方余弹均为 0；
本轮 HP/两侧伤害均减少或增加 620，原生靶剩余 9380，实测转速 4.87716 rad/s。
证据为 `logs/gestalt/speed-goal-31-manifest.json` 及对应 plan、summary、逐发/逐帧和 postflight 文件。
该次按目标停止的 31/33 结果没有加入前述 94/100 的固定弹量统计。

全部对照完成后，启动器的 `trial` 默认改为 `-SettleMs 300 -Prearm`，默认 `HitGoal=0`、预算 5 发；
需要慢速诊断时显式传 `-SettleMs 1300 -Prearm:$false`。按 31 次目标停止须显式传 `-HitGoal 31 -ShotBudget 50`。
默认 `start` 始终只跟随、不装填/发射；最终已恢复持续旋转和无发射视觉跟随。

### 2026-10-23：五轮重复验证与实车评估

五轮已完成：**228/250，91.2%**，逐轮为 43、42、47、50、46 发有效命中。
每轮均在第 31～36 发累计达到 31 次有效命中。比例的 Wilson 95% 描述区间约为
87.0%～94.1%，逐轮范围为 84%～100%；区间的独立发射假设不覆盖连续相关失误。

| 轮次 | 有效命中 / 实际出膛 | 第 31 次有效命中的发数 | 首请求至对应出膛回执 / s |
|---|---:|---:|---:|
| 1 | 43/50 | 36 | 285.03 |
| 2 | 42/50 | 35 | 198.29 |
| 3 | 47/50 | 33 | 169.19 |
| 4 | 50/50 | 31 | 88.05 |
| 5 | 46/50 | 34 | 160.30 |

回执时间不是精确离膛/碰撞时刻，以上用时不含初始化、往返或正式裁判结算。
原始逐轮记录为 `trial-repeat-fixed-01`～`05`；完整汇总、冻结文件哈希和运行事件分别在
`logs/gestalt/repeat-fixed-summary.json`、`repeat-fixed-manifest.json`、`repeat-fixed-protocol-events.jsonl`。

预先登记 `logs/gestalt/repeat-fixed-plan.json`：再做 5 轮，每轮 50 发，共 250 发。
所有轮次保留，参数固定为 24.5 m/s、200 ms、+1.5°、CenterHold、±8°，
同一可执行文件、模型和正式配置；此前 48/50 不混入本次前瞻性汇总。
每轮重新启动视觉跟随，靶持续规定姿态旋转；轮间把靶 HP 恢复到 10000，以测完 50 发。
另按每次有效命中扣 10 血，记录达到 31 次命中的发数，属于离线规则阈值分析。
这是命中诊断，没有运行官方裁判系统的 310 血归零、停止射击和返回全流程。

第二轮最初无检测、实际出膛为 0，恢复己方名义摆位和朝向后开始射击；
后续各轮也在开始前恢复相同摆位。监控超时从 7 分钟延长至 12 分钟，
这些运行操作保存在 `repeat-fixed-protocol-events.jsonl` 及对应场景遥测中，瞄准参数未变。
第五轮原零出弹尝试在一条异常俯仰指令后丢失目标，随后共享帧中断；界面控制亦被用户停止。
该尝试的全部原始记录保留为 `trial-repeat-fixed-05-acquisition*`，恢复后以同一冻结版本补完第五轮。
因此上述命中率是实际出膛子弹的统计，不能把初始化可靠性也当作 100%。

最终汇总由离线脚本生成，原始文件哈希在任何后续快速模式修改之前已经核对并保存：

```powershell
.\build\python\Scripts\python.exe scripts/gestalt_series_report.py --plan logs/gestalt/repeat-fixed-plan.json --output logs/gestalt/repeat-fixed-summary.json
```

用户确认：实车约 320 ms 是粗略估计的指令到弹丸出膛时间，历史约 30% 命中率也是
3 m、4.87 rad/s 固定旋转靶，使用海康相机。尚缺仪器测量的逐发时延/波动、历史样本量、
弹丸散布及实际弹速分布。
本地正式配置为海康、150 FPS、曝光 6 ms、高速预测补偿 338 ms；配置不能代替现场测量。
这些数值没有因仿真测试而改写或部署到实体机器人。

本次仿真使用专门的中心保持和相位开火门，不能把 +1.5°/200 ms 的仿真经验补偿直接移植到实车。
320 ms 的平均值本身不能确定命中率；在 4.87 rad/s 下，时延误差 10/20 ms 对应
2.79°/5.58° 车体相位误差。以半径 0.25 m 为示例，切向位置变化约 12.2/24.3 mm。
3 m 平面靶上 0.5° 指向变化约为 26.2 mm，说明相机/云台零偏和外参也必须实测。
这些是几何敏感度示例，不是额外跑出的实车命中结果。

汇总采用 [NIST 所述 Wilson 区间](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)，
只作独立同概率发射假设下的仿真比例描述；另列轮次范围，区间不覆盖连续漏弹和场景差异。
[ICRA 2018 仿真迁移研究](https://xbpeng.github.io/projects/SimToReal/)指出建模误差会影响迁移；
该论文的任务与本项目不同，不能拿其结果推算本车命中率。
当前缺少配对实车试验，新增实车命中率在报告中保留为空；规划时暂沿用用户报告的历史 30%，
不宣称新方案实测仍为 30%，也不宣称已达到仿真比例。

以下数学表只说明对可靠击毁所需命中率的要求：假定每发独立且命中概率恒定，
50 发内至少 31 次命中的概率，70% 单发命中率约为 91.52%，75% 约为 98.61%，80% 约为 99.91%。
连发相关失误、靶面灵敏度和全流程限时均不在此模型内。这些不是实车预测值。

### 2026-10-23：此前单轮独立 50 发验证，48/50

先以 20 发探索取得 19/20，再冻结参数，另做完整 50 发验证。
`trial-pitch15-holdout-50` **实际出膛 50 发，有效命中 48 发，命中率 96%**；
相较首轮 39/50（78%），本轮增加 9 次有效命中、提高 18 个百分点。
全部 50 发的命中计数、靶 HP、己方伤害与靶受伤记录一致，总扣血/伤害 960，
没有需要排除的零伤害命中计数。第 31 发累计达到 31 次有效命中。
这是本场景的一轮独立验证，不能视为长期稳定命中率或正式比赛击毁成绩。

| 当前启动器默认参数 | 数值 |
|---|---|
| 瞄准与开火 | 视觉 EKF 靶心方向保持；未来板法向 ±8° 窗口 |
| 弹速 | 解算 24.5 m/s；实测中位数 24.35 m/s |
| UE 俯仰指令偏置 | **+1.5°**，仅当前仿真经验补偿 |
| 高速预测附加延迟 | 200 ms，仅当前仿真经验补偿 |
| 检测和几何适配 | 原配套模型、角点细化、quad scale 1.192 |
| 代码 | 复用 Tracker 已解出的观测，避免诊断重复解算 |

本轮处理耗时中位数 36.11 ms、P95 49.18 ms；帧时龄中位数 79.01 ms、P95 94.34 ms。
独立遥测复核转速 4.87363 rad/s。冻结记录包含源码、配置和可执行文件的 SHA256，
参数在该 50 发期间未更改。证据：

- `logs/gestalt/pitch15-holdout-50-plan.json`：验证前登记的参数及文件哈希。
- `logs/gestalt/trial-pitch15-holdout-50.json` 及 `.frames.jsonl`：逐发与逐帧记录。
- `logs/gestalt/pitch15-holdout-50-summary.json`：完整成绩及判伤一致性检查。
- `logs/gestalt/pitch15-postflight.jsonl.summary.json`：结束时的独立转速复核。
- `logs/gestalt/pitch15-holdout-50-manifest.json`：最终结果、文件哈希及恢复后的运行状态。

验证后已将启动器默认偏置从 +2° 改为 +1.5°，恢复持续旋转和不发射的视觉跟随。
`start` 不装弹；有限弹量测试仍须显式使用 `trial`。
相机到靶心名义约 3 m，炮口测距和相机外参未独立验收；当前为规定姿态旋转，
不是电机动力学。模拟器原生伤害仍为每次 20，严格相机契约仍未通过，
上述参数没有写入实车 `configs/standard.yaml`。

### 2026-10-23：继续优化与开源对照

查阅了以下原始项目及其文档：

| 项目 | 与当前问题相关的内容 | 本轮采用方式 |
|---|---|---|
| [同济 sp_vision_25](https://github.com/TongjiSuperPower/sp_vision_25) / [Planner](https://github.com/TongjiSuperPower/sp_vision_25/blob/main/tasks/auto_aim/planner/planner.cpp) | 未来射击轨迹、云台可跟随轨迹、未来误差开火门 | 借鉴时序和窗口设计，继续复用当前估计/弹道链 |
| [交大 LMTD](https://github.com/julyfun/rm.cv.fans) / [时延文档](https://github.com/julyfun/rm.cv.fans/blob/main/docs/auto_aim/latency.md) | 陀螺建模、降自由度姿态解算、分段时延与信号对应关系 | 检查现有 yaw 优化及预测时域；现有 Solver 已包含固定倾角的 yaw 优化 |
| [南理工 rmcs_auto_aim_v2](https://github.com/Alliance-Algorithm/rmcs_auto_aim_v2) | 带时间戳的采集/IMU、组件接口、录制/回放工作流 | 作为后续工程化接入参考 |

本轮没有复制这些项目源码或更换用户提供的模型。仅比较设计思想；跨项目的演示数字不作为本工况性能依据。
同济查阅时的 tree SHA 为 `bd9f5e798fa3c6dd3b483ae6627796afb41c608d`。

已保留的代码修正：

- 仿真诊断复用 `Tracker::last_associations()` 中已解出的观测及初始化帧结果，
  删除进入 Tracker 前重复的 IPPE / 140 次 yaw 重投影搜索。检测器、Tracker 和弹道算法保持原流程。
- 状态文件保留最后完整的逐发统计；后续共享帧中断时增加错误字段，不再覆盖掉已完成的成绩。
  原 `trial-holdout-50.json` 曾被帧中断错误覆盖，已从逐帧日志恢复其原始 39/50 记录，并保留原错误。
- 统计有效命中要求靶 HP、己方伤害及靶受伤一致。单独增加 `BulletHitTotal`、但三项判伤均为零的事件
  列入 `non_damaging_hit_counter_shots`，不计有效命中；不推定其具体碰撞原因。
- 增加 `-FireWindowDeg` 和离线 `--frame-stats`，便于逐项检验未来相位窗口和处理耗时。

本轮探索记录（每组独立计出膛配额）：

| 候选 | 有效命中 / 实际出膛 | 处理 |
|---|---:|---|
| 当前旧版复测，200 ms、+2° | 14/20 | 对照 |
| 多帧相位拟合 | 14/20 | 未见优势，试验代码已移除 |
| 平滑共同高度俯仰 | 13/20 | 未见优势，试验代码已移除；原始命中计数 14，排除 1 次零扣血事件 |
| 175 ms、±8° | 17/20 | 小样本探索 |
| 175 ms、±4° | 13/20 | 未采用 |
| 150 ms、±8° | 7/20 | 未采用；排除 1 次零扣血事件 |
| 185 ms、±8° | 11/20 | 未采用 |
| 新代码、175 ms 的独立 50 发 | 32/50 | 未复现 17/20，不替换原延迟；原始命中计数 34，排除 2 次零扣血事件 |
| 新代码、200 ms、+1.5°、±8° | 19/20 | 探索后冻结参数 |
| 相同参数的独立 50 发 | **48/50** | 已设为仿真启动器默认值 |

175 ms 独立轮次的处理耗时中位数为 36.73 ms、P95 50.00 ms；本轮旧版 20 发对照为
38.52 ms、54.15 ms。处理耗时下降本身不代表命中改善；最终采用的 200 ms、+1.5°
组合另经独立 50 发验证，结果见上节。不能把这一组合的收益全部归因于某一个代码修改。
对应证据保存在 `logs/gestalt/*-summary.json` 和 `refined-timing-comparison.json`。

### 2026-10-23：首轮固定参数独立 50 发验证，39/50

首轮 `trial-holdout-50`：**实际出膛 50 发，装甲命中 39 发，命中率 78%**。
每发的 `BulletHitTotal`、靶 HP、己方 `DamageAppliedTotal`、靶 `DamageTakenTotal` 全部一致；
总扣血/伤害 780。此轮没有继续调参；前面的探索轮次不计入这 50 发。
第 39 发累计达到 31 次命中；该数字仅说明命中次数，不宣称已按正式判伤完成击毁。

| 首轮仿真设置（历史对照） | 数值 |
|---|---|
| 目标和运动 | HACHILLES 四轮步兵，固定中心；规定姿态旋转 +4.87 rad/s |
| 距离 | 相机到旋转中心约 3.00 m；炮口到靶测距基准尚未验收 |
| 检测/解算 | 用户配套 YOLO26、tiny_resnet；生产角点细化；quad scale 1.192 |
| 瞄准 | 视觉 EKF 靶心方向保持；预测板法向 ±8° 开火窗口 |
| 弹速 | 解算 24.5 m/s；本轮实测中位数 24.36 m/s |
| UE 俯仰指令偏置 | +2°，当前仿真的经验补偿 |
| 高速预测附加延迟 | 200 ms，当前仿真的经验补偿；不能当作已分解的真实发射延迟 |
| 伤害/靶 HP | 实际每次 20；MaxHP/初始 HP 10000，用于命中诊断 |

探索轮次：零偏置基线 0/5；CenterHold、+2°、0 ms 为 1/5；
CenterHold、+2°、250 ms 为 5/10；CenterHold、+2°、200 ms 为 8/10。
随后固定最后一组参数，取得上述 39/50 独立结果。
这些试验带有单发装填、遥测确认及 1.3 s 判伤等待，不能把用时当作比赛全流程成绩。
另有 3 发判伤校准中 2 次命中，尝试设置伤害倍率后仍各扣 20，随后已恢复该倍率原值。
PDF 的每次 10 血、官方靶几何、炮口测距以及 flags7/arm0 严格相机契约仍未验收；
本轮不是正式比赛成绩，也没有把仿真补偿写入实车 `standard.yaml`。

验证结束后两个后台进程继续运行，己方不再装填/发射，保持视觉瞄准。
独立 3 s 遥测复核转速 4.87156 rad/s；己方实际出膛累计稳定在 104（包含此前校准），
实弹量和允许弹量均为 0，外控模式仍为 90。对应证据：

- `logs/gestalt/trial-holdout-50.json`：逐发记录及当前跟随状态。
- `logs/gestalt/holdout-50-summary.json`：50 发汇总与一致性检查。
- `logs/gestalt/holdout-postflight-check.json`：停止发射、余弹与累计计数复核。
- `logs/gestalt/holdout-camera-check.json`：相机位置、同帧身份及未通过的严格几何门。

后续验证已将默认俯仰偏置更新为 +1.5°；默认 `start` 仍只瞄准。回到探索基线时显式使用
`-CenterHold:$false -PitchBiasDeg 0 -SimDelayMs 0`。

### 2026-10-23：有限弹量射击诊断入口

默认 `start` 仍不发射。已有持续旋转场景中，可以显式开始一个有限弹量诊断：

```powershell
.\scripts\run_gestalt_live.ps1 -Action trial -TrialName fixed-target -ShotBudget 5
.\scripts\run_gestalt_live.ps1 -Action status
.\scripts\run_gestalt_live.ps1 -Action stop
```

`trial` 保留靶的旋转进程，替换己方视觉控制进程。每次仅装填一发，收到实际
`BulletFiredTotal` 增量后停止扳机、清空余弹，按 `SettleMs` 等待后记录靶 HP 和己方累计伤害；
当前启动器默认 300 ms，旧的逐发诊断使用 1300 ms。
新增版本同时记录独立的 `BulletHitTotal` 和靶 `DamageTakenTotal` 增量。
实际出膛达到预算或显式设置的有效命中目标后继续视觉瞄准，不再装填；失去新鲜检测时发送 fire=0。
试验开始及结束均不打开物理相机、串口或实车主控。
已有未完成射击诊断或同名结果时启动器拒绝覆盖。结果路径由 `status` 返回，
逐帧记录位于结果文件名加 `.frames.jsonl`，不要只查看默认 `live-follow.json`。

诊断参数：`-BulletSpeed`（m/s，默认 24.5）、`-PitchBiasDeg`（UE 世界俯仰指令偏置，默认 +1.5）、
`-SimDelayMs`（只覆盖仿真运行缓存的高速延迟，默认 200）、`-CenterHold`（默认启用，仅针对固定中心旋转靶）。
`-FireWindowDeg` 默认 8°。`CenterHold` 使用视觉 EKF 估计的靶心方向，预测板法向距靶心方向小于该窗口且
云台误差、图像时龄和跟踪状态合格时才请求单发；靶车真值不参与瞄准或开火判定。
这些是仿真诊断开关，没有接入 `standard.cpp` 的实车主线。

模拟器 `BulletSpeed=2450 cm/s`，实测首发 `ShooterRealSpeed=2428 cm/s`；
旧无发射诊断使用 20 m/s，射击诊断已显式改为 24.5 m/s。
模拟器首次确认命中的靶 HP 减少 20、己方伤害增加 20，不能按 PDF 的每次 10 血换算实测命中率。
初始单发及随后 5 发基线均无命中；CenterHold 的零偏置 5 发和 −2° 5 发也无命中；
+2° 5 发有 1 次 20 血命中，+3° 10 发有 1 次 20 血命中。这些是探索轮次，不能据此认定最优参数。
为防止校准时靶被击毁，后续探索将靶 MaxHP/HP 设为 10000；不将该轮次描述为比赛击毁测试。
请求到出膛计数更新约 0.24–0.32 s，只是本地遥测接收间隔，不能直接当作真实发射延迟。
相机仍是 flags3/arm−1，名义 3 m 的炮口外参未验收；严格主程序门限保持原样。

离线核对当前或已完成的结果，不控制游戏：

```powershell
.\build\python\Scripts\python.exe scripts/gestalt_trial_report.py logs/gestalt/trial-pitch15-holdout-50.json --frame-stats --output logs/gestalt/pitch15-holdout-50-summary.json
```

### 2026-10-23：持续旋转、持续瞄准

当前 Training 使用两辆 HACHILLES 四轮步兵（`66000009`），蓝方 pid 0 为己方，红方 pid 92011 为靶。
靶车持续按固定位置/旋转姿态运行；独立遥测窗口实测约 4.870 rad/s。
己方通过生产 YOLO26 → Solver → Tracker → Aimer 连续下发云台指令；所有指令固定 `fire=0`。
不再用限时姿态诊断代替持续瞄准。靶的位置/转速控制和视觉估计分属两个进程，靶车真值不送入视觉估计器。

修复了仿真 BGR 图像直接进入生产 RGB 检测器的问题；红色 3 号装甲板已能正确识别。
仿真运行缓存从 `configs/standard.yaml` 派生，只覆盖相机几何、敌方颜色、机械零偏、
仿真角点适配和本次无发射跟随的时序；正式配置及实车算法未改。
已启用生产角点细化，并显式复用历史桥的 `sim_quad_scale=1.192`，此数值仍需独立几何标定。
某段约 91.5 s 的记录中，视觉角速度中位数约 −4.887 rad/s；符号由 UE / 视觉世界坐标系反射决定。
该段估计靶心 x 中位数约 3.119 m；不能据此认定炮口到靶的 3 m 已验收。
相机仍报告 `arm=-1, flags=3`；无发射跟随检查 publisher、player/map、view/target、epoch 和连接代际，
没有改动 `gestalt.exe` 的严格 flags7/arm0 验收门，也不将当前跟随当作射击验收通过。

在已有运行中的 Training 场景、上述两辆车和 `.wsport` 文件均正确时，从工程根目录执行：

```powershell
.\scripts\run_gestalt_live.ps1 -Action start
.\scripts\run_gestalt_live.ps1 -Action status
.\scripts\run_gestalt_live.ps1 -Action stop
```

启动器在后台启动持续旋转和持续瞄准两个进程，重复启动会拒绝；`stop` 创建
`logs/gestalt/live.stop`，两进程检查后退出，瞄准进程发送 fire=0 并归还外控 lease。
持续运行日志写入文件，内存仅保留有限时间窗。实际转速见
`logs/gestalt/live-target.jsonl.summary.json`，云台状态见 `logs/gestalt/live-follow.json`，
逐帧记录见 `logs/gestalt/live-follow.json.frames.jsonl`。
`live-processes.json` 记录实际进程号。当前靶位置为 `(-132,300,-95) cm`，
相机位置约 `(-430,300,-60) cm`，属于名义 3 m 摆位，炮口外参和规则测距基准仍未验收。
已实测停止入口：两进程退出，己方 TargetMode 恢复为 1，实际出膛计数仍为 0；随后重新启动持续运行。
证据见 `logs/gestalt-build/live-stop-check.json`。速度前馈的顺序诊断没有形成改善证据，
当前保留位置目标跟随；候选结果保存在 `follow-feedforward-summary.json`，不据此作命中率结论。

下面保留早期接口诊断；PID/端口属于当时实例，不是固定配置。

| 项目 | 观测 | 结论 |
|---|---|---|
| 安装包 | `v0.1.9.137-preview`，Shipping；游戏 SHA256 `9e093b853aefed16060efe34ae911c78f2fccb7231af98e17ccad404ad38c850` | 外部桥接接口存在 |
| 回环 WS | 启动后 `.wsport` 为 19564，成功订阅属性 | 控制/遥测传输可用；下次启动重新读端口 |
| 共享帧 | 协议 v3，writer PID 14712，1280×720，frame id/QPC 持续增长 | 图像数据面可用；PID 每次重启变化 |
| 相机 | `rgbCamera.applySettings` 后元数据 FOV 从 90° 变为 25° | FOV 设置有效 |
| 步兵相机契约 | 取景实验靶时 `arm_cm=-1`、`identity_flags=3`；现有严格门要求吊臂 0、flags 低三位全为 1 | **未通过**；不能删门绕过，需在实际 shooter 上查明 |
| mode 99 | WS 调度、日志和属性证明 move=99；实际 ChassisYaw 不呈稳定 4.87 rad/s，且正式开赛会补入其他 AI | **未通过固定靶场景验收** |
| ForcedSpin | 前端枚举证实 `IsForceSpin=10000019`、`ForcedSpinSpeed=10000020`；原值 3140。试写 4870 后仍未测出稳定 ±4.87 | 单位/与 AI 的优先关系尚不明，不把 4870 当已生效速度 |
| Training 自定义旋转 | 两辆步兵；对实验靶按时间发送固定位置、旋转姿态。实际 yaw 拟合为 +4.87095 / −4.87056 rad/s，RMS 1.27° / 1.26°；每轮去掉 1 s 预热后约 6.97 s | 正反转速度通过；这是规定姿态的运动，不包含底盘电机动力学 |
| 位置 | 此轮属性流未发布 WorldPosX/Y/Z，`RBNavLab status` 有位置；取景帧另有相机位置 | **3 m 实际射距未验收**；底盘中心间距也不能代替炮口到靶的射距 |
| 模型/构建 | Windows OpenCV 4.11.0、OpenVINO 2024.6.0 等已安装，`gestalt.exe` 编译通过；三份模型已接入且 SHA256 与清单一致，CPU/GPU 单张推理和生产解码/分类离线检查通过 | 依赖及模型就绪，见 [Windows 环境](windows_gestalt.md) |

早期 mode 99 诊断清理结果记录在 `cleanup.jsonl.summary.json`；之后在 Training 重建了 pid 92011 的步兵靶。
两轮自定义转速实测时 `MatchStatus=1`，下一次接入应重新读取游戏状态。
原始证据在 `logs/gestalt/`：`camera-frame-smoke.json` 记录严格相机检查，
`force-spin-steady.jsonl.summary.json` 记录原生强制旋转不达标，
`custom-spin-positive.jsonl.summary.json` 和 `custom-spin-negative.jsonl.summary.json` 记录自定义转速通过。

官方接入依据：[桥接仓库](https://github.com/ChangeClock/sp_vision_25_gestalt_system_bridge)、
[Shipping 调用契约](https://github.com/ChangeClock/sp_vision_25_gestalt_system_bridge/blob/main/docs/ai-cookbook-command-guide.md)。
`console.exec` 返回 `success/dispatched` 只证明命令已调度，效果必须看遥测和共享帧。

## 命中率优化的优先顺序

1. **先建立正确场景。** 隔离其他 AI；实际测量中心不平移、两种旋向均为 ±4.87 rad/s；
   以炮口/实际相机外参核对 3 m，并核对四板/半径/板高等模型。mode 99 默认值不作为验收依据。
2. **标定发射链时序和弹速。** 统一曝光时间、处理时龄、命令发送、实际出膛和碰撞时间；
   `Aimer::aim(to_now=true)` 已包含图像处理时龄和弹道飞行时间，不要再次相加。
   `configs/standard.yaml` 的高速 338 ms 是现有实车补偿，不能据此认定仿真有相同延迟。
3. **复用当前生产流程再比较。** `src/gestalt.cpp` 虽共用 Detector/Solver/Tracker/Aimer，
   开火仍走旧 `Shooter` 与 `tongji_*` 门；没有执行 `src/standard.cpp` 的最新高速分面残差、
   模式时序选择及 XUC 请求节奏。旧仿真跑高分不能直接说明当前实车主线改进有效。
4. **每次只改一个因素并配对验证。** 先保持当前高速整车 EKF 基线；取得实测延迟后，
   在其附近做小范围仿真扫描，再检查选面、法向入射角与门限。只在高速路径比较，
   静止/低速沿原流程。预先登记参数与测试顺序，候选须用独立轮次验证。

具体敏感度（车体旋转相位，不是云台 yaw 误差）：

| 量 | 4.87 rad/s 下的结果 |
|---|---:|
| 一圈时间 | 1.290 s |
| 若四块板等间隔，过面间隔 | 0.323 s |
| 多预测 10 ms | 多 2.79° 相位 |
| 3 m、20 m/s 的直线飞行近似 | 150 ms，期间旋转 41.9° |
| 3 m、24.5 m/s 的直线飞行近似 | 122.4 ms，期间旋转 34.2° |
| 两种弹速的飞行相位差 | 7.69° |

以上飞行值只是说明敏感度，正式使用实际板位、重力/阻力模型和解算出的飞行时间。
`src/gestalt.cpp` 在弹速遥测无效时回落到 24.5 m/s，而正式配置为 20 m/s；应先测清再统一。
338 ms 基础补偿本身对应约 94.3° 车体转角，不是“瞄准提前 94.3°”的直接云台角指令。
高速 1000 ms 最小请求间隔会限制吞吐，但它是已有节奏约束；不能在没有实际单发/受击证据时直接取消。
旧仿真 `simulation/README.md` 的 76% 针对前哨站；不用于本工况的性能承诺。

## 诊断工具

需要 Node.js 22+ 和 Windows Python，无第三方 Python 包。
这两个脚本不会连接相机、串口、主控板或真实发射机构。

### 启动游戏

使用已安装的发行物，在项目根目录执行。启动参数已在本机验证，进入单机地图后为 prep。
不依赖未随包发布的 `ai-match-selftest.ps1`，也不猜测其 AutoStart 参数。

```powershell
$gameRoot = 'C:\Program Files (x86)\Steam\steamapps\common\GestaltSystem'
$logFolder = Join-Path (Get-Location).Path 'logs\gestalt'
New-Item -ItemType Directory -Path $logFolder -Force | Out-Null
$gameLog = Join-Path $logFolder 'fixed-target-game.log'
$gameArgs = @('-externalvisioncontrol','-automationcontrol','-visionbridge',
  '-wsbind=127.0.0.1','-windowed','-ResX=1280','-ResY=720',('-abslog="' + $gameLog + '"'))
Start-Process -FilePath (Join-Path $gameRoot 'Gestalt_System.exe') `
  -WorkingDirectory $gameRoot -ArgumentList $gameArgs -WindowStyle Hidden
```

已有本次游戏实例时不要重复启动。本任务界面路径：主菜单 → Single Player → Training → F 进入。
裸 `-externalvisioncontrol` 有效；不要改成 `-externalvisioncontrol=1`。

### 订阅属性和验转速

```powershell
node scripts/gestalt_probe.mjs --port-file logs/gestalt/fixed-target-game.log.wsport `
  --seconds 5 --output logs/gestalt/probe.jsonl

# 靶车已正确生成并稳定后，验收负向转速；正向改成 =4.87。
node scripts/gestalt_probe.mjs --port-file logs/gestalt/fixed-target-game.log.wsport `
  --seconds 8 --player 92011 --expected-omega=-4.87 `
  --output logs/gestalt/spin-check.jsonl
```

转速门默认 ±0.10 rad/s，仅作调试验收公差，非组委会已发布公差。
脚本用消息接收时间拟合展开 ChassisYaw，发现大于 0.4 s 的间隙就分段；
至少 2 s/10 点，角度拟合 RMS 不超过 5°，并满足指定旋向/速度才接受。
该时间轴不能用于曝光或离膛延迟标定。位置数据缺失会输出 `null`，不补成零。
`--command` 可显式发送并记录仿真控制台命令，但不保证命令生效；默认只订阅。

### 验共享帧

先用 `.wsport` 对应监听进程的 PID，不能使用 launcher PID：

```powershell
$wsPort = [int](Get-Content logs/gestalt/fixed-target-game.log.wsport -Raw)
$listener = Get-NetTCPConnection -State Listen -LocalPort $wsPort
python scripts/gestalt_frame_probe.py --pid $listener.OwningProcess --seconds 2 `
  --output logs/gestalt/frame-probe.json

# shooter 接管后才要求严格契约；失败时退出非零，需修复后继续。
python scripts/gestalt_frame_probe.py --pid $listener.OwningProcess --seconds 2 `
  --require-takeover --player 0 --output logs/gestalt/takeover-check.json
```

帧脚本只读已存在的共享内存，不创建或改变游戏帧流。
严格模式同时要求同一 writer、递增 frame/QPC、固定非零 takeover epoch、正确 player/map/view identity、
1280×720/FOV25/实际 armLength=0。数据面可读与完整接管可用分别输出，不能混为一项。

## 后续闭环验收

模型已放入 `assets/`，文件及预期 SHA256 在 `docs/artifacts.json`：
`yolo26.xml`、`yolo26.bin`、`tiny_resnet.onnx`。Windows 仿真不需要真实相机 SDK。
本机 Windows 构建与不含模型的依赖测试已通过，步骤见 [Windows 格式塔构建环境](windows_gestalt.md)。
合成几何的 3 m PnP 测试不代表训练场实际射距已经验收。

先完成无发射的检测/跟踪/转速/取景身份检查，再做仿真单发标定。
车辆的默认伤害与省赛 10 HP 不同：`bench_damage_per_hit` 只是统计除数，改它不会改变游戏伤害。
应先用隔离单发确认真实伤害/受击事件，再映射到省赛计分，归零时注意最后一发扣血被截断。
不能将 `fire_cmds` 或心跳包数当作弹丸数；实际出膛用 `BulletFiredTotal(63000002)` 差值。
受击须有独立事件或经过单发验证的伤害依据，补血/复活/他车伤害混入的回合不能作有效命中统计。

基线与候选各至少做正反转 5 轮、每轮不超过 50 发，控制初相位/环境一致并交错测试顺序。
同时报告所有尝试、场景验收失败轮数、实际发弹、有效命中、50 发内击毁比例和用时；
没有实际出膛的回合单列，不能伪装成 0% 或从报告中消失。

本次已通过：原有 Aimer 的 105 个夹具、双滤波几何/旋向/弹道迭代/三模式时序隔离测试，
以及 6 项高速离线分析测试；诊断脚本完成现场 WS/帧读取与无效转速拒绝检查。
这些测试不等于视觉闭环或实弹命中率。
