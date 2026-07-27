# Bridge iLink Plugin

iLink 上游插件，负责与微信平台通信，处理消息收发和文件上传。

## 功能

- 微信消息接收和发送
- 文件上传到微信 CDN
- 消息加解密
- 会话管理

## 目录结构

```
bridge-ilink-plugin/
├── __init__.py          # 插件初始化
├── client.py            # iLink 客户端
├── connector.py         # 连接器
├── send.py              # 消息发送 (CDN 上传)
├── cdn_client.py        # CDN 客户端
├── ilink_auth.py        # 认证
└── config.yaml          # 插件配置
```

## 核心模块

### client.py

iLink WebSocket 客户端，处理与上游平台的连接。

### connector.py

插件连接器，实现 `Plugin` 接口：

```python
class ILinkPlugin(Plugin):
    name = "ilink_main"
    
    async def on_message(self, message):
        # 处理消息
        pass
    
    async def send_message(self, to, content):
        # 发送消息
        pass
```

### send.py

消息发送模块，包含：

- `get_upload_url()`: 获取微信 CDN 上传地址
- `upload_to_cdn()`: 上传文件到 CDN
- `send_message()`: 发送消息（支持文本/图片/文件）

### cdn_client.py

CDN 客户端，处理文件上传和下载：

```python
async def upload_file(data, media_type):
    # 1. 获取上传 URL
    # 2. AES 加密文件
    # 3. 上传到 CDN
    # 4. 返回媒体 ID
    pass
```

## 微信 CDN 流程

```
1. getUploadUrl → 获取 upload_param
2. AES-128-ECB 加密文件
3. 上传到 CDN URL
4. sendmessage (CDNMedia) → 发送消息
```

## 配置

### config.yaml

```yaml
ws_url: "ws://127.0.0.1:8765/ws/upstream"
app_id: "ilink_main"
app_secret: "your-secret"
```

## 依赖

- cryptography (AES 加密)
- aiohttp
- pydantic

## 使用

插件通过 WebSocket 连接到 bridge-main：

```python
# 连接到主平台
ws = await connect("ws://host:8765/ws/upstream")

# 发送消息
await send_message(to="user@im.wechat", content="Hello")
```

## 消息类型

- `message_type=1`: 文本
- `message_type=2`: 图片
- `message_type=3`: 文件

## 错误码

- `ret=0`: 成功
- `ret=-1`: 失败
- `ret=-2`: 参数错误

## 微信绑定

- 启动程序，会在终端或日志打印出二维三，用微信V8.0.69以上版本扫描二维码进行绑定，一个微信号只能绑定一个ilink
- 微信有限制必须每24小时发一次信息才能继续收到信息，本系统会在22、23小时发送一次提醒，回复OK或任意内容均可
- 
