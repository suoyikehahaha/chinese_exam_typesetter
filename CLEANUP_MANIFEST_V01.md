# 0.1 清理记录

本次清理以 `windows_launcher_v045_final.py` 为 Windows 打包入口，以 `desktop_app_v045.py` 为当前桌面入口。

保留范围：

- `app/`、`templates/`、`samples/`、`assets/`
- 当前运行入口及其仍被引用的排版模块
- `tests/` 与 `work/build-tools/`
- `output/` 中的用户生成文件

删除范围：

- 未被当前入口、测试和当前构建脚本使用的旧启动器
- 仅用于旧版本包装的顶层入口文件
- 旧版本打包脚本、spec 文件和状态记录
- 未被当前入口或测试使用的旧预览服务模块
- Python 缓存目录

删除前已按当前入口、测试和 v0.4.5 spec 计算依赖闭包。仍保留的带版本后缀模块属于当前运行链或回归测试依赖，删除会破坏导入、预览或测试。模板、样例、测试和当前构建工具未纳入删除范围。

本次同步清除了本地 `release-v042-final`、`release-v043-final` 和 `release-v044-final` 生成目录。v0.45 生成目录暂时保留，便于回退和复测；这些目录均被 `.gitignore` 排除，不会进入源码仓库。

## 当前仍保留的版本模块

当前 Windows 入口的继承链为：

`windows_launcher_v045_final` → `desktop_app_v045` → `desktop_app_v041` → `desktop_app_v04` → `desktop_app_v03` → `desktop_app_current_v01` → `desktop_app_current` → `desktop_app_v087_release` → `desktop_app_v086_answer_release` → `desktop_app_v085_release` → `desktop_app_v084_fixed` → `desktop_app_v083_production` → `desktop_app_v082` → `desktop_app_v081` → `desktop_app_v080` → `desktop_app_v070` → `desktop_app_v060` → `desktop_app_v050`。

因此 GitHub 中看到的 `desktop_app_v060` 至 `desktop_app_v087` 并非孤立备份文件。它们是当前运行链的一部分，删除前需要先完成一次整体架构合并，再重新进行 EXE 启动、导入、预览和导出回归。
