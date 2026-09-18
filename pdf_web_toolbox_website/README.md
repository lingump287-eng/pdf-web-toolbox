# PDF Web Toolbox — 网站部署版

这是从原本地桌面/localhost 版本整理出的 **可直接部署的网站项目**。前端和 FastAPI 后端由同一个服务提供，部署后用户只需要访问域名，无需安装软件。

## 已保留功能

- PDF 合并
- 页面提取
- 删除页面
- 按页拆分
- 页面旋转
- PDF 转图片
- 图片转 PDF
- 文字水印
- 添加页码
- PDF 加密
- PDF 解密
- PDF 信息查看

## 本地启动

### Docker（推荐）

```bash
docker compose up --build
```

浏览器访问：`http://localhost:8000`

### Python

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python start_server.py
```

## 云端部署

项目根目录已经包含 `Dockerfile` 和 `render.yaml`，可部署到支持 Docker 的平台（Render、Railway、Fly.io、自建 VPS 等）。

典型流程：

1. 将本项目上传到 GitHub/GitLab。
2. 在云平台新建 Web Service。
3. 选择 Docker 部署。
4. 平台分配域名后直接访问即可。

## 环境变量

| 变量 | 默认值 | 用途 |
|---|---:|---|
| `PORT` | `8000` | Web 服务端口，云平台通常自动设置 |
| `PDF_TOOLBOX_MAX_FILE_MB` | `100` | 单个文件最大 MB |
| `PDF_TOOLBOX_MAX_FILES` | `20` | 单次最多上传文件数 |
| `PDF_TOOLBOX_TEMP_ROOT` | `/tmp/pdf-web-toolbox`（Docker） | 临时任务目录 |
| `PDF_TOOLBOX_ALLOWED_ORIGINS` | 空 | 仅在前后端跨域部署时设置，逗号分隔 |

## 文件隐私与存储

处理文件保存在服务端临时任务目录。正常完成或发生错误后，任务目录都会被删除。项目不需要数据库，也不会主动长期保存用户上传的 PDF。

如果准备公开给大量用户使用，建议在反向代理/云平台再增加：HTTPS、请求体大小限制、限流、日志与监控。

## 项目结构

```text
.
├─ Dockerfile
├─ docker-compose.yml
├─ render.yaml
├─ backend/
│  ├─ requirements.txt
│  ├─ start_server.py
│  └─ app/
│     ├─ main.py
│     ├─ api/pdf.py
│     ├─ services/pdf_ops.py
│     ├─ utils/files.py
│     └─ frontend_dist/
└─ README.md
```
