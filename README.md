# Python Web UI Request Tester

一个轻量 Python 前后端项目：`server.py` 提供 Web UI 和 API，`core.py` 保留命令行执行能力。

## 运行

```powershell
python -m pip install -r requirements.txt
python server.py --host 127.0.0.1 --port 8765
```

打开：

```text
http://127.0.0.1:8765
```

## 部署提示

- 服务默认读取环境变量 `REQUEST_TESTER_HOST`、`REQUEST_TESTER_PORT`
- 任务数据默认保存在项目内 `server_data/jobs.json`
- 服务器部署建议设置 `REQUEST_TESTER_DATA_DIR=/var/lib/python-webui-request-tester`
- `deploy.systemd.service.example` 已包含 `StateDirectory=python-webui-request-tester`，systemd 会为服务用户准备可写数据目录
- 如果前面有 Nginx/Caddy 反向代理，Python 服务可以继续监听 `127.0.0.1:8765`

## Web UI 输入

- 输入：每行一个链接；不再支持单独输入 token
- 第一种任务格式：包含 `token=` 的链接
- 例如：`https://service.icourses.cn/resCourse/web/multi-level?token=aca27ca0becc4e808b3827146322e9c8`
- 第二种任务格式：`token-https://higher.smartedu.cn/course/lmc/<id>`
- 例如：`eyJ...281Z7mwZE1njOKXUMqlnOX-https://higher.smartedu.cn/course/lmc/67da63c02f4f1bef26a7e76a`
- 第二种任务由 `smartedu_core.py` 执行，会按课程视频逐个串行上报学习记录
- AES Key 不在前台输入，后端使用内置测试占位值；也可以用环境变量 `REQUEST_TESTER_AES_KEY` 或 `AES_KEY` 覆盖
- 第一门/第二门课程链接会用于判断课程页面类型：`multi-level` 使用分章详情接口，`lecture` 使用无分章详情接口
- 后端请求地址默认复用 `core.py` 里的 `DEFAULT_BASE_URL`
- 当前默认请求地址：`https://www.icourses.cn/higher_smartedu/course`
- 如需临时覆盖后端请求地址，可以用环境变量 `REQUEST_TESTER_BASE_URL`
- 第二种任务会拆出 `token` 和 `/course/lmc/<id>` 里的课程 id，后续由 `smartedu_core.py` 处理
- Web UI 会把任务交给后台线程执行，日志和状态保存到服务器本地 `server_data/jobs.json`
- 第三种任务每次启动都会先向 SmartEdu 服务器查询观看记录，只执行未完成的视频
- 如果 SmartEdu 服务器显示全部视频已完成，任务会直接输出 `全部课程已观看完毕！`，不会继续上报
- 第三种任务需要输入 `token-课程链接`；单独输入课程链接无法查询服务器观看状态
- 第三种任务运行中会启用结果框右上角的 `停止` 按钮；停止后再次输入同一 `token-课程链接` 可以重新查询服务器观看状态并继续未完成部分
- 前端会在浏览器 `localStorage` 保存设备标识；服务器记录不会写入明文 token 日志，只保存脱敏输入预览

## 链接提取程序

```powershell
python token_processor.py "https://service.icourses.cn/resCourse/web/multi-level?token=aca27ca0becc4e808b3827146322e9c8"
python token_processor.py --json "eyJ...281Z7mwZE1njOKXUMqlnOX-https://higher.smartedu.cn/course/lmc/67da63c02f4f1bef26a7e76a"
```

多行文本也可以通过 stdin 输入：

```powershell
Get-Content links.txt | python token_processor.py
```

## 命令行

```powershell
python core.py --token "YOUR_TOKEN" --link "https://www.example.com/api/course" --aes-key "YOUR_16_BYTE_KEY"
```

只在你拥有授权的接口、账号和网络环境里使用。
