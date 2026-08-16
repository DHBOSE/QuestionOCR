# 截图转题目 Word（Screenshot2QuestionWord）

一个**本地优先**的 Web 应用：上传题目截图 / PDF / Word 文件，自动识别文字与公式（LaTeX）、裁剪题目插图、配对图注，最终生成**公式可编辑**的 Word（.docx）或 PDF 文件。

为数理化试卷数字化设计：识别出的公式在 Word 中是**原生可编辑公式**（OMML），不是图片、不是乱码 LaTeX 文本。

- 前端：React 18 + Vite + Ant Design 5（浅色主题 + WebGL 丝线背景，全中文界面）
- 后端：Python + FastAPI + [Pix2Text](https://github.com/breezedeus/Pix2Text) + [UniMERNet](https://github.com/opendatalab/UniMERNet) + Pandoc
- 三种识别引擎逐文件可选：**本地**（完全离线）/ **API**（云端多模态大模型）/ **混合**（API 识别文字公式 + 本地版面分析裁剪插图，推荐）

> 界面截图：docs/screenshot.png（待补充）

## 功能特性

### 输入方式

- **题目截图**（png / jpg）：拖拽、点击选择、或直接 **Ctrl+V 粘贴**剪贴板截图
- **PDF 文件**：按页渲染成高清图（180 DPI）走识别管线，支持**页码范围**（如 `1-5,8`，留空全部，单次最多 20 页）
- **Word 文件**（.docx）两种模式：
  - **直转重排**（默认）：Pandoc 直接解析，文字公式**零误差**、秒级完成、不占识别额度，内嵌图片自动提取，按题号自动切分
  - **OCR 识别**：Word 里嵌的是题目截图时，先转 PDF 再逐页识别

### 识别与后处理

- **公式四层管线**：pix2tex 初识别 → LaTeX 清理 → Pandoc(texmath) 校验 → UniMERNet 二次识别 → 图片兜底，保证 Word 中不出现乱码公式
- **一页多题自动拆分**：左侧边条 OCR 找题号切页（兼容乱码题号），可开关
- **选项自动分行**：挤在一行的 A. B. C. D. 选项拆开各占一行（含公式包裹的选项）
- **填空还原**：下划线填空自动还原为统一占位样式
- **插图智能处理**：
  - 版面分析（DocLayout-YOLO）定位插图并裁剪
  - 图注自动配对（甲 / 乙 / 图 1 / 第 N 题图），显示在插图下方
  - 重叠/嵌套检测框去重；**大框未覆盖区域回收**（四宫格插图只检测出部分小图时防丢图），按网格线切分
  - 多题插图混排时按图注题号**跨题重分配**
  - PDF 换页滑出的纯图页自动并入上一题
  - 表格已由 API 输出为 Markdown 表格时跳过重复裁图
- **并行识别**：API / 混合模式最多 3 路并发，本地引擎串行（CPU 计算瓶颈）

### 输出与管理

- **识别预览与编辑**：左编辑右预览实时联动（KaTeX 渲染公式、插图实时显示），确认后再生成文件
- **导出格式**：Word（.docx）/ PDF / 两者都要；PDF 由本机 Word → WPS → LibreOffice 逐级回退转换
- **中文字体**：仿宋 / 黑体 / 楷体 / 宋体 / 微软雅黑 / 等线（默认仿宋）
- **历史任务**：识别结果保留 7 天，可随时重新下载或改格式再导出，无需重新识别
- **API 用量统计**：token 用量本地累计，API 设置弹窗可查

## 快速开始（Windows）

### 一键安装（推荐）

克隆仓库后，双击项目根目录的 **`一键安装.bat`**，自动完成全部部署：

1. 检查 Python（需 3.10+）
2. 创建虚拟环境 `backend\.venv`
3. 安装 CPU 版 PyTorch 与全部 Python 依赖（含 UniMERNet 公式引擎，首次约 5-15 分钟）
4. 构建前端 `frontend\dist`（需 Node.js 18+）
5. 检查并可选自动安装 Pandoc

安装结束后可直接选择启动程序。

### 一键启动

完成首次安装后，双击项目根目录的 **`启动.bat`**：
服务以**无窗口方式**后台静默启动（单进程，8000 端口同时提供页面和 API），并自动打开浏览器。
停止服务双击 **`停止服务.bat`**；运行日志见 `backend/backend.log` 与 `backend/console.log`。

### 手动安装

#### 1. Python 依赖（Python 3.10+）

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
# 建议先装 CPU 版 PyTorch（体积小很多）：
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
# UniMERNet 声明的 transformers 版本与本项目冲突，需单独 --no-deps 安装：
pip install unimernet==0.2.3 --no-deps
```

> 已知兼容组合：torch 2.5.1+cpu / torchvision 0.20.1 / onnxruntime 1.20.1 / transformers 4.46.3。
> 新版 torch / onnxruntime 在部分机器上 DLL 初始化失败，建议按此组合安装。

#### 2. 模型与组件下载（因体积未包含在仓库中）

| 组件 | 大小 | 获取方式 |
|---|---|---|
| Pix2Text 模型 | 约 1-2GB | **首次识别时自动下载**（保持网络畅通即可），之后完全离线 |
| UniMERNet-small 权重 | 约 810MB | 下载官方 `unimernet_small.pth` 与配置文件，放入 `backend/models/unimernet_small/`（缺省时自动跳过二次识别，不影响主流程） |
| Pandoc | 约 220MB | 任选：`winget install pandoc`（系统安装，推荐）/ 放入 `backend/pandoc/pandoc.exe`（便携版）/ macOS `brew install pandoc` / Ubuntu `apt-get install pandoc` |

#### 3. 构建前端

```bash
cd frontend
npm install
npm run build        # 生成 frontend/dist（改了前端代码才需要重新构建）
```

#### 4. 启动

```bash
cd backend
python api.py        # 或 .venv/Scripts/python api.py（Windows）
```

浏览器打开 **http://localhost:8000** 即可使用。

> 开发调试前端：`npm run dev`（5173 端口，已配置接口代理到 8000）。

## 使用说明

1. **上传题目文件**：截图 / PDF / Word 可混传；PDF 可填页码范围；Word 可选直转或 OCR 模式
2. **选择识别引擎**（每个文件独立）：
   - **本地**：完全离线，免费但慢（每张约 20-60 秒，纯 CPU）
   - **API**：云端大模型识别，快且文字公式质量高；插图坐标可能不准
   - **混合**（默认推荐）：API 识别文字公式 + 本地版面分析裁剪插图，兼顾质量与准确
3. 点「开始处理」，实时查看进度与日志
4. 在**预览编辑区**检查修改每题内容（公式用 `$...$` 行内公式语法）
5. 点「生成 Word 文件 / PDF」，自动下载

### API 设置

选择 API / 混合引擎时，点击「API 设置」填写服务商配置。预设支持
**qwen-vl-max（阿里云百炼，首选）/ GLM-4V / 豆包 vision / Gemini / 自定义**，
全部为 OpenAI 兼容端点。密钥只保存在浏览器 localStorage 与本任务内存中，**不落盘、不上传**。

## 项目结构

```
Screenshot2QuestionWord/
├── 启动.bat / 启动.vbs       # 一键无窗口启动（GBK 编码，勿用 UTF-8 编辑器保存）
├── 停止服务.bat / stop_server.ps1
├── backend/
│   ├── api.py                # FastAPI：/upload /process(SSE) /finalize /download /history /usage
│   ├── recognizer.py         # 本地识别：Pix2Text + 公式四层管线 + 插图检测框去重/回收
│   ├── api_recognizer.py     # API 识别：OpenAI 兼容协议，多服务商预设
│   ├── converter.py          # Markdown 生成：选项分行 / 填空还原 / 图注配对 / 跨题重分配
│   ├── splitter.py           # 一页多题自动拆分（左侧边条 OCR 题号检测）
│   ├── doc_loader.py         # Word / PDF 文件直识别（pandoc 直转 / 页渲染）
│   ├── docx_builder.py       # Pandoc 调用 + 中文字体后处理（styles.xml / theme1.xml）
│   ├── pdf_builder.py        # docx → PDF（Word / WPS / LibreOffice 回退链）
│   ├── models/unimernet_small/   # UniMERNet 权重（需自行下载，见上）
│   ├── pandoc/               # Pandoc 便携版（可选，需自行放置）
│   └── requirements.txt
└── frontend/                 # React 18 + Vite + AntD 5
    └── src/components/       # Uploader / FileList / QuestionPreview / HistoryPanel / WebThreads ...
```

## 已知边界

- 本地识别为 CPU 推理，复杂题目触发 UniMERNet 二次识别会更慢
- UniMERNet-small 偶有 `\iota`/`l` 混淆；可换 UniMER-Base（4.9GB）提升质量
- 无「第 N 题图」标注的多题混排插图无法自动归属（人工在预览里调整即可）
- 双栏选项排版（A/B 一行、C/D 一行）本地 OCR 可能串行，混合/API 模式更稳
- PDF 单次最多处理 20 页（防整本上传跑半小时）

## 致谢

- [Pix2Text](https://github.com/breezedeus/Pix2Text) —— 版面分析 + 文字 OCR + 公式初识别
- [UniMERNet](https://github.com/opendatalab/UniMERNet) —— 公式二次识别
- [Pandoc](https://pandoc.org/) —— Markdown ↔ docx 转换（公式 ↔ OMML）
- [React Bits](https://reactbits.dev/) —— WebThreads WebGL 背景
