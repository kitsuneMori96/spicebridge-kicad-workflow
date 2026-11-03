# spice-projects：讲解必须被仿真验证

面向电路分析（邱关源体系）+ 模电放大器的教学仓库。**铁律只有一条**：
写进讲解、答案、注释、图里的每一个数字和每一根曲线，都必须来自一次真实 ngspice 运行；
脚本另算一遍解析式做交叉对账，对不上就非零退出。公式推出来的数只能当"期望值"，
不能当"实测值"。做不到就明说是解析值，不要冒充仿真结果。

## 硬规则

1. **工具报错/警告要查到根因，不许绕过去。** `unrecognized parameter - ignored` 是
   "参数名用错了"，不是"这个版本不支持"。先查真名（把候选名逐个喂进去看它报不报错、
   `strings /usr/bin/ngspice` 看参数表），再决定要不要退让。上一轮就是没查，把错误结论
   写进了网表注释和 README，还据此设计了 `ic=` 回填的绕行方案。
2. **仿真必须独立给出 0− 状态。** 换路题用真 `sw` 开关 + 方波控制极（`circ.switch_lines()`），
   让直流初始解自己落在换路前的网络上。手工 `ic=` + `uic` 是把结论喂给仿真再"验"一遍，
   等于没验；只有开关换路本身会污染待测量时才允许，并要写明为什么。
3. **感性开关必须先合后断**（make-before-break，断开那只加 `t_action` 延迟）。先断后合会让
   `L·di/dt` 撞上 `roff`，打出 1e12 V 尖峰把数值解冲垮（实测 `i_L` 从 1.2 A 塌到 3e-5 A，
   而且**不报错**）。重叠窗口内的读数都是过渡值，等它结束再取 0+。
4. **新写的校验要有反例。** 加判据时顺手跑一次"故意做错"的配置，确认它真的 FAIL；
   判据抓不到错误等于给自己发了一张假通行证。
5. **两种独立模型互相对账。** 同一道题至少留两条不相关的路（替代定理的直流解 /
   瞬态实测 / 三要素解析式；戴维南的 Voc·Isc 与加压求流），偏差进容差表才算通过。
6. **写进文档的每条"某写法不认 / 不支持"都要有当场复现。** 复现不了的直接删，不要
   保留成"以防万一"。本文件初稿里那条"SPICE 不认 `e+` 后缀"就是这样被作废的：实测
   `1e+12`、`1e12`、`1e+3k` 在 ngspice-36 的元件取值和 `let` 表达式里都解析正常。

## 本机 ngspice-36 陷阱（细节见 README「已知限制」与 `circ.py` docstring）

- `sw` 压控开关只认 **`ron / roff / vt / vh`**；`onres / von / voff / swon_res / swoff_res /
  cntl_on / cntl_off` 全部 ignored。判据：控制电压 `≥ vt+vh` 导通（先判），`≤ vt−vh` 关断，
  中间**保持原状态**。默认 `vt=vh=0` ⇒ **控制极给 0 V 就是导通**，"开关断不开"不是 bug。
  `.model ... vswitch` 这种类型关键字建不出模型，器件随后报找不到模型 → nan。
- `.tran Tstep Tstop Tstart TstepMax`：第 3 位是**起始时刻**不是步长，填错会整段丢掉 t=0；
  要细分跳变沿得用第 4 位（`circ.tran(..., tmax=)`）。
- `wrdata`：实向量输出两列成对（点号、值），ac 三列；多个向量写进同一个文件列数变 2n，
  极易读错列——**一个向量一个文件**。表达式不认，先 `let`。
- `.control` 里取不到地节点（`v(0)` / `v(#ground)` 都失败），地端只能读单点电位；
  `i(...)` 只有独立源有，电阻要串 0 V 电流表；向量名一律小写（`i(VAM)` → `i(vam)`）。
- `.op` 没有电流向量，直流支路电流得用长瞬态取末值（`circ.dcop()`）。
- `.end` 之后的内容不读，拼 `.control` 前必须摘掉原有 `.end`；首行永远是标题卡，会被吞。
- 元件的 `ic=` 会被直流工作点覆盖，除非 `tran ... uic`。
- 电流源 `I n+ n− val` 的正方向是从 n+ **经过源流向** n−（即从 n+ 抽走电流）；
  照着教材箭头写反了，整个响应差一个负号。
- LTspice 的 `value = {...}` 不是 ngspice 语法；受控电压源写 `E n+ n− nc+ nc− gain`。
- 独立源置零会把开关的**控制源**一起置零 → 开关状态翻车。求 Req 时端口和开关别混用
  同一套改写（`circ.set_value()` 只认 R/V/I 元件，端口元件必须是 R 开头命名的电阻）。

## 怎么跑

```bash
python3 circ.py circuits/<name>        # 戴维南 / 三要素 / 相量 / 谐振，读 spec.json
python3 circuits/<name>/solve.py       # 分步骤讲解型题目（t7_3a、t7_3b …）
python3 run.py circuits/<name>         # 模电放大器闭环（含 KiCad 校验）
python3 tran.py circuits/<name>/netlist.cir out
```

结尾必须是 `RESULT PASS: <name>` 或 `RESULT: PASS`，退出码 0。改完任何 `circ.py` / 共享
模块，上面四条全跑一遍——回归基线是 `circuits/{thevenin,firstorder,rlc,t7_3a,t7_3b}`。

## 加新东西的位置

- 通用电路分析能力 → `circ.py`（真实仿真 + `expect` 容差表 + `checks`/`fail` 那套结构），
  不要写进题目脚本。
- 某道题的解法与讲解顺序 → `circuits/<题号>/`：分步讲解写 `solve.py` + 网表，
  只报数不讲解的写 `netlist.cir` + `spec.json` 交给 `circ.py`。
- 波特图/动画 → `bode.py`、`svg_anim.py`、`scene.py`；原理图 → `kicad_build.py` + 校验。
- 只有本机版本才成立的行为，写进上面的陷阱清单，别只写在注释里——下一个会话读不到注释。
