# 08. 常见问题与排障

## 1. `JSON decode error: Invalid control character`

### 现象

执行：

```powershell
$body = Get-Content .\examples\hangzhou_request.json -Raw
Invoke-RestMethod ... -Body $body
```

服务端返回：

```text
json_invalid
Invalid control character
```

### 根因

示例 JSON 使用 UTF-8 编码且不包含 BOM。PowerShell 7 默认能正确读取 UTF-8，但 Windows PowerShell 5.1 的 `Get-Content` 默认使用旧系统编码。

文件中的中文可能因此变成：

```text
杭州 → 鏉窞
```

部分中文字符的字节还可能破坏 JSON 字符串末尾的引号，导致服务端收到的内容已经不是合法 JSON。

所以：

```text
磁盘上的 JSON 文件是合法的
PowerShell 读取后的 $body 已经损坏
FastAPI 只是在正确地拒绝损坏的请求
```

### 正确命令

```powershell
$body = Get-Content `
  -LiteralPath .\examples\hangzhou_request.json `
  -Raw `
  -Encoding UTF8

$null = $body | ConvertFrom-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/plans `
  -ContentType "application/json; charset=utf-8" `
  -Body $body
```

其中：

```text
-Encoding UTF8
→ 明确使用 UTF-8 读取文件

ConvertFrom-Json
→ 在发送前先在本地验证 JSON

charset=utf-8
→ 明确请求体使用 UTF-8
```

### 更简单的方式

项目已经提供兼容 PowerShell 5.1 和 PowerShell 7 的脚本：

```powershell
.\scripts\invoke-hangzhou-example.ps1
```

## 2. 如何查看 PowerShell 版本

```powershell
$PSVersionTable.PSVersion
```

常见情况：

```text
Major = 5
→ Windows PowerShell 5.1，需要特别注意默认文件编码

Major = 7
→ PowerShell 7，默认 UTF-8 行为更一致
```

即使使用 PowerShell 7，项目命令仍显式写 `-Encoding UTF8`，避免依赖环境默认值。

## 3. 修正后返回中文仍然乱码

v0.1 API 现在会返回：

```text
Content-Type: application/json; charset=utf-8
```

更新代码后，如果开发服务器使用 `--reload`，它会自动重启。否则请手动停止并重新启动 Uvicorn：

```powershell
.\.venv\Scripts\python.exe -m uvicorn travel_agent.app:app --reload
```

然后重新运行调用脚本。

## 4. `Connection refused` 或无法连接

先检查健康接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

如果无法连接，确认：

- Uvicorn 是否正在运行
- 启动命令是否在项目根目录执行
- 端口是否为 8000
- 是否被其他程序占用

## 5. HTTP 422

HTTP 422 通常表示请求 JSON 可以解析，但字段不符合 Pydantic 模型，例如：

- 日期格式错误
- 经纬度超出范围
- 时间没有时区
- `departure <= arrival`
- 预算小于或等于零

查看响应中的 `detail`，其中的 `loc` 会指出出错字段路径。

## 6. `status = infeasible`

这不是 HTTP 或程序错误。它表示 Graph 正常运行，但在当前约束和 Replan 次数内没有找到合法行程。

检查：

```text
candidates[*].validation.violations
```

即可看到预算、营业时间、必去地点等具体冲突。

