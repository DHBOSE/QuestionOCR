// GlassButton.jsx —— 玻璃质感按钮
// 结构对应样式约定：.glass-btn-wrap > button > span + .button-shadow
// 视觉效果（见 index.css 中"玻璃质感按钮"一节）：
//   旋转描边高光、悬停扫光、按下 3D 倾斜 + 投影变化
import React from 'react'

export default function GlassButton({
  children,
  icon,
  onClick,
  disabled = false,
  loading = false,
  style,
}) {
  const inactive = disabled || loading
  return (
    <div
      className={`glass-btn-wrap${inactive ? ' glass-btn-inactive' : ''}`}
      style={style}
    >
      <button type="button" onClick={onClick} disabled={inactive}>
        <span>
          {icon && <i className="glass-btn-icon">{icon}</i>}
          {children}
        </span>
      </button>
      <div className="button-shadow" />
    </div>
  )
}
