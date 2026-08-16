// QuestionPreview.jsx —— 题目 Markdown 的富文本预览
// 把编辑框中的 Markdown 实时渲染为接近 Word 的效果：
//   - 文字段落 / 换行
//   - $...$ / $$...$$ 公式用 KaTeX 渲染成数学排版
//   - ![](figures/...) 插图通过后端 /task-files 接口显示图片本体
// 渲染失败的公式显示原始 LaTeX 并红色标注（提前暴露问题，不阻塞生成）。
import React, { useMemo } from 'react'
import MarkdownIt from 'markdown-it'
import katex from 'katex'
import 'katex/dist/katex.min.css'

// html: false —— 模型输出的任何 HTML 都按文本转义，防注入
// breaks: true —— 题干中的换行直接生效，符合题目排版习惯
const md = new MarkdownIt({ html: false, linkify: false, breaks: true })

// 公式占位符（不会出现在正常题目文本中）
const MATH_TOKEN = (i) => `@@S2QWMATH${i}@@`

function renderMarkdownToHtml(text, taskId) {
  // 1. 提取公式为占位符（先 $$ 后 $，避免 $$ 被拆成两个 $）
  const maths = []
  let s = text.replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => {
    maths.push({ tex, display: true })
    return `\n\n${MATH_TOKEN(maths.length - 1)}\n\n`
  })
  s = s.replace(/\$([^$\n]+?)\$/g, (m, tex) => {
    maths.push({ tex, display: false })
    return MATH_TOKEN(maths.length - 1)
  })

  // 2. 剥掉 Pandoc 图片尺寸属性（{width=6cm ...} 浏览器不认）
  s = s.replace(/(!\[[^\]]*\]\([^)\s]+\))\{[^}]*\}/g, '$1')

  // 3. Markdown → HTML（公式占位符此时只是普通文本，不受影响）
  let html = md.render(s)

  // 4. 插图相对路径改写为后端文件接口
  html = html.replace(
    /<img src="(?!https?:|data:|\/)([^"]+)"/g,
    (m, p) => `<img src="/task-files/${taskId}/${encodeURI(p)}" alt="题目插图"`,
  )

  // 5. 占位符还原为 KaTeX 渲染结果
  html = html.replace(/@@S2QWMATH(\d+)@@/g, (m, i) => {
    const { tex, display } = maths[Number(i)]
    try {
      return katex.renderToString(tex, {
        displayMode: display,
        throwOnError: true,
        strict: false,
      })
    } catch {
      // 渲染失败：显示原始 LaTeX 并标红，提醒用户修正
      return `<code class="math-error">${tex.replace(/</g, '&lt;')}</code>`
    }
  })

  return html
}

export default function QuestionPreview({ text, taskId }) {
  // 编辑内容变化时重新渲染（useMemo 避免每次按键重复渲染整块）
  const html = useMemo(() => renderMarkdownToHtml(text, taskId), [text, taskId])
  // eslint-disable-next-line react/no-danger
  return <div className="question-preview" dangerouslySetInnerHTML={{ __html: html }} />
}
