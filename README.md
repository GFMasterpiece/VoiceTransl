
<p align="center">
	<img src="avatar.png" alt="Logo" width="160" />
</p>

<h1><p align='center' >VoiceTransl 聆译</p></h1>
<div align=center><img src="https://img.shields.io/github/v/release/shinnpuru/VoiceTransl"/>   <img src="https://img.shields.io/github/license/shinnpuru/VoiceTransl"/>   <img src="https://img.shields.io/github/stars/shinnpuru/VoiceTransl"/></div>

<p align="center">简体中文 | <a href="README_EN.md">English</a></p>

VoiceTransl聆译是 Windows 字幕处理与翻译软件，提供视频下载、音频提取、已有字幕翻译、视频合成和字幕总结等工具。本项目基于[Galtransl](https://github.com/xd2333/GalTransl)，采用GPLv3许可。当前桌面端不再提供自动听写；音视频翻译需要同名 SRT 字幕。

Web 开发版目前只保留最简音频输入/输出节点工作流及 Mock 验证能力，不执行真实识别或翻译。当前范围见[开发线路图](docs/roadmap.md)与[架构约定](docs/architecture.md)，下列工具特性主要对应桌面端。

## 特色

* 支持多种翻译模型，包括在线模型（任意OpenAI兼容接口）和本地模型（Sakura、Galtransl及Ollama、Llamacpp）。
* 支持AMD/NVIDIA/Intel GPU加速，翻译引擎支持调整显存占用。
* 支持多种输入格式，包括音频、视频、SRT字幕。
* 支持多种输出格式，包括SRT字幕、LRC字幕。
* 支持多种语言，包括日语，英语，韩语，俄语，法语。
* 可导入已有SRT字幕并继续转换、翻译和合并处理。
* 支持字典功能，可以自定义翻译字典，替换输入输出。
* 支持世界书/台本输入，可以自定义翻译参考资料。
* 支持从YouTube/Bilibili及媒体链接直接下载视频。
* 支持文件和链接批量处理，自动识别文件类型。
* 支持音频切分，字幕合并和视频合成。
* 支持视频总结，将视频内容总结为带时间轴简短的文本。
* 支持人声分离，将人声和伴奏分离，支持多种模型。

<div align=center><img src="title.jpg" alt="title" style="width:512px;"/></div>

## 在线镜像

打开即用的AI翻译，与配置环境说拜拜，推荐大家使用优云智算算力租赁平台。万卡4090 超多好玩免费的镜像给大家免费体验,高性价比算力租赁平台,上市公司ucloud旗下，专业有保障。点击链接直达[镜像地址](https://www.compshare.cn/images/compshareImage-16qc028dgfoh?referral_code=1RFfR2FQ2FyEVRJMyrOn5d&ytag=GPU_YY-GH_simple)，使用说明请看
[视频教程](https://b23.tv/qN9bDHi)。使用昕蒲邀请链接注册可得实名20增金+链接注册20+高校企业认证再得10，还可享95折，4090一小时只要1.98 ：[邀请链接](https://passport.compshare.cn/register?referral_code=1RFfR2FQ2FyEVRJMyrOn5d&ytag=simple_bilibili)

## 下载地址

下载最新版本的[VoiceTransl](https://github.com/shinnpuru/VoiceTransl/releases/)，解压后运行`VoiceTransl.exe`。

## 使用说明

使用说明请见 [视频教程](https://www.bilibili.com/video/BV12FjN6iEmz)。

桌面程序通过 `desktop_runtime.py` 统一处理工具路径、命令参数和子进程。FFmpeg、FFprobe 分别优先从程序的 `ffmpeg/` 目录查找，再查系统 PATH；请提供当前系统可运行的程序。源码运行和打包运行继续使用原有入口，已有命令模板无需迁移。

关闭任务时统一停止并等待直接启动的子进程，超时后强制结束并回收；该保证不包含外部工具自行创建的整棵进程树。Windows 隐藏控制台、旧命令模板解析和系统关机命令等必要差异集中在同一模块，普通文件路径操作使用 Python 标准库。

## 声明

本软件仅供学习交流使用，不得用于商业用途。本软件不对任何使用者的行为负责，不保证翻译结果的准确性。使用本软件即代表您同意自行承担使用本软件的风险，包括但不限于版权风险、法律风险等。请遵守当地法律法规，不要使用本软件进行任何违法行为。

## 贡献者

@[shinnpuru](https://github.com/shinnpuru) @[MurthiNext](https://github.com/MurthiNext)

## 如果对你有帮助的话请给一个Star!

![Star History Chart](https://star-history.dera.page/svg?repos=shinnpuru/VoiceTransl&type=Date)
