# 开发与发布

## 环境与入口

源码面向 Windows 与64位 Python 3.11+，需要 Tkinter 和常用中文字体。依赖分别列在 `requirements-runtime.txt`、`requirements-build.txt`。推荐虚拟环境，运行与打包命令见仓库 README。

| 职责 | 当前入口 |
| --- | --- |
| 桌面启动与内部诊断 | `windows_launcher.py` |
| 编辑、导入调度与预览交互 | `desktop_app.py` |
| DOCX/DOC 导入 | `app/current_importer.py`，结构规则在 `app/flexible_importers.py` |
| 独立答案与附录 | `app/answer_typesetting.py` |
| 结构模型与校验 | `app/models/`、`app/validators/` |
| 正式 DOCX 生成 | `app/current_pipeline.py`、`app/renderers/docx_renderer.py` |
| 预览调度、缓存及取消 | `app/preview_service.py` |
| 内部页面预览 | `app/internal_preview.py`、`app/internal_preview_core.py`、`app/internal_preview_native.py` |
| 版式与字体配置 | `templates/layout.yaml` |

DOCX处理不依赖办公软件。旧版DOC转换按需使用Word/WPS；预览和导出共用段落调节参数，导出阶段不再调用LibreOffice重新校准行距。内部预览仍是估算，不能代替打印前核验。

## 验证要求

修改导入器、渲染器、Office/WPS 桥接层后运行全部测试：

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -rs
```

一部分测试读取开发机上的真实试卷，未找到原件时会显示 `skipped`。这些原件、学生数据、未公开试题不进入仓库。不要为消除跳过结果而上传真实文档；通用问题应增加合成小样本，真实文档另作本地验收。测试成功数与跳过数应同时记录。

测试集同时包含unittest类与pytest函数，统一使用pytest发现全部用例。GUI回归使用真实Tk控件及隔离的对话框，不会替用户操作现有文档窗口。`workbench_ui.py`负责界面布局，`workbench_state.py`负责草稿与文件名规则，`reference_profile.py`负责用户模板兼容规则。

内部诊断模式可验证打包后的实际代码，不会自动打开编辑窗口：

```powershell
$exe = (Resolve-Path '.\output\release\ChineseExamTypesetter_0.2.1.exe').Path
$inputDoc = (Resolve-Path 'D:\测试目录\脱敏样例.docx').Path
$diagnosticDir = (New-Item -ItemType Directory -Force '.\work').FullName
foreach ($mode in @('import', 'preview', 'export')) {
    $resultPath = Join-Path $diagnosticDir "$mode.json"
    $arguments = @("--$mode-test", ('"{0}"' -f $inputDoc), '--test-output', ('"{0}"' -f $resultPath))
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw "$mode failed; inspect $resultPath" }
    Get-Content -LiteralPath $resultPath
}
```

诊断使用绝对输入和结果路径：冻结版会切换到自身资源目录。PowerShell 对窗口程序可能立即返回，因此示例等待进程退出后读取结果。`IMPORT_OK`、`PREVIEW_OK`、`EXPORT_OK` 仅表示该环节完成，还要检查题号、分值、文字顺序及图表保留。

## 构建与版本

1. 更新 `app/version.py` 的 `APP_VERSION` 与 `pyproject.toml` 的版本；测试会校验两者一致。
2. 更新 `CHANGELOG.md` 和 README。打包配置直接读取 `APP_VERSION`，不用另改 EXE 文件名。
3. 安装依赖后运行 `build_windows.ps1`。脚本使用指定 Python 的基础安装位置寻找 Tcl/Tk，支持虚拟环境，不依赖开发者私人路径。
4. 在目标 Windows 环境检查 EXE、真实文档导入导出、预览与数据兼容。核对源文件哈希，保护原件。
5. 只在新包验收后清理构建中间文件；本地最多额外保留一个近期回滚版本。

运行依赖目前使用最低版本约束，构建器固定为 PyInstaller 6.22.3。相同源码在不同依赖环境下可能生成不同二进制，因此不能宣称逐字节可复现；每次发布必须校验实际上传的资产。

## GitHub 发布

- 源码提交到仓库，EXE 与 `SHA256SUMS.txt` 放 GitHub Releases，不加入 Git 源码目录。
- 标签采用 `v主版本.次版本.修订号`，指向已经验收的源码提交。先创建草稿、上传资产并检查，再转为正式版本。
- 发布说明列出变更、运行要求、测试范围、已知限制及校验方式；不要把历史验证描述成当前版本的新验证。
- 发布后检查 `releases/latest`、下载资产及 SHA256，并验证软件更新接口能读到新版本。
- 不覆盖已发布标签或原有资产。需要修改已发布程序时递增版本号，保留正式发布历史。

`work/`、`release/`、`output/`、缓存与本地附件均已忽略。提交前检查 `git diff --cached --name-only`，确认不含原始试卷、个人配置、凭据、日志或临时输出。
