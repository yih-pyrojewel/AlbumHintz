# AlbumHintz

一个为犹豫时刻准备的音乐入口。输入一个音乐标签和年份，AlbumHintz 会从 MusicBrainz 的公开资料中随机挑选一张正式专辑。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![MusicBrainz](https://img.shields.io/badge/data-MusicBrainz-BA478F)

## 功能

- 搜索音乐标签，例如 `pop`、`rock`、`hyperpop`、`mandopop`
- 在 🌎 欧美与 🇨🇳 华语音乐之间切换
- 按年份或年份范围筛选
- 只推荐正式 Album，排除 compilation、single 与 EP
- 随机推荐与预加载队列，减少重复和等待
- Apple Music、Spotify 与 MusicBrainz 的直达链接
- 专辑封面加载进度、模糊显现与毛玻璃界面
- 自动适配系统深色模式

## 运行

不需要安装任何第三方依赖。电脑需要有 Python 3。

1. 下载或克隆此仓库。
2. 在此文件夹内打开「终端」。
3. 运行：

   ```bash
   python3 server.py
   ```

4. 浏览器打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。

保持终端窗口运行；关闭它会停止本地服务。按 `Control + C` 可停止服务。

## 使用方式

1. 选择 🌎 或 🇨🇳。
2. 填写想听的 genre；可以从出现的建议中点选，也可以直接输入后点击「搜索」。
3. 需要时在右上方填写年份范围。
4. 点击「换一张」获得同一条件下的另一张专辑。

华语模式按普通话、粤语、闽南语、客家语、吴语等中文语言资料筛选；欧美模式按美国、英国、加拿大、澳大利亚、新西兰和爱尔兰的发行资料筛选。

## 数据与说明

- 专辑资料由 [MusicBrainz](https://musicbrainz.org/) 提供。
- 封面由 [Cover Art Archive](https://coverartarchive.org/) 提供。
- 所有请求都在你本机启动的 Python 服务中转发；本项目不保存用户搜索记录。
- MusicBrainz 是公共服务，偶尔会限流或暂时不可用。应用会自动重试；稍后重新搜索即可。

## 反馈

欢迎在 [GitHub: yih-pyrojewel](https://github.com/yih-pyrojewel) 反馈想法与问题。
