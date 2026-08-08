# 0.1 清理记录

本次清理以 `windows_launcher.py` 为唯一 Windows 入口，以 `desktop_app_current_v01.py` 为桌面运行模块。

保留范围：

- `app/`、`templates/`、`samples/`、`assets/`
- 当前运行入口及其仍被引用的排版模块
- `tests/` 与 `work/build-tools/`
- `output/` 中的用户生成文件

删除范围：

- 未被当前入口、测试和构建脚本使用的旧启动器
- 仅用于旧版本包装的顶层入口文件
- 旧版本打包目录 `b7/`、`d7/`
- Python 缓存目录

删除前已按项目根目录校验路径，模板、样例、测试和构建工具未纳入删除范围。
