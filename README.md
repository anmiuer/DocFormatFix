# DocFormatFix

DocFormatFix 是一个 Word / Markdown 文档格式整理工具。它识别文档中的标题、正文、参考文献、表格、图注/表注、列表、引用、公式与代码块等结构，按所选模板统一字体、字号、首行缩进、行距、段前段后与对齐；只调整排版，不修改正文文字。

- 项目地址：https://github.com/anmiuer/DocFormatFix
- 版本：1.0
- 许可证：MIT

## 项目状态

- 本项目使用 AI 工具辅助开发，代码与文档可能包含 AI 生成内容。
- 作者不保证持续维护或升级该项目，项目按现状提供；如遇问题，欢迎自行 Fork 修改。

## 最近更新

- 新增 `start.bat` 与 `check_env.bat`：Windows 启动不再依赖 VBScript，自动查找可用的 Python 并输出环境诊断。
- 修复环境检测：缺少 `tkinterdnd2` 时只回退 Windows 原生拖放，不再弹出“环境缺失”对话框。
- 新增公式保留回归测试：验证 Word 分式、上标、根式、矩阵等 OMML 结构，以及 Markdown `$...$` / `$$...$$` 公式不会被破坏。
- 统一 Windows 启动脚本换行格式，补充下载 ZIP、VBScript 被禁用等启动排查说明。

## 用途

- **Word 文档排版校准**：识别 `.docx` 中的标题层级、正文、参考文献、图注、表注等，按模板统一格式。
- **Markdown 结构规范化**：整理 `.md` / `.markdown` 的标题空格、块间空行，同时保留表格、代码块、公式与列表结构。
- **批量处理**：支持单文件、文件夹、多文件拖入，一次处理多篇文档。
- **安全处理**：执行前强制备份原始文档；只修改排版，不改动文字内容。
- **可视化复核**：预扫描后展示识别结果与原文，可手动合并、拆解、保护或改判段落。
- **模板与格式要求**：内置 3 套模板，也可可视化编辑、保存模板，或粘贴自然语言格式要求自动识别。

## 安装与启动

要求 Python 3.9+，主要面向 Windows。

Windows 推荐双击 `start.bat`。它不依赖 VBScript，会自动查找 `.venv`、`pyw.exe`、`pythonw.exe`、`py.exe` 或 `python.exe`；找不到 Python 时会停留并显示原因。

- `DocFormatFix.vbs`：可选的无窗口入口，需要系统启用 VBScript。
- `启动DocFormatFix.bat`：兼容入口，内部调用 `start.bat`。
- `check_env.bat`：启动诊断，打印 Python 路径、版本并执行 `launcher.py --check`。

启动器会检查 Python、依赖与项目文件，缺失时可自动下载安装或选择本地 Python 环境。

手动安装与调试：

```bash
pip install -r requirements.txt
python launcher.py --check
python gui.py
```

`tkinterdnd2` 用于拖拽增强；Windows 自带原生拖放，缺少该依赖时会自动回退，不会阻止启动，也不会弹出环境缺失对话框。macOS / Linux 没有 `.bat` / `.vbs` 入口，请直接运行 `python3 gui.py`；如果 `tkinterdnd2` 安装失败，可只安装 `python-docx`，程序仍可运行。

### 启动失败排查

1. 双击 `start.bat`，观察是否弹出 Python 未找到或启动报错提示。
2. 运行 `check_env.bat`，确认 Python 路径、版本与 `launcher.py --check` 输出。
3. 查看 `logs/launcher.log`，启动器会把环境检查和 GUI 异常写入该文件。
4. 如果从 GitHub 下载 ZIP 后运行时被 Windows 拦截，右键文件 → 属性 → 解除锁定，或在终端运行 `python gui.py`。
5. 部分 Windows 11 版本默认禁用 VBScript；此时请使用 `start.bat` 或 `python gui.py`，不要依赖 `DocFormatFix.vbs`。
6. 也可以在项目目录直接运行：

```bash
python launcher.py --check
python gui.py
```

## 使用

### GUI

1. 选择【单个文件】或【整个文件夹批量处理】，也可以把多个 docx / md 文件或文件夹直接拖入窗口。
2. 选择排版模板；如需调整输出格式，点击【格式设置…】。如果已有现成的格式要求文本，点击【识别格式要求…】粘贴后自动识别；需要重命名、复制、删除或导入模板时，点击【管理模板…】。展开【高级选项】可关闭自动推断标题层级、调整置信阈值（60~90）以及启用附加清理（连续空段落 / 修订痕迹与隐藏文字 / 多余空格）。
3. 点击【预扫描文档】。该步骤只读取文档，不会修改文件。
4. 在【格式识别与原文】中查看识别结果：单击格式可高亮同类段落；相邻同类型段落默认合并显示，可拖选后【拆解所选】；也可【合并所选】、【保护所选】或【改判为】其他类型。
5. 确认后点击【执行格式化】。程序先备份原文，再输出到指定目录，并生成 TXT 报告；勾选后会同时生成 HTML 报告。
6. 如需还原，点击底部【恢复备份】选择历史任务。

Markdown 文档只做结构规范化，不套用 Word 字体模板。

### CLI

```bash
# 批量处理文件夹（docx / md 混合也可以）
python main.py --dir ./docs --template competition_report

# 处理单个 Word 文档并生成 HTML 报告
python main.py --file ./论文.docx --template undergraduate_thesis --html

# 处理 Markdown 文档
python main.py --file ./笔记.md

# 自定义备份与输出目录
python main.py --dir ./docs --backup-dir D:\backup\docformatfix --output-dir D:\output\docformatfix

# 调整置信阈值、关闭标题推断并启用附加清理
python main.py --file ./论文.docx --threshold 75 --no-infer --cleanup spaces revisions

# 查看历史备份 / 恢复最近一次
python main.py --list-backups
python main.py --restore latest
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--file FILE` | 处理单个文件，可重复 |
| `--dir DIR` | 处理文件夹，可重复 |
| `--template NAME` | 选择排版模板 |
| `--threshold N` | 置信阈值 50~100，默认读取配置（70） |
| `--no-infer` | 关闭标题推断，仅使用 Word 原生标题样式 |
| `--html` | 生成 HTML 报告 |
| `--backup-dir DIR` | 自定义备份目录 |
| `--output-dir DIR` | 自定义输出目录 |
| `--cleanup ITEM...` | 附加清理：`merge_empty`、`revisions`、`spaces` |
| `--list-backups` | 列出历史备份 |
| `--restore BACKUP_DIR` | 恢复备份，`latest` 表示最近一次 |
| `--version` | 显示版本 |

CLI 阈值范围为 50~100；GUI 滑块范围为 60~90。

CLI 没有预览和人工覆写能力，只使用自动识别结果。

### 输出位置

- 文档输出：`<output_dir>/<相对路径>/<文件名>_formatted.docx|md`
- TXT 报告：`<output_dir>/reports/docformatfix_<时间戳>.txt`
- HTML 报告：同一 `reports` 目录下的 `.html`（勾选时生成）
- 备份：`<backup_dir>/<YYYYMMDD_HHMM>/`，含 `manifest.json`

## 配置文件

GUI 与 CLI 默认读取 `config/settings.json`：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `confidence_threshold` | `70` | 置信阈值；CLI 范围 50~100，GUI 滑块为 60~90 |
| `default_template` | `competition_report` | 默认排版模板 |
| `html_report` | `false` | 是否默认生成 HTML 报告 |
| `backup_dir` | `backup` | 备份目录，相对项目目录或绝对路径 |
| `output_dir` | `output` | 输出目录，相对项目目录或绝对路径 |
| `cleanup` | 三项均 `false` | `merge_empty` / `revisions` / `spaces` 附加清理 |

- GUI 的【目录设置…】会把 `backup_dir` / `output_dir` 写回该文件；其余字段目前作为启动默认值读取。
- 手动修改配置文件后，GUI 会自动重新读取并同步到界面。
- 备份始终强制开启，不提供关闭选项。

## 工作原理

1. **预扫描**：只读遍历文档，采集字号、加粗、首行缩进、对齐、样式与大纲级别，并统计正文基准字号、表格和公式段落。
2. **结构识别**：优先使用 Word 原生标题样式和段首编号；对没有样式的文档，结合字号、加粗、长度、句末标点和章节关键词打分，推断一级/二级/三级标题。手动改判与保护优先级最高。
3. **置信度策略**：高置信段落自动按模板修改；中置信只标记不修改；低置信跳过。尽量少改，避免误改。
4. **格式应用**：按模板设置段落字体、字号、加粗、缩进、行距、段前段后与对齐；表格保留行列与合并结构，只统一单元格文字格式。含 OMML 公式的段落自动保护。
5. **模板**：模板是 `templates/*.json`，按段落类型保存格式字段。GUI 下拉框、格式编辑器、智能识别与模板管理面板共用这些文件；模板文件被外部修改或删除时会自动同步。
6. **Markdown 规范化**：统一标题标记与块间空行，保留表格、代码围栏、数学公式、YAML 元数据与段落内硬换行。
7. **备份与报告**：每次执行前按时间戳备份原始文档并记录清单；输出文件名带 `_formatted`，并生成 TXT 报告，可选 HTML 报告。

## 模板

内置模板：

| 文件 | 场景 |
| --- | --- |
| `undergraduate_thesis.json` | 本科毕业论文 |
| `competition_report.json` | 竞赛申报书 |
| `common_homework.json` | 课程作业 |

模板字段示例：

```json
{
  "一级标题": {
    "font": "黑体",
    "size": 16,
    "bold": true,
    "indent": false,
    "line_spacing": 1.5,
    "space_before": 12,
    "space_after": 12
  },
  "正文": {
    "font": "宋体",
    "size": 12,
    "bold": false,
    "indent": true,
    "line_spacing": 1.5
  },
  "图注": {
    "font": "宋体",
    "size": 10.5,
    "bold": false,
    "align": "center"
  }
}
```

- `indent: true` 表示首行缩进 2 字符；也可写数字，如 `1.5` 表示 1.5 字符，`0` 表示不缩进。
- `line_spacing` 可写倍数（如 `1.5`），也可写固定磅值字符串（如 `"20pt"` 或 `"20磅"`）。
- `align` 可取 `left`、`center`、`right`、`justify`。
- 模板缺少某类段落规则时回落使用“正文”；缺少“表格”规则时表格文字保持不变。
- 可直接新增 JSON 文件，也可在 GUI 中编辑、识别保存或管理模板。

## 支持范围与限制

- 仅处理 `.docx`、`.md`、`.markdown`；`.doc`、`.docm`、`.dot`、`.dotx`、`.wps`、`.rtf`、`.odt`、`.pdf` 等会跳过并记录原因。
- 只处理主体段落和表格单元格文字；文本框、艺术字、图片内文字不处理。
- 不处理页边距、页眉页脚、页码、分栏、水印等页面设置。
- Word 表格保留结构，但字号或行距变化可能轻微影响行高。
- 数学公式只保护，不做 LaTeX/OMML 转换或重排。
- 中/低置信段落不会自动修改；标题推断存在误判风险，建议先预扫描复核。
- 模板中的字体需系统已安装；Linux 需自行配置中文字体。
- 程序只校准格式，不校验文字内容、错别字或逻辑。

## License

MIT License，见 [LICENSE](LICENSE)。
