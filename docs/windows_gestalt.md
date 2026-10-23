# Windows 格式塔构建环境

本机工程位于 `C:\Users\Hasumi\Documents\ChatGPT\RM导航\Fujian_AutoAim`。
依赖和产物只写入 `build/`，构建日志位于 `logs/gestalt-build/`；不修改全局 Python 或系统 PATH。
构建脚本只编译程序并运行不含模型的依赖测试，不连接游戏、相机、串口或电控。

## 固定版本

| 依赖 | 版本 | 用途 |
|---|---|---|
| Windows Python | 3.12 x64 | 项目虚拟环境与 OpenVINO 包 |
| OpenVINO | 2024.6.0 | C++ 推理库及 CPU/GPU 插件，沿用现有工程版本 |
| OpenCV | 4.11.0 | 图像、PnP、DNN、窗口与视频 I/O |
| Eigen | 3.4.0 | 矩阵计算 |
| fmt | 8.1.1 | 格式化，沿用已验证 Linux API 版本 |
| spdlog | 1.9.2 | 日志，使用外部 fmt |
| yaml-cpp | 0.7.0 | 配置读取 |
| nlohmann/json | 3.11.3 | 仿真通信 |

使用现有 MSVC x64 工具链。优先选择该 Visual Studio 安装自带的原生 CMake/Ninja，
避免 PATH 上的 MinGW CMake 缺少 MSVC RC 依赖辅助工具。
OpenCV 的 `vc17` 是安装布局标签；库仍由实际选中的本机 MSVC 编译。
fmt/spdlog/yaml-cpp/OpenCV 统一使用 Release、x64、动态 CRT（`/MD`）。
OpenCV 和自瞄源码使用 UTF-8 编译，支持本机的中文工程目录。
DXGI 采集使用 Windows SDK 自带的 WRL COM 指针，不再要求额外安装 ATL。

OpenCV 只编译本程序需要的 core/imgproc/imgcodecs/highgui/videoio/calib3d/dnn 及其依赖，
Windows 视频后端使用 DirectShow/Media Foundation；没有启用 FFmpeg。
这不影响格式塔的本地共享帧采集。OpenVINO GPU 插件独立于 OpenCV OpenCL 开关。

## 构建

在工程根目录，用 Windows CPython 3.12 执行：

```powershell
python scripts/build_gestalt_windows.py --jobs 8
```

此电脑可明确使用已安装的 3.12 运行时：

```powershell
& 'C:\Users\Hasumi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  scripts/build_gestalt_windows.py --jobs 8
```

脚本会：

1. 从 MSVC 的 `vcvars64.bat` 获取当前进程的编译环境。
2. 在 `build/python` 建立虚拟环境，安装 `scripts/requirements_gestalt.txt` 的固定版本。
3. 下载并校验固定 SHA256 的官方源码，编译安装到 `build/deps/install`。
4. 编译 `build/gestalt.exe` 和 `build/gestalt_dependencies_test.exe`。
5. 将本地依赖 DLL 部署到 `build/`，生成 `build/gestalt-dependencies.json`。
6. 执行依赖测试；不运行 `gestalt.exe`。

重复执行会复用已校验源码和已有构建。首次构建 OpenCV 需要较长时间。
若更换 CMake 路径，脚本会只刷新相应构建目录的 CMake 缓存。

源码版本与 SHA256 固定在构建脚本；Python 依赖版本固定在 requirements 文件。
下载与编译产物均被 Git 忽略，不要提交 `build/` 或模型。

## 不含模型的验证

```powershell
.\build\gestalt_dependencies_test.exe
```

该程序验证：

- Eigen 矩阵求逆、YAML 解析、fmt/spdlog/JSON 链接与运行。
- OpenCV PNG 编解码及已知 3 m 几何的 PnP 数值结果。
- 在内存中构造 `x+1` 网络，分别在可用 CPU/GPU 上推理并检查数值。

它不读取 YOLO，不打开仿真控制链，不产生射击请求。
实际验证结果保存在 `logs/gestalt-build/dependency-smoke.log`。

本机 2026-10-22 验证结果：

| 检查 | 结果 |
|---|---|
| `gestalt.exe` 与依赖测试程序 | MSVC x64 Release 编译、链接通过 |
| PNG 编解码、合成 3 m 几何的 PnP、Eigen/YAML/日志/JSON | 通过 |
| OpenVINO 2024.6.0 CPU 推理数值 | PASS |
| OpenVINO 2024.6.0 GPU 推理数值 | PASS |
| 普通 PowerShell 直接运行依赖测试，无需先加载 MSVC 环境 | 通过 |

模型已于 2026-10-22 接入，三份文件的大小和 SHA256 均与 `docs/artifacts.json` 完全一致。
以上依赖检查本身不包含完整视觉闭环或命中率；后续 Training 实测另见固定旋转靶记录。
现有 `gestalt.exe` 仍包含历史前哨站场景的启动流程；步兵靶车的场景接入与相机身份核验继续按
[固定旋转靶检查记录](gestalt_fixed_target.md) 完成。

## 模型交接

用户提供的配套资源已从 Downloads 校验并复制到工程 `assets/`：

- `yolo26.xml`
- `yolo26.bin`
- `tiny_resnet.onnx`

XML 与 BIN 必须来自同一次导出。当前代码按 16 个红/蓝装甲类别和四角点进行解码，
普通目标检测 YOLO 权重不能直接替换。现有生产资源的大小与 SHA256 记录在 `docs/artifacts.json`。
分类器在 YOLO26 检测器的构造路径中也会初始化，因此三份文件都需要保留。

模型检查命令（从工程根目录执行）：

```powershell
.\build\python\Scripts\python.exe scripts/check_runtime_assets.py --kind model
.\build\gestalt_models_test.exe
```

也可在构建时加 `--check-models` 自动运行该离线检查。它调用生产 YOLO26 预处理、推理和解码，
并调用分类器的 OpenCV DNN / OpenVINO 两条路径，不连接游戏或电控。
当前均已通过：YOLO 输出为 `1×300×18`，三张空白帧均接受 0 个装甲板；
分类器两条路径都将均匀灰色图判为 `not_armor`。
另外，YOLO 与分类模型均通过 OpenVINO CPU/GPU 单张推理的有限数值检查。
分类模型原始批次维为动态维 `?`，单张输入为 `1×1×32×32`，输出为 `1×9`。
证据保存在 `logs/gestalt-build/models-integration.json` 和 `models-smoke.json`。
上述离线检查之外，2026-10-23 已用 Training 四轮步兵的共享帧验证真实场景识别，
并修复桥接 BGR → 生产 RGB 的转换；红色 3 号装甲板可正确识别、跟踪。
持续无发射瞄准、有限弹量诊断和命中统计见 [固定旋转靶检查记录](gestalt_fixed_target.md)。

Windows 仿真需要使用自己的相机内外参及仿真时序设置；不能直接把实车串口或机械偏置当作仿真参数。
后续启动视觉前，继续核验训练场的 3 m 几何、±4.87 rad/s、同帧姿态和装甲类型。

## 官方来源

- [OpenVINO PyPI 安装说明](https://docs.openvino.ai/2024/get-started/install-openvino/install-openvino-pip.html)
- [OpenCV 4.11.0](https://github.com/opencv/opencv/releases/tag/4.11.0)
- [Eigen 3.4.0](https://gitlab.com/libeigen/eigen/-/tree/3.4.0)
- [fmt 8.1.1](https://github.com/fmtlib/fmt/releases/tag/8.1.1)
- [spdlog 1.9.2](https://github.com/gabime/spdlog/releases/tag/v1.9.2)
- [yaml-cpp 0.7.0](https://github.com/jbeder/yaml-cpp/releases/tag/yaml-cpp-0.7.0)
- [JSON 3.11.3](https://github.com/nlohmann/json/releases/tag/v3.11.3)
- [Windows SDK WRL ComPtr](https://learn.microsoft.com/en-us/cpp/cppcx/wrl/comptr-class?view=msvc-170)
