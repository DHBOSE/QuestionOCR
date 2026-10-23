// DownloadBtn.jsx —— 结果下载组件
// 处理完成后按实际生成的文件显示下载按钮（Word / PDF；双版本时学生版/教师版分开）；
// 若有失败文件，用红色列出
import React from 'react'
import { Alert } from 'antd'
import GlassButton from './GlassButton'

// 触发浏览器下载（kind: docx / pdf；ver: teacher 默认 / student 学生版）
const download = (taskId, kind, ver = 'teacher') => {
  const a = document.createElement('a')
  a.href = `/download/${taskId}?kind=${kind}&ver=${ver}`
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

export default function DownloadBtn({ taskId, failed, files }) {
  if (!taskId) return null
  // files 为 finalize 返回的 {docx, pdf, dual, docx_student, pdf_student}；
  // 未传时按只有 Word 处理（兼容旧逻辑）
  const hasDocx = files ? files.docx : true
  const hasPdf = files ? files.pdf : false
  const dual = files?.dual

  return (
    <div style={{ marginTop: 16 }}>
      <Alert
        type="success"
        showIcon
        message={dual ? '处理完成！已生成学生版 + 教师版' : '处理完成！'}
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
          <GlassButton onClick={() => download(taskId, 'docx', 'teacher')}>
            {dual ? '教师版 Word（含答案）' : '下载 Word 文件'}
          </GlassButton>
        )}
        {dual && files?.docx_student && (
          <GlassButton onClick={() => download(taskId, 'docx', 'student')}>
            学生版 Word（无答案）
          </GlassButton>
        )}
        {hasPdf && (
          <GlassButton onClick={() => download(taskId, 'pdf', 'teacher')}>
            {dual ? '教师版 PDF（含答案）' : '下载 PDF 文件'}
          </GlassButton>
        )}
        {dual && files?.pdf_student && (
          <GlassButton onClick={() => download(taskId, 'pdf', 'student')}>
            学生版 PDF（无答案）
          </GlassButton>
        )}
      </div>
    </div>
  )
}
