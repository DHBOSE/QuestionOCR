// ProgressBar.jsx —— 进度条组件
// 显示整体处理进度（如 "2/5 张"）与百分比
import React from 'react'
import { Progress } from 'antd'

export default function ProgressBar({ current, total, status }) {
  if (total === 0) return null

  // 识别全部完成的标志：preview（等待确认编辑）与 done（Word 已生成）都算 100%
  const finished = status === 'preview' || status === 'done'

  // 并行识别模式下进度按"已完成张数"推进：current=已完成数
  const percent = finished ? 100 : Math.round((current / total) * 100)
  // status: active（处理中）/ success（完成）/ exception（出错）
  const progressStatus =
    status === 'error' ? 'exception' : finished ? 'success' : 'active'

  // 阶段提示文字
  const hint =
    status === 'preview'
      ? `已识别完 ${total}/${total} 张，请在下方预览编辑后生成 Word`
      : status === 'done'
        ? '全部完成'
        : `并行识别中，已完成 ${current}/${total} 张`

  return (
    <div style={{ marginTop: 16 }}>
      <Progress percent={percent} status={progressStatus} />
      <div style={{ color: '#6b7280', fontSize: 13, textAlign: 'center' }}>
        {hint}
      </div>
    </div>
  )
}
