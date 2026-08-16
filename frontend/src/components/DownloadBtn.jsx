// DownloadBtn.jsx —— 结果下载组件
// 处理完成后按实际生成的文件显示下载按钮（Word / PDF）；
// 若有失败文件，用红色列出
import React from 'react'
import { Alert } from 'antd'
import GlassButton from './GlassButton'

// 触发浏览器下载（kind: docx / pdf）
const download = (taskId, kind) => {
  const a = document.createElement('a')
  a.href = `/download/${taskId}?kind=${kind}`
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

export default function DownloadBtn({ taskId, failed, files }) {
  if (!taskId) return null
  // files 为 finalize 返回的 {docx, pdf}；未传时按只有 Word 处理（兼容旧逻辑）
  const hasDocx = files ? files.docx : true
  const hasPdf = files ? files.pdf : false

  return (
    <div style={{ marginTop: 16 }}>
      <Alert
        type="success"
        showIcon
        message="处理完成！"
        style={{ marginBottom: 12 }}
      />
      {/* 红色列出识别失败的文件 */}
      {failed.length > 0 && (
        <Alert
          type="error"
          showIcon
          message="以下文件识别失败："
          description={
            <ul className="failed-list">
              {failed.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          }
          style={{ marginBottom: 12 }}
        />
      )}
      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
        {hasDocx && (
          <GlassButton onClick={() => download(taskId, 'docx')}>
            下载 Word 文件
          </GlassButton>
        )}
        {hasPdf && (
          <GlassButton onClick={() => download(taskId, 'pdf')}>
            下载 PDF 文件
          </GlassButton>
        )}
      </div>
    </div>
  )
}
