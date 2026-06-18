# POST /api/upload

上传文件接口，支持 PDF、DOCX、TXT 格式。上传后可在对话中通过 `doc_id` 引用。

## 请求

```
POST /api/upload
Content-Type: multipart/form-data
```

### 表单字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `file` | File | 上传的文件 |

支持的文件类型：`.pdf`、`.docx`、`.txt`

## 响应

### 成功 (200)

```json
{
  "doc_id": "a1b2c3d4e5f6...",
  "filename": "租赁合同.pdf",
  "char_count": 12580,
  "truncated": false
}
```

| 字段 | 说明 |
|------|------|
| `doc_id` | 文档唯一 ID，用于后续对话引用 |
| `filename` | 原始文件名 |
| `char_count` | 解析后的文本字符数 |
| `truncated` | 是否因超长被截断（上限 60000 字符） |

### 错误

| 状态码 | 场景 |
|--------|------|
| 400 | 无文件名 / 不支持的文件类型 / 空文件 |
| 413 | 文件超过大小限制（默认 10MB） |
| 422 | 文件解析后内容为空 |
| 500 | 解析失败 |

## 示例

```bash
curl -X POST http://localhost:8000/api/upload \
  -F "file=@租赁合同.pdf"
```
