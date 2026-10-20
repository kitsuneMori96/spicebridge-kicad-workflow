# SPICEBridge → KiCad 电路工作流

一条可重复的链路：**SPICE 网表 → 真实仿真 → KiCad 原理图 → 真实 KiCad 校验**。
仿真走本机 SPICEBridge MCP 服务，校验走真实 KiCad 8（`kicad-cli`），不靠"看起来对"。

![共射放大器示例](docs/ce_amp.png)

> 上图就是 `run.py` 生成的 `.kicad_sch` 渲染结果，可直接在 KiCad 8 里打开编辑。

## 目录

```
spice-projects/
├── run.py                 # 一键：仿真 → 出图 → 校验 → 渲染
├── flow.py                # 多步 MCP 调用（同一 stdio 会话，circuit_id 才有效）
├── mcp_call.py            # 单次 MCP 调用 / 列工具 / 查 schema
├── kicad_build.py         # 自带 KiCad 8 原理图生成器（器件库 + 布线 + 输出）
├── verify_kicad.py        # 权威校验：kicad-cli 网表连通分割比对 + ERC 引脚检查
├── circuits/<name>/       # 一个电路一个目录
│   ├── netlist.cir        #   SPICE 网表（不含分析语句）
│   ├── layout.py          #   原理图版式脚本（输入输出路径见下）
│   └── spec.json          #   要跑哪些分析
├── out/<name>/            # 产物：.kicad_sch / .svg / .png（已 gitignore）
├── docs/                  # 示例渲染图
└── smoke/                 # 回归用例（rc 低通、bjt 共射）
```

## 快速开始

```bash
git clone https://github.com/kitsuneMori96/spicebridge-kicad-workflow.git
cd spicebridge-kicad-workflow
python3 run.py circuits/ce_amp
```

依赖（Ubuntu/WSL）：

```bash
pip3 install --user spicebridge mcp cairosvg      # SPICEBridge 自带 ngspice 调用
sudo apt install ngspice
sudo add-apt-repository ppa:kicad/kicad-8.0-releases && sudo apt install kicad
```

输出：

```
  op : {'v(vcc)': 12.0, 'v(b)': 1.5678, 'v(c)': 6.8334, 'v(e)': 0.7616, ...}
  ac : gain 15.13 dB at 1e7 Hz, dc gain 14.96 dB
PASS: KiCad loads the file, pins are connected, netlist matches
OK -> out/ce_amp/ce_amp.kicad_sch
```

`out/<name>/<name>.kicad_sch` 可直接用 KiCad 8 打开、修改、重新仿真。

## 加一个新电路

1. **写网表** `circuits/<name>/netlist.cir`
   ```spice
   * 分压偏置共射放大器
   VCC vcc 0 12
   V1 in 0 AC 1
   ...
   .model Q2N2222 NPN(BF=200)
   .end
   ```

2. **写版式** `circuits/<name>/layout.py`（照抄 `circuits/ce_amp/layout.py` 改坐标/器件）

   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根目录
   from kicad_build import Schematic

   sch = Schematic("我的电路")
   Q1 = sch.add("Q1", "Q", "Q2N2222", ["c", "b", "e"], 127.00, 66.04, 0)
   R1 = sch.add("R1", "R", "1.5k", ["vcc", "c"], 129.54, 48.26, 0)
   sch.wire((129.54, 35.56), R1.pin("1"))
   sch.connect(R1.pin("2"), Q1.pin("C"))   # 自动拐一次弯
   sch.power_port("power:VCC", 96.52, 35.56)
   sch.label("c", 133.35, 63.50)
   open(sys.argv[1], "w").write(sch.emit())
   ```

   - 坐标单位 mm，全部落在 1.27 mm 栅格上（`snap()` 会强制对齐）
   - `part.pin("1")` 按引脚号取坐标，`part.pin_for("b")` 按网表节点名取坐标
   - 器件位号必须是「字母 + 数字」（`R4`、`R5`），`RE`/`RL` 这种会被 KiCad 判为未标注

3. **写分析** `circuits/<name>/spec.json`
   ```json
   { "op": true, "ac": { "start": 10, "stop": 10000000, "ppd": 20 } }
   ```

4. `python3 run.py circuits/<name>`，必须看到 `PASS`。

## 校验到底校验了什么

`verify_kicad.py` 做两件事，都调用真实 KiCad：

1. **网表连通性**：`kicad-cli sch export netlist --format spice` 让 KiCad 自己算网表，
   再把「哪些引脚同网」与源网表逐组比对。只比较两边都能导出的端子，所以没有 SPICE
   模型的 Q/X 器件不会造成假报错；但**短路、断路、错连一定会被抓到**。
2. **ERC**：`kicad-cli sch erc` 不允许出现 `pin_not_connected`，覆盖第 1 步看不到的
   晶体管 / 子电路引脚。

两条都过才返回 0。CI 里可以直接用退出码。

## 已知限制

- **Q / X 器件不出现在 KiCad 导出的 SPICE 网表里**：KiCad 只导出能映射到 SPICE 原语的
  器件，没有模型的符号会写成 `Q1 __Q1`。它们的连通性由 ERC 保证，而不是网表比对。
- **版式是手写的**：自动布局（`auto_layout`）只适合简单链式电路，复杂电路请写显式坐标。
  这样出来的图才像人画的。
- **KiCad 视图里的 "library not configured" 警告**是正常的：符号已内嵌在文件里，不依赖
  本机库表。
- `spicebridge` 的 site-packages 打了三个补丁（见下），重装会丢失。

## 对 SPICEBridge 的本地补丁

| 文件 | 问题 | 处理 |
|---|---|---|
| `spicebridge/kicad_export.py` | 导出的 `.kicad_sch` KiCad 8 打不开；引脚/标签偏移 2.54 mm；子电路符号名带库前缀 | 修正符号名、栅格、GND/标签偏移；并绕过其导出器 |
| `spicebridge/simulator.py` | spicelib 共享库后端把 ngspice banner 写到进程 stdout，冲垮 MCP stdio 协议 | 默认走子进程后端（输出被捕获），`SPICEBRIDGE_USE_SPICELIB=1` 可切回 |
| `~/.config/opencode/opencode.jsonc` | — | 注册 `spicebridge` 为本地 MCP server |

> 原理图实际由本仓库的 `kicad_build.py` 生成（已验证），`kicad_export.py` 的补丁只用于
> 让原工具在别的场合不至于崩。

## 环境

- ngspice 36 `/usr/bin/ngspice`
- KiCad 8.0.9 `/usr/bin/kicad-cli`（PPA `kicad-8.0-releases`）
- SPICEBridge 1.3.0 `/home/kitsunemori/.local/bin/spicebridge`
- 渲染：`kicad-cli sch export svg` + cairosvg
