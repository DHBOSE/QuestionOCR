// LogView.jsx —— 实时日志组件
// 显示后端 SSE 推送的处理日志；自动滚动到底部
import React, { useEffect, useRef } from 'react'

export default function LogView({ logs }) {
  const boxRef = useRef(null)

  // 日志更新时自动滚动到底部
  useEffect(() => {
    const el = boxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logs])

  return (
    <div className="log-view" ref={boxRef}>
      {logs.length === 0 ? (
        <span style={{ color: '#9ca3af' }}>暂无日志，点击"开始处理"后这里会实时显示进度……</span>
      ) : (
        logs.map((line, i) => {
          // 根据日志内容着色：成功绿色、失败红色、其余灰色
          const cls = line.includes('❌') || line.includes('失败')
            ? 'log-line-error'
            : line.includes('✅') || line.includes('成功') || line.includes('完成')
              ? 'log-line-success'
              : 'log-line-info'
          return (
            <div key={i} className={cls}>
              {line}
            </div>
          )
        })
      )}
    </div>
  )
}
