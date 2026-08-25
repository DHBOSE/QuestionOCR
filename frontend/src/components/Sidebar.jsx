// Sidebar.jsx —— 左侧固定导航栏
// position: fixed 吸附在视口左侧，页面滚动时保持原位可见。
// 后续新功能只需在 NAV_ITEMS 里加一项（key + 图标 + 名称），
// 并在 App.jsx 中按 active 切换渲染对应页面组件。
import React from 'react'
import GradientText from './GradientText/GradientText'

// 品牌图标（书本 SVG）：fill 用 currentColor，颜色随 CSS 控制，悬停变色
const BrandIcon = () => (
  <svg
    className="sidebar-brand-icon"
    viewBox="0 0 1024 1024"
    xmlns="http://www.w3.org/2000/svg"
    width="20"
    height="20"
  >
    <path
      d="M277.333333 1002.666667c-83.2 0-149.333333-66.133333-149.333333-149.333334V170.666667c0-83.2 66.133333-149.333333 149.333333-149.333334h576c23.466667 0 42.666667 19.2 42.666667 42.666667v896c0 23.466667-19.2 42.666667-42.666667 42.666667H277.333333z m0-213.333334c-36.266667 0-64 27.733333-64 64s27.733333 64 64 64h533.333334v-128H277.333333zM213.333333 170.666667v548.266666c19.2-10.666667 40.533333-14.933333 64-14.933333h533.333334V106.666667H277.333333c-36.266667 0-64 27.733333-64 64z"
      fill="currentColor"
    />
    <path
      d="M362.666667 320h298.666666c23.466667 0 42.666667-19.2 42.666667-42.666667s-19.2-42.666667-42.666667-42.666666H362.666667c-23.466667 0-42.666667 19.2-42.666667 42.666666s19.2 42.666667 42.666667 42.666667M362.666667 469.333333h170.666666c23.466667 0 42.666667-19.2 42.666667-42.666666s-19.2-42.666667-42.666667-42.666667h-170.666666c-23.466667 0-42.666667 19.2-42.666667 42.666667s19.2 42.666667 42.666667 42.666666"
      fill="currentColor"
    />
  </svg>
)

// 导航项配置：第一个功能即本项目主功能「截图转题目 Word」
const NAV_ITEMS = [
  { key: 'screenshot2word', icon: '📄', label: '截图转题目 Word' },
]

export default function Sidebar({ active = 'screenshot2word', onSelect }) {
  return (
    <aside className="app-sidebar">
      {/* 品牌区（文字为渐变流光动效） */}
      <div className="sidebar-brand">
        <BrandIcon />
        <GradientText
          colors={['#15009C', '#5227FF', '#FF9FFC', '#15009C']}
          animationSpeed={6}
          showBorder={false}
          className="sidebar-brand-text"
        >
          题目系统
        </GradientText>
      </div>

      {/* 功能导航 */}
      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <div
            key={item.key}
            className={`sidebar-item${item.key === active ? ' sidebar-item-active' : ''}`}
            onClick={() => onSelect?.(item.key)}
          >
            <span className="sidebar-item-icon">{item.icon}</span>
            <span>{item.label}</span>
          </div>
        ))}
      </nav>

      {/* 底部提示：预留后续功能入口 */}
      <div className="sidebar-footer">更多功能开发中…</div>
    </aside>
  )
}
