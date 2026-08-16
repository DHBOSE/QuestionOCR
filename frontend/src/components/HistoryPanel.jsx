// HistoryPanel.jsx —— 历史任务面板
// 列出 7 天内有识别结果的任务：可重新下载、按当前输出设置改格式再导出、删除记录
import React, { useEffect, useState } from 'react'
import { Modal, List, Button, Popconfirm, Empty, message, Typography } from 'antd'
import axios from 'axios'

const { Text } = Typography

// 触发浏览器下载（kind: docx / pdf）
const triggerDownload = (taskId, kind) => {
  const a = document.createElement('a')
  a.href = `/download/${taskId}?kind=${kind}`
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

const fmtTime = (ts) =>
  ts ? new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }) : ''

export default function HistoryPanel({ open, onClose, exportSettings }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [reexporting, setReexporting] = useState(null) // 正在重新导出的 task_id

  const load = () => {
    setLoading(true)
    axios
      .get('/history')
      .then((res) => setItems(res.data?.items || []))
      .catch(() => message.warning('历史任务加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (open) load()
  }, [open])

  // 重新导出：取回历史任务的最终 Markdown，按当前输出设置重新生成文件
  const reexport = async (item) => {
    setReexporting(item.task_id)
    try {
      const { data: detail } = await axios.get(`/history/${item.task_id}`)
      const { filename, font, title, format } = exportSettings
      const { data } = await axios.post(`/finalize/${item.task_id}`, {
        questions: detail.question_mds,
        filename: filename || item.output_stem || 'output',
        font,
        title,
        format,
      })
      const filesOut = data.files || { docx: true, pdf: false }
      message.success('重新导出完成，已开始下载')
      if (format === 'pdf') {
        triggerDownload(item.task_id, 'pdf')
      } else if (format === 'both' && filesOut.pdf) {
        triggerDownload(item.task_id, 'docx')
        setTimeout(() => triggerDownload(item.task_id, 'pdf'), 600)
      } else {
        triggerDownload(item.task_id, 'docx')
      }
      if (data.pdf_error) message.warning(`PDF 生成失败，已仅导出 Word：${data.pdf_error}`)
      load() // 刷新文件状态
    } catch (err) {
      message.error(`重新导出失败：${err.response?.data?.detail || err.message}`)
    } finally {
      setReexporting(null)
    }
  }

  const remove = async (item) => {
    try {
      await axios.delete(`/history/${item.task_id}`)
      setItems((prev) => prev.filter((x) => x.task_id !== item.task_id))
      message.success('已删除')
    } catch {
      message.error('删除失败')
    }
  }

  return (
    <Modal
      title="历史任务（保留 7 天，含识别结果）"
      open={open}
      onCancel={onClose}
      footer={null}
      width={640}
    >
      {items.length === 0 && !loading ? (
        <Empty description="暂无历史任务" style={{ padding: '24px 0' }} />
      ) : (
        <List
          loading={loading}
          dataSource={items}
          style={{ maxHeight: 440, overflow: 'auto' }}
          renderItem={(item) => (
            <List.Item
              actions={[
                item.has_docx && (
                  <Button key="docx" size="small" onClick={() => triggerDownload(item.task_id, 'docx')}>
                    Word
                  </Button>
                ),
                item.has_pdf && (
                  <Button key="pdf" size="small" onClick={() => triggerDownload(item.task_id, 'pdf')}>
                    PDF
                  </Button>
                ),
                <Button
                  key="re"
                  size="small"
                  type="primary"
                  ghost
                  loading={reexporting === item.task_id}
                  onClick={() => reexport(item)}
                >
                  重新导出
                </Button>,
                <Popconfirm key="del" title="删除该任务及文件？" onConfirm={() => remove(item)}>
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>,
              ].filter(Boolean)}
            >
              <List.Item.Meta
                title={
                  <span>
                    {item.output_stem || item.orig_names[0] || '未命名'}
                    <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                      {item.question_count} 题
                      {item.api_calls > 0 && ` · API ${item.api_calls} 次 / ${item.total_tokens} tokens`}
                      {item.failed?.length > 0 && ` · ${item.failed.length} 张失败`}
                    </Text>
                  </span>
                }
                description={
                  <span style={{ fontSize: 12 }}>
                    {fmtTime(item.created_at)}
                    {item.orig_names.length > 0 && ` · ${item.orig_names.join('、')}`}
                  </span>
                }
              />
            </List.Item>
          )}
        />
      )}
      <div style={{ marginTop: 8, fontSize: 12, color: '#9ca3af' }}>
        「重新导出」使用该任务的最终识别结果 + 当前「输出设置」里的格式 / 字体 / 标题前缀，无需重新识别。
      </div>
    </Modal>
  )
}
