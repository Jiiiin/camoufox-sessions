# Camoufox Sessions

A native macOS launcher for named, persistent Camoufox browser sessions.

给浏览会话起一个名字，下次打开接着用。基于 SwiftUI 和 Camoufox 的独立社区项目。

## 功能

- 创建、改名、搜索会话，设置首次打开的网址和备注。
- 多个会话同时打开，分别保存 Cookie、页面存储和设备配置。
- 在一个窗口里恢复多个标签页。
- 默认跟随 macOS 系统代理。
- 归档、恢复会话；删除归档会话时确认，并把数据移入系统废纸篓。
- 防止同一会话重复打开，支持保存并关闭或直接关闭最后一个浏览器窗口。

目前是早期版本，仅支持 macOS 14+。本地验证使用 Camoufox Python 0.5.8
和配套浏览器 156.0.1-beta.36。没有自动更新、批量管理、代理节点管理或跨设备同步。

## 从源码构建

需要 Xcode Command Line Tools、Python 3 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。
首次构建需要联网下载独立 Python 运行时、Python 依赖和配套浏览器，应用体积较大。

```sh
git clone https://github.com/Jiiiin/camoufox-sessions.git
cd camoufox-sessions
python3 scripts/build.py
open "build/Camoufox Sessions.app"
```

构建产物位于 `build/Camoufox Sessions.app`，可以复制到“应用程序”目录。
应用包含运行时，运行时无需另外安装 Python。构建脚本会保留第三方组件的许可证，
并对应用进行本地 ad-hoc 签名；目前没有 Apple 开发者签名或公证，也没有预编译安装包。

构建脚本默认使用 uv 选择并下载的 Python 架构。可以复用独立 CPython 3.12 和
Camoufox 缓存来减少下载；虚拟环境不能替代独立运行时：

```sh
python3 scripts/build.py --runtime /path/to/standalone-python \
  --browser-cache /path/to/camoufox-cache
```

复用的浏览器需要和 Camoufox 0.5.8 配套，并适用于本机架构。
在 Apple Silicon 上使用 Intel 构建需要 Rosetta；不同架构的完整构建还需要进一步验证。
不要把个人浏览器配置目录当成上述缓存路径。

## 使用

1. 点击“新建会话”，填写名称，可选填写首次打开的网址和备注。
2. 点击“打开会话”，不同会话可以同时使用。
3. 直接关闭整个浏览器窗口，或者点击“保存并关闭”。下次打开同名会话会复用数据，
   在一个窗口里重新打开最近保存的标签页地址。
4. “归档”隐藏会话；开启“查看归档”后可以恢复，也可以选择“删除会话…”。

关闭整个窗口会保留最近保存的标签页。逐个关闭标签页会更新已保存的页面列表。
页面地址约每半秒保存一次；不恢复滚动位置、未提交的表单或网页运行状态。
登录是否有效仍由网站决定。退出启动器会先关闭它打开的会话。

删除操作会从列表移除会话，并把相应浏览器数据放入 macOS 废纸篓。
正在使用的会话不能删除。废纸篓里的数据不会自动重新出现在启动器中。

## 数据

用户数据独立于应用，位于：

```text
~/Library/Application Support/Camoufox Sessions/
  sessions.json
  profiles/<uuid>/
    browser-data/
    identity.json
    tabs.json
```

关闭所有会话后，可以备份整个数据目录。替换应用时保留该目录即可继续使用。
这些文件包含会话数据，不应提交到 GitHub 或附在公开 Issue 中。

## 开发与验证

```text
src/Launcher.swift       SwiftUI 界面、会话元数据与进程管理
src/session_worker.py    每个会话独立的持久化浏览器进程
src/Icon.swift           原创图标生成器
scripts/build.py        独立运行时与浏览器打包
scripts/fetch_browser.py 隔离缓存内下载配套浏览器
tests/                  真实浏览器与会话删除检查
```

检查删除行为（仅测试临时会话）：

```sh
python3 tests/test_deletion.py --scratch build/deletion-tests
```

完成构建后，检查两个会话各三个标签页、单窗口恢复、Cookie 与页面存储隔离、重开持久化、
设备配置复用、重复打开保护和正常关闭。会短暂打开测试浏览器窗口：

```sh
APP_RESOURCES="$PWD/build/Camoufox Sessions.app/Contents/Resources"
"$APP_RESOURCES/runtime/bin/python" tests/test_sessions.py \
  --resources "$APP_RESOURCES" --scratch build/session-tests
```

GitHub Actions 检查原生界面编译和 Python 语法；真实浏览器检查需要 macOS 图形会话。
可使用 `CAMOUFOX_SESSIONS_DATA_DIR` 为启动器指定独立测试目录。
本地已验证复用运行时和浏览器缓存的完整构建；首次全部联网下载的流程和其他 Mac
仍需要更多验证。

## 许可证与致谢

本项目的原创代码采用 [MIT](LICENSE)。感谢 [Camoufox](https://github.com/daijro/camoufox)
和 [Playwright](https://github.com/microsoft/playwright-python)。浏览器、Python 运行时及其依赖
保留各自许可证，见 [第三方说明](THIRD_PARTY_NOTICES.md)。本项目不隶属于 Camoufox 官方。
