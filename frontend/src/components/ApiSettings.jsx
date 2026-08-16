// ApiSettings.jsx —— API 识别设置面板
// 服务商 / API Key / 接口地址 / 模型名称；配置保存在浏览器 localStorage，不上传服务器留存
import React, { useEffect, useState } from 'react'
import { Alert, Input, Select, Space, Typography } from 'antd'
import axios from 'axios'

const { Text } = Typography

// localStorage 键名（密钥仅存在用户自己浏览器里）
const STORAGE_KEY = 's2qw_api_config'

// 默认配置（服务商预设从后端 /api-providers 拉取，失败时用这份兜底）
const FALLBACK_PROVIDERS = [
  {
    value: 'qwen',
    label: '通义千问 Qwen-VL（阿里 DashScope，推荐）',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-vl-max',
  },
  {
    value: 'zhipu',
    label: '智谱 GLM-4V',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4v-plus',
  },
  {
    value: 'doubao',
    label: '豆包 Vision（火山方舟）',
    base_url: 'https://ark.cn-beijing.volces.com/api/v3',
    model: 'doubao-vision-pro-32k-241028',
  },
  {
    value: 'gemini',
    label: 'Gemini（Google，需可访问 Google 网络）',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai',
    model: 'gemini-2.5-flash',
  },
  { value: 'custom', label: '自定义（任意 OpenAI 兼容端点）', base_url: '', model: '' },
]

function loadSaved() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return {
      provider: saved.provider || 'qwen',
      apiKey: saved.apiKey || '',
      baseUrl: saved.baseUrl || '',
      model: saved.model || '',
      workspace: saved.workspace || '',
    }
  } catch {
    return { provider: 'qwen', apiKey: '', baseUrl: '', model: '', workspace: '' }
  }
}

export default function ApiSettings({ config, onChange, disabled, open }) {
  const [providers, setProviders] = useState(FALLBACK_PROVIDERS)
  const [usage, setUsage] = useState(null)

  // 从后端拉取服务商预设（保持前后端一致）
  useEffect(() => {
    axios
      .get('/api-providers')
      .then((res) => {
        const list = res.data?.providers
        if (Array.isArray(list) && list.length > 0) setProviders(list)
      })
      .catch(() => {}) // 后端不可达时用兜底列表
  }, [])

  // 弹窗每次打开时刷新 API 用量统计
  useEffect(() => {
    if (!open) return
    axios
      .get('/usage')
      .then((res) => setUsage(res.data))
      .catch(() => setUsage(null))
  }, [open])

  // 更新字段并持久化（apiKey 也只存在本地浏览器）
  const update = (patch) => {
    const next = { ...config, ...patch }
    onChange(next)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  }

  // 切换服务商时自动带上该服务商的默认地址与模型（用户可再改）
  const handleProviderChange = (value) => {
    const preset = providers.find((p) => p.value === value)
    update({
      provider: value,
      baseUrl: preset?.base_url || '',
      model: preset?.model || '',
    })
  }

  return (
    <div>
      <Alert
        type="info"
        showIcon
        message="API 密钥仅保存在你自己的浏览器中，随请求发送给后端转发，不会写入服务器文件。"
        style={{ marginBottom: 12 }}
      />
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <div>
          <Text>服务商：</Text>
          <Select
            value={config.provider}
            onChange={handleProviderChange}
            options={providers}
            style={{ width: '100%', maxWidth: 480, marginTop: 4 }}
            disabled={disabled}
          />
        </div>
        <div>
          <Text>API Key：</Text>
          <span className="underline-field" style={{ display: 'block', maxWidth: 480, marginTop: 4 }}>
            <Input.Password
              value={config.apiKey}
              onChange={(e) => update({ apiKey: e.target.value })}
              placeholder="粘贴你的 API Key"
              style={{ width: '100%' }}
              disabled={disabled}
              autoComplete="off"
            />
          </span>
        </div>
        <div>
          <Text>接口地址：</Text>
          <span className="underline-field" style={{ display: 'block', maxWidth: 480, marginTop: 4 }}>
            <Input
              value={config.baseUrl}
              onChange={(e) => update({ baseUrl: e.target.value })}
              placeholder="https://...（OpenAI 兼容端点）"
              style={{ width: '100%' }}
              disabled={disabled}
            />
          </span>
        </div>
        <div>
          <Text>模型名称：</Text>
          <span className="underline-field" style={{ display: 'block', maxWidth: 480, marginTop: 4 }}>
            <Input
              value={config.model}
              onChange={(e) => update({ model: e.target.value })}
              placeholder="如 qwen-vl-max"
              style={{ width: '100%' }}
              disabled={disabled}
            />
          </span>
        </div>
        {/* 业务空间 ID 仅通义千问可选填：普通模型调用不需要，
            调用部署在特定业务空间里的模型时才必须 */}
        {config.provider === 'qwen' && (
          <div>
            <Text>业务空间 ID（可选，一般不填）：</Text>
            <span className="underline-field" style={{ display: 'block', maxWidth: 480, marginTop: 4 }}>
              <Input
                value={config.workspace}
                onChange={(e) => update({ workspace: e.target.value })}
                placeholder="如 ws-xxxxxxxx，仅调用业务空间内部署的模型时需要"
                style={{ width: '100%' }}
                disabled={disabled}
              />
            </span>
          </div>
        )}
      </Space>

      {/* API 用量统计（本机累计，存于后端 api_usage.json） */}
      {usage && usage.total?.calls > 0 && (
        <div style={{ marginTop: 16, padding: '10px 12px', background: '#f8fafc', borderRadius: 8 }}>
          <Text strong style={{ fontSize: 13 }}>累计 API 用量</Text>
          <div style={{ fontSize: 12, color: '#4b5563', marginTop: 6 }}>
            共 {usage.total.calls} 次调用 · 输入 {usage.total.prompt_tokens?.toLocaleString()} +
            输出 {usage.total.completion_tokens?.toLocaleString()} ={' '}
            {usage.total.total_tokens?.toLocaleString()} tokens
          </div>
          {Object.entries(usage.by_model || {}).map(([model, u]) => (
            <div key={model} style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>
              · {model}：{u.calls} 次，{u.total_tokens?.toLocaleString()} tokens
            </div>
          ))}
          <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 6 }}>
            费用请按服务商刊例价 × token 用量估算，这里只统计本机发起的调用。
          </div>
        </div>
      )}
    </div>
  )
}

export { loadSaved as loadApiConfig }
