# 🚀 AutoScripts

> 个人维护的自动化脚本集合，定期更新优化。

欢迎加入QQ交流群：**1093813702**

## 📌 仓库信息

* **适用环境**：青龙面板、呆呆面板
* **主要语言**：Python / Node.js
* **维护状态**：个人维护，定期更新

## 📚 CodeScript

| 状态 | 脚本        | 功能       | AppID                | 登录方式 | 账号变量        | 青龙命令           |
| -- | --------- | -------- | -------------------- | ---- | ----------- | -------------- |
| ✅  | `sfsy.py` | 顺丰速运签到 | `wxd4185d00bf7e08ac` | Code | `sf_openid` | `task sfsy.py` |
| ✅  | `trsj.py` | 甜润世界签到 | `wx210e40a77dbe7a27` | Code | `trsj_openid` | `task trsj.py` |
| ✅  | `cyy.py` | 草原云小程序签到 | `wxcf78d47051628692` | Code | `cyy_openid` | `task cyy.py` |
| ✅  | `wyjh.py` | 无忧计划签到 | `/` | Code | `WY_ACCOUNT` | `task wyjh.py` |
| ✅  | `mpcbh.js` | 毛铺草本荟签到 | `wxefd0fe341e06b815` | Code | `mpcbh_openid` | `task mpcbh.js` |
| ✅  | `jph.py` | 君品荟签到 | `wx8d41cdc44c8aeaab` | Code | `jph_openid` | `task jph.py` |
| ✅  | `ppcs.js` | 朴朴超市签到 | `wx122ef876a7132eb4` | Code | `ppcs_openid` | `task ppcs.js` |
| ✅  | `oppo.py` | OPPO商城签到 | `wxe705c556754a1de2` | Code | `oppo_openid` | `task oppo.py` |
| ✅  | `mdhy.js` | 美的会员签到 | `wx49a622805968d156` | Code | `midea_openid` | `task mdhy.js` |

> 部分脚本需要通过 **Code 登录** 获取账号信息，请先部署并配置 `YYB-Go` 服务。

## 🔑 Code 登录

需要 Code 登录的脚本，请先部署 **YYB-Go**。

YYB-Go 用于管理微信账号并获取小程序 `wx.login` Code，脚本通过 `YYB-Go` 获取对应账号的登录凭证。

### 部署 YYB-Go

建议使用 Docker 部署。

部署完成后，例如：

```text
http://服务器IP:8000
```

然后在脚本环境变量中配置：

```bash
wx_server_url="http://服务器IP:8000"
```

> YYB-Go 的具体部署方式及配置请以对应项目文档为准。

## 📝 账号变量

多账号支持以下分隔方式：

```text
& (推荐优先使用)
英文逗号（,）
中文逗号（，）
换行
```

示例：

```bash
wx_server_url="http://服务器IP:8000"

sf_openid="openid_a&openid_b"
```

> 不同脚本所需的环境变量可能不同，请以脚本注释或对应说明为准。

## ⚙️ 使用方法

### 1. 部署 YYB-Go

需要 Code 登录的脚本，先完成 YYB-Go 部署及微信账号配置。

### 2. 配置环境变量

根据脚本要求配置对应变量。

### 3. 创建任务

青龙面板：

```bash
task xx.py
```

也可以手动运行：

```bash
python3 xx.py
```

> 建议首次使用单账号测试，确认正常后再配置多账号。

## 📦 依赖

如果脚本需要额外依赖，请根据脚本顶部注释安装。

例如：

```bash
python3 -m pip install requests cryptography
```

## ⚠️ 注意事项

* 使用前请自行检查脚本代码及运行风险。
* 请勿将 OpenID、Token、Cookie、密钥等敏感信息提交到公开仓库。
* 平台接口、活动规则及验证机制可能随时变化，脚本可能失效。
* 请确保使用行为符合相关法律法规及平台规则。

## 💬 问题反馈

遇到问题可加入交流群：

**QQ群：1093813702**

反馈时请提供**脚本名称 + 运行日志**，并注意隐藏 OpenID、Token、Cookie 等敏感信息。

## ⚖️ 免责声明

本仓库部分内容来源于公开互联网资料、公开代码整理或 AI 工具辅助生成，并非全部为原创。

所有内容仅供**个人学习、技术研究、测试与交流**使用。

本仓库内容按 **“AS IS（现状）”** 提供，不保证稳定性、可用性及持续有效性。因使用相关脚本产生的任何风险或损失，由使用者自行承担。

禁止将本仓库内容用于**未经授权的访问、违法活动或其他违反法律法规及平台规则的行为**。

如发现仓库内容存在侵权问题，请联系维护者处理。

---

⭐ 如果本仓库对你有所帮助，欢迎点亮 Star。
