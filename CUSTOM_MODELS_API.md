# 自定义模型列表 API 支持

本分支添加了从自定义 API 动态获取模型列表的功能，让 Zen MCP Server 能够使用任何兼容 OpenAI 格式的模型服务。

## 功能特性

- ✅ 从自定义 API 动态获取模型列表
- ✅ 自动使用 API Key 进行认证
- ✅ 失败时自动回退到本地 JSON 配置
- ✅ 完全向后兼容现有配置

## 使用方法

### 1. 配置环境变量

在 `.env` 文件中添加以下配置：

```bash
# OpenRouter API Key（或使用 OPENAI_API_KEY）
OPENROUTER_API_KEY=your_api_key_here

# 自定义 Base URL（会自动拼接 /models 获取模型列表）
OPENROUTER_BASE_URL=https://sucloud.vip/v1
```

### 2. API 响应格式

API 应该返回以下格式的 JSON：

```json
{
  "data": [
    {
      "id": "model-name",
      "context_length": 32768
    }
  ]
}
```

### 3. 支持的 API 服务

理论上支持任何返回模型列表的 OpenAI 兼容 API，例如：

- sucloud.vip
- OpenRouter (https://openrouter.ai/api/v1/models)
- 自建的模型服务
- 其他兼容 OpenAI 格式的服务

### 4. 测试功能

运行测试脚本验证配置：

```bash
python test_dynamic_models.py
```

## 工作原理

1. **优先从 API 获取**：如果设置了 `OPENROUTER_MODELS_API_URL`，系统会首先尝试从 API 获取模型列表
2. **自动认证**：使用 `OPENROUTER_API_KEY` 或 `OPENAI_API_KEY` 进行 Bearer Token 认证
3. **自动回退**：如果 API 请求失败（网络错误、认证失败等），自动回退到本地 `conf/openrouter_models.json` 配置
4. **格式转换**：自动将 API 返回的模型信息转换为 Zen 内部格式

## 配置示例

### 使用 sucloud.vip

```bash
OPENROUTER_API_KEY=sk-xxxxx
OPENROUTER_BASE_URL=https://sucloud.vip/v1
```

### 使用 OpenRouter 官方 API

```bash
OPENROUTER_API_KEY=sk-or-xxxxx
# OPENROUTER_BASE_URL 使用默认值，可以不设置
```

### 使用本地服务

```bash
OPENROUTER_API_KEY=local-key
OPENROUTER_BASE_URL=http://localhost:8000/v1
```

## 注意事项

1. **API Key 优先级**：系统会优先使用 `OPENROUTER_API_KEY`，如果没有则使用 `OPENAI_API_KEY`
2. **超时设置**：API 请求超时时间为 10 秒
3. **错误处理**：任何 API 错误都会被捕获并记录到日志，然后自动回退到本地配置
4. **模型能力**：从 API 获取的模型会使用默认能力配置，如需自定义请编辑本地 JSON 文件

## 与主分支同步

本功能在独立分支 `feature/custom-model-list` 上开发，可以随时合并主分支的更新：

```bash
# 获取主分支最新更新
git fetch origin main

# 合并主分支到当前分支
git merge origin/main

# 解决冲突（如果有）
git add .
git commit -m "Merge main branch updates"
```

## 故障排查

### 问题：API 请求失败

检查日志文件 `logs/mcp_server.log`：

```bash
tail -f logs/mcp_server.log | grep -i "openrouter"
```

常见原因：
- API Key 无效或过期
- API URL 不正确
- 网络连接问题
- API 服务暂时不可用

### 问题：模型列表为空

1. 确认 API 返回格式正确（包含 `data` 字段）
2. 检查 API 响应中的模型是否有 `id` 字段
3. 查看日志中的详细错误信息

## 开发说明

修改的文件：
- `providers/registries/openrouter.py` - 添加 API 获取逻辑
- `.env.example` - 添加新的环境变量说明
- `test_dynamic_models.py` - 测试脚本

核心逻辑在 `OpenRouterModelRegistry._load_config_data()` 方法中。
