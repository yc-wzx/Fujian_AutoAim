# 高速旋转装甲靶：公开源码对照与优化清单

审阅日期：2026-10-24。本地对照基线：`ccee9240fd0cb5c5fa4856a3bec06bc00fed0119`。工况为17 mm步兵、名义3 m、固定中心旋转靶车、4.87 rad/s。本轮阅读8个公开项目的相关模块，记录候选改进，并补齐高速PnP失败的拦截与传播。其他候选方案没有移植、部署或通过实弹验收。按用户要求，不继续仿真优化。

目前新代码没有实车命中率验收。用户提供的历史约30%命中率、粗估320 ms指令至出膛延迟，不能用来计算新版本命中率。下文优先级是依据当前实现和工况作出的工程判断，并非各开源项目之间的效果排名。

## 当前最值得解决的问题

1. **时间基准与物理出弹证据**：当前图像时间仍是收帧时间减固定偏移，IMU也是主机收包时间建历史。已有插值解决了取哪条样本的问题，尚未解决设备时钟、曝光中心与传输延迟的标定。
2. **PnP输入和候选解质量**：高速0.1° yaw细化已经实现，但平移仍来自单个IPPE解，细网格不能消除错误分支、角点偏差或外参误差。
3. **同时优化有效命中与完成时间**：已有分面历史质量、Pitch稳定和请求节奏门控；应识别真正限制吞吐的环节，不能把增加门控或压缩请求间隔直接视为优化成功。

以下数值只由给定工况计算：靶体每1 ms转过约0.279°；10 ms对应约2.79°。若四板均匀分布，相邻装甲板经过同一朝向约间隔322.55 ms，接近粗估320 ms，因此周期性图像可能产生错配一块板的时间歧义，不能据此确认出膛延迟。这个板间周期也不等于发射周期或极限射频。配置6 ms曝光对应约1.67°靶体转角，实际曝光和图像模糊仍须测量；这些都是**靶体相位**，不能直接当成云台Yaw误差。

## 阅读的公开实现

链接固定在本次核验的提交，后续上游更新不会改变本报告的引用内容。

| 项目及提交 | 实际查看的模块与可借鉴之处 | 对当前工程的判断 |
|---|---|---|
| [同济SuperPower / sp_vision_25，bd9f5e7](https://github.com/TongjiSuperPower/sp_vision_25/tree/bd9f5e798fa3c6dd3b483ae6627796afb41c608d) | [Planner](https://github.com/TongjiSuperPower/sp_vision_25/blob/bd9f5e798fa3c6dd3b483ae6627796afb41c608d/tasks/auto_aim/planner/planner.cpp)：对Yaw/Pitch轨迹求受加速度约束的MPC，输出角度、速度、加速度 | 本工程已经有Planner/TinyMPC及另一入口；正式`standard`未接入。需要电控接口与响应模型配合，固定中心工况下优先级低于时序和姿态质量 |
| [rm.cv.fans，6abd1bf](https://github.com/julyfun/rm.cv.fans/tree/6abd1bf359356d1d603766c4b3bcabbb1d6432b2) | [延迟定义](https://github.com/julyfun/rm.cv.fans/blob/6abd1bf359356d1d603766c4b3bcabbb1d6432b2/docs/auto_aim/latency.md)、[LMTD旋转模型](https://github.com/julyfun/rm.cv.fans/blob/6abd1bf359356d1d603766c4b3bcabbb1d6432b2/aimer/auto_aim/predictor/motion/lmtd_top_model.cpp)：拆分曝光、发送、控制、发射、命中时刻，按装甲状态和跟随代价选点 | 借鉴时间定义和反馈关联；其忽略`control_to_fire`的做法有连续发射与已补偿云台响应的假设，不能移植到本工程单包请求流程 |
| [中南FYT2024，dae7547](https://github.com/CSU-FYT-Vision/FYT2024_vision/tree/dae7547a37eaa9f51a663f277bbc6d0668b3c074) | [姿态估计](https://github.com/CSU-FYT-Vision/FYT2024_vision/blob/dae7547a37eaa9f51a663f277bbc6d0668b3c074/rm_auto_aim/armor_detector/src/armor_pose_estimator.cpp)、[重投影边](https://github.com/CSU-FYT-Vision/FYT2024_vision/blob/dae7547a37eaa9f51a663f277bbc6d0668b3c074/rm_auto_aim/armor_detector/src/graph_optimizer.cpp)：多IPPE候选筛选、受约束Yaw优化 | 值得补候选筛选。这里的重投影优化不能描述成已经联合优化平移；移植前还需独立检查候选数、角度单位与退化情况 |
| [南理工Alliance / rmcs_auto_aim_v2，4f43409](https://github.com/Alliance-Algorithm/rmcs_auto_aim_v2/tree/4f4340956a989b0d3ecd4fbd94b1e72a301293cd) | [线性时钟映射](https://github.com/Alliance-Algorithm/rmcs_auto_aim_v2/blob/4f4340956a989b0d3ecd4fbd94b1e72a301293cd/src/io/capturer/linear_sync_model.hpp)、[PnP候选筛选](https://github.com/Alliance-Algorithm/rmcs_auto_aim_v2/blob/4f4340956a989b0d3ecd4fbd94b1e72a301293cd/src/utility/math/solve_pnp/pnp_solution.cpp)、[射击容差](https://github.com/Alliance-Algorithm/rmcs_auto_aim_v2/blob/4f4340956a989b0d3ecd4fbd94b1e72a301293cd/src/module/fire_control/shoot_evaluator.cpp)：相机/板载时钟配对、深度/法向/倾角筛选、物理尺寸转角容差 | 本工况最值得参考的几个接口。时钟拟合需要真实配对事件；射击容差源码是几何判定，并非经过校准的命中概率模型 |
| [SHtech_auto_aim，e0914de](https://github.com/Astra-Whale/SHtech_auto_aim/tree/e0914de82e510a18916fe198e3f846d3c9ba1b5c) | [装甲角点细化](https://github.com/Astra-Whale/SHtech_auto_aim/blob/e0914de82e510a18916fe198e3f846d3c9ba1b5c/detect/armor_corner_optimizer.cpp)：网络关键点引导灯条ROI搜索，配对几何检查和分阶段耗时统计 | 本工程已有类似细化器，目前正式配置关闭。应先验证高速真实图像上的改点效果和失败回退，不能把重新实现同类算法当成新增能力 |
| [天津大学TJURM-2024，3d3ed19](https://github.com/HHgzs/TJURM-2024/tree/3d3ed196f992cbadbc1011aff2f4ade7ea7b0dd8) | [Pointer](https://github.com/HHgzs/TJURM-2024/blob/3d3ed196f992cbadbc1011aff2f4ade7ea7b0dd8/src/threads/pipeline/baseline/tracker/pointer.cpp)、[Locater](https://github.com/HHgzs/TJURM-2024/blob/3d3ed196f992cbadbc1011aff2f4ade7ea7b0dd8/src/threads/pipeline/baseline/tracker/locater.cpp)：网络ROI内灯条配对、灰度端点定位、帧内姿态变换 | 用于比较端点提取与几何一致性；整套流水线替换收益未证实，颜色阈值和物理尺寸不能直接搬用 |
| [Z_LION_AutoAim2025，28aa267](https://github.com/dielivelr/Z_LION_AutoAim2025/tree/28aa267ed706f19fb3991a081b2c9225d6f107c7) | [TimePredictor](https://github.com/dielivelr/Z_LION_AutoAim2025/blob/28aa267ed706f19fb3991a081b2c9225d6f107c7/src/auto_aim/controler/TimePredictor/TimePredictor.cpp)、[策略切换](https://github.com/dielivelr/Z_LION_AutoAim2025/blob/28aa267ed706f19fb3991a081b2c9225d6f107c7/src/auto_aim/controler/ShootStrategy/StrategyPlanner/StrategyPlanner.cpp)：拆分程序/串口/发弹/飞行时间，按运动状态选择跟随、中心与精确打击 | 拆分时间语义有价值；本工程已有三态分类、中心跟随和整车预测，不需要再增加重复状态机 |
| [RobotPilots模型部署库，ccb5dfe](https://github.com/SZURPVision/26_NNDeployment_Lib_and_Detection_Models/tree/ccb5dfe09696edeea445ffb8f99adaa7c383ec3a) | [OpenVINO请求池](https://github.com/SZURPVision/26_NNDeployment_Lib_and_Detection_Models/blob/ccb5dfe09696edeea445ffb8f99adaa7c383ec3a/src/core/algorithm/NNdeployment/src/inference/network_deployment_openvino.cpp)：同步LATENCY与异步多槽位，槽位各自持有输入缓冲 | 本工程已有单请求LATENCY、预分配和最新帧读取。多槽位提高吞吐的同时会增加结果帧龄；需要携带原帧身份，不能只看FPS选方案 |

## 按优先级逐项对照

P0表示应先明确的基础问题；P1表示值得先做的代码或离线验证；P2表示有条件后再评估的方案。下表除明确标注已完成的PnP异常处理外，均为候选方案；正式配置没有更改。

| 优先级 | 环节、当前证据 | 改进方式 | 如何判断是否有收益 |
|---|---|---|---|
| P0 | 相机曝光/IMU时序：[HikRobot](../io/hikrobot/hikrobot.cpp)在`GetImageBuffer`后取主机时间，减`hikrobot_capture_to_exposure_ms`；[XUC](../io/xuc.cpp)用主机收包时间记录IMU | 保留每帧设备tick、曝光、收帧时间、帧号及映射来源；核验设备时间含义后，利用配对触发事件建立设备/MCU/主机时钟关系；同步失效时明确降级 | 用独立同步证据检查偏置、漂移与P95/P99抖动；任意收帧/收包时间的回归不能证明物理曝光已同步 |
| P0 | 指令至出膛320 ms仍是估计；[XUC反馈结构](../io/xuc.hpp)中的`fire_flag`是电流阈值事件，主机请求ID未上协议 | 明确请求、接收、拨弹、实际出膛分别对应什么；保留逐发独立证据与请求关联。分析器[analyze_shot_timing.py](../scripts/analyze_shot_timing.py)已存在 | 区分请求数、实际发数、有效命中；比较实际出膛延迟分布。用户校准延迟与弹速，本报告不改这些参数 |
| P1，已完成基础拦截 | PnP异常路径：审阅基线的[Solver::solve](../tasks/auto_aim/solver.cpp)未检查`solvePnP`返回值；Tracker中两处直接调用，无局部异常隔离 | 现已让高速分支显式返回成功/失败，校验点数、有限值、严格凸四边形、求解结果及全部角点正深度，隔离OpenCV异常；Tracker拒绝失败的初始化/关联候选。新增每帧失败求解次数`high_speed_pose_rejections`，包含身份回退尝试，不是唯一装甲板计数 | 离线验证异常角点不会初始化目标或参与滤波更新，恢复有效输入后可继续更新；原有正常姿态结果和静止/低速路径保持。此项是正确性修复，不等于命中率提升证明；重投影阈值与多解选择仍待另行验证 |
| P1 | 平面姿态二义性：[Solver](../tasks/auto_aim/solver.cpp)只用一个IPPE结果，高速优化只改Yaw、不重估平移 | 先用`solvePnPGeneric`获取候选，按正深度、实际法向/倾角、重投影筛选；有跟踪后再评估连续性代价。歧义未解决时应记录，不能靠历史强行锁错 | 带真值的噪声投影、近正面/斜视/遮挡序列、保留片段验证；比较位置、角度误差及错误分支率，不能只比较像素误差。多解API见[OpenCV官方说明](https://docs.opencv.org/4.13.0/d5/d1f/calib3d_solvePnP.html) |
| P1 | 弹速使用口径：[standard](../src/standard.cpp)接纳反馈5～40 m/s，[Aimer](../tasks/auto_aim/aimer.cpp)接纳5～35 m/s，超出会回退23 m/s；配置入口另有1～50 m/s范围 | 统一“反馈字段可信、数值有效、模型支持”的判断及拒绝原因，明确最终使用值与来源；先审计，不能擅自放宽有效区间或更换实车弹速 | 边界输入和全链路日志一致；例如36 m/s目前可能在入口被接纳后改成23 m/s。没有证据表明实车已经触发此路径 |
| P1 | 角点细化已有[AdaptiveCornerRefiner](../tasks/auto_aim/adaptive_corner_refiner.cpp)，`yolo26_refine_corners:false` | 在高速真实帧上比较网络原角点与细化结果，评估单侧遮挡、过曝、拖影、贴边和端点偏移；保留失败回退。若启用须显式保持静止/低速隔离 | 人工标注的端点误差、PnP/未来预测误差、失检率、P99耗时同时改善；细化成功率高本身不能证明角点更准 |
| P1 | 高速Yaw当前固定±0.2°；[standard门控](../src/standard.cpp)另有可见角、Pitch稳定与历史预测质量 | 先旁路计算距离、装甲朝向和有效区域对应的允许角窗，再结合已测误差分析现有门限；记录哪些候选因固定门限被拒绝 | 使用实际有效区域、弹丸半径和保守边缘裕量，比较有效命中与完整轮次耗时；不直接放宽门限，也不混用装甲外形、灯条间距与有效判伤区域 |
| P1 | 高速请求额外最小间隔0 ms、低电平释放100 ms；`shoot_request_ready`只反映软件命令状态 | 先利用现有首个阻塞原因、请求发送记录及实际出弹标签，分解模型等待、门控等待、通信和机械等待；之后才讨论ready/ack协议与节奏调整 | 比较整轮有效命中、真实耗弹、完成时间、卡弹/漏弹与未完成比例。板间322.55 ms和软件100 ms均不能冒充实际出弹周期 |
| P2 | [固定中心模型](../tasks/auto_aim/fixed_center_model.hpp)已有多帧联合拟合和时间留出验证，输入是PnP的3D观测 | 评估直接用多帧原角点做受约束几何拟合，将中心、半径、相位、角速度与观测重投影一起优化；保留当前模型作对照 | 用后续未参与拟合的片段比较未来位置误差和CPU；同源PnP偏差不应被当成真实轨迹。此项是进一步研究方案，不是声称某校代码已替本工程验证 |
| P2 | [Target](../tasks/auto_aim/target.cpp)已有整车IEKF、Huber、多面批更新；[measurement_noise](../tasks/auto_aim/measurement_noise.hpp)已有随距离/置信度/入射角变化的噪声 | 用留出实测数据校准噪声，检查不同入射角残差、创新相关性和异常值；必要时再评估自适应Q/R或多模型 | 看预测误差和创新一致性；无独立状态真值时不能把内部诊断NEES当成真正的真值一致性检验；不以滤波曲线更平滑作为主要标准 |
| P2 | [历史预测质量](../tasks/auto_aim/face_prediction_residual.hpp)已有同面/相近相位的多次独立过面；[HighSpeedPitchGate](../tasks/auto_aim/high_speed_fire_quality.hpp)已有稳定窗 | 分析冷启动、重建、固定中心模型接管后分别等了多久；针对实际阻塞原因改进模型就绪策略，而非统一缩短成熟等待 | 对照未参与调参的整轮日志；同一相机/姿态链可共享系统偏差，历史残差小不能直接换算命中概率 |
| P2 | 正式链已有中心跟随与Yaw PI；[14字节XUC包](../io/xuc.hpp)只有角度、control和shoot，无法表达完整速度/加速度前馈 | 先测云台响应及跟随误差，再评估前馈或现有[Planner](../tasks/auto_aim/planner/planner.cpp)接入；完整MPC需要对应协议/控制器支持 | 固定中心锁定后跟随中心的角速度接近零，先看是否确有云台瓶颈；用实测跟随误差和完成时间判断，不照搬他队增益 |
| P2 | [YOLO26](../tasks/auto_aim/yolos/yolo26.cpp)已有LATENCY、单stream/请求、预分配；相机已有`read_latest`，识别已有ROI重捕获 | 在目标小电脑测采集到发送的帧龄P50/P95/P99及各阶段耗时，评估颜色转换、拷贝、诊断输出和线程争用；如测试异步，逐槽携带图像与原时间戳 | 相同真实视频/负载下比较端到端帧龄和目标召回；FPS升高但结果变旧不算收益 |
| P2 | 使用用户提供的YOLO26及数字模型；换模型涉及关键点顺序、RGB/BGR、letterbox和类别映射 | 建立同一批实车高速片段上的感知对照，先改失败最多的场景；需要时再评估训练/量化与其他开源模型 | 看关键点误差、当前灯条颜色门控后的召回、错识率和端到端延迟，不凭模型名称或他队mAP更换模型 |
| P2 | [Recorder](../tools/recorder.cpp)已有图像/四元数记录，但`standard`中的调用被注释；现有预测日志和旋转窗口回放不等于完整原始输入回放 | 接通可选、有限队列的实车输入记录：原帧/帧号、时间基准、IMU历史、反馈、配置/模型/固件版本；回放调用正式感知和预测模块 | 同一输入能重现同一目标身份、预测来源与开火资格；记录丢帧数，量录制对帧龄的影响；角点评估保留原图，避免仅用压缩视频作真值 |
| P2 | `standard.cpp`包含采集、模式、预测修正、门控、协议及大段诊断；已存在多份入口 | 在保持单份正式实现的前提下提取时序输入、预测结果与门控接口，增加高速/低速切换、丢失重建和数据失效的集成检查 | 先保证同一输入行为一致，再逐项优化；不能另建一套只在测试入口运行的“改进自瞄” |

## 实施顺序与验收边界

本轮先完成高速PnP失败传播；其余可先离线推进的工作为候选解检查、弹速来源/边界口径审计、帧元数据与版本信息梳理、正式模块回放接口。多解选择与新角点算法先保留对照输出，拿到真实图像后再决定是否正式接管。

需要现场资料才能定量决定的工作：曝光/IMU时间标定、真实逐发出膛延迟与抖动、实际弹速/散布、云台响应、稳定拨弹周期。现有[实测资料说明](field_measurements.md)及离线分析工具继续使用；不从粗估320 ms或他队参数自动推导补偿，不开发未经授权的射后延迟自适应。

验收同时保留每轮实际发数、有效命中数、总耗时、丢目标/卡弹/未完成记录；规则最终完成阈值沿用裁判确认后的口径。当前资料不能支持“上述方案可把实车命中率提高到某百分比”的结论。

本轮修改正式Solver/Tracker及`standard`诊断字段，更新离线检查和审阅报告；未移植上游代码，未改延迟、弹速或开火间隔，未启动实车。高速保护随现有`xuc_high_speed_pose_refinement_enabled`开关在配置的高速Tracker中生效；关闭开关仍沿用原路径。离线边界检查不能代替实车验收。

验证结果：Windows四项回归通过（姿态/坏观测、静止低速隔离、整车预测、弹道）；Linux姿态/坏观测检查与正式入口语法检查通过；Windows共享`auto_aim`库构建通过。姿态测试含216组正常输入、6组排除目标一致性、20个无效输入/变换检查，以及真实Tracker拒绝初始化/关联并恢复更新；没有新增实弹命中率测量。详情见[当前状态](status.md)。
