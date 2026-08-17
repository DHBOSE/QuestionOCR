// App.jsx —— 主页面
// 布局：上方控制区（上传 + 输出设置 + 操作按钮），下方进度与日志区
import React, { useEffect, useRef, useState } from 'react'
import { Input, Select, message, Alert, Modal } from 'antd'
import axios from 'axios'
import Uploader from './components/Uploader'
import ProgressBar from './components/ProgressBar'
import LogView from './components/LogView'
import DownloadBtn from './components/DownloadBtn'
import QuestionPreview from './components/QuestionPreview'
import ApiSettings, { loadApiConfig } from './components/ApiSettings'
import HistoryPanel from './components/HistoryPanel'
import GlassButton from './components/GlassButton'
import WebThreads from './components/WebThreads/WebThreads'

// Word 中文字体选项（与后端 docx_builder.AVAILABLE_FONTS 保持一致）
const FONT_OPTIONS = [
  { value: '仿宋', label: '仿宋' },
  { value: '黑体', label: '黑体' },
  { value: '楷体', label: '楷体' },
  { value: '宋体', label: '宋体' },
  { value: '微软雅黑', label: '微软雅黑' },
  { value: '等线', label: '等线' },
]

// 导出格式选项（PDF 由后端调用本机 Word / WPS / LibreOffice 转换）
const FORMAT_OPTIONS = [
  { value: 'docx', label: 'Word（.docx）' },
  { value: 'pdf', label: 'PDF（.pdf）' },
  { value: 'both', label: 'Word + PDF' },
]

// 触发浏览器下载（kind: docx / pdf）
const triggerDownload = (taskId, kind) => {
  const a = document.createElement('a')
  a.href = `/download/${taskId}?kind=${kind}`
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

export default function App() {
  // 已选文件列表 [{uid, name, raw(File), url(预览地址)}]
  const [files, setFiles] = useState([])
  // 输出文件名（不含后缀，后缀由导出格式决定）
  const [outputName, setOutputName] = useState('output')
  // Word 中文字体（默认仿宋）
  const [fontName, setFontName] = useState('仿宋')
  // 导出格式：docx / pdf / both
  const [format, setFormat] = useState('docx')
  // 一页多题自动拆分（默认开）
  const [autoSplit, setAutoSplit] = useState(true)
  // 生成结果包含哪些文件（finalize 成功后由后端返回）
  const [resultFiles, setResultFiles] = useState(null)
  // 每道题标题前缀（默认"题目"，生成"题目 N"）
  const [titlePrefix, setTitlePrefix] = useState('题目')
  // 处理状态：idle / processing / done / error
  const [status, setStatus] = useState('idle')
  // 进度：{ current, total }
  const [progress, setProgress] = useState({ current: 0, total: 0 })
  // 实时日志行
  const [logs, setLogs] = useState([])
  // 当前任务 ID 与失败文件列表
  const [taskId, setTaskId] = useState(null)
  const [failed, setFailed] = useState([])
  // 识别预览：每题的 Markdown（可编辑）；null 表示尚未到预览阶段
  const [previewQuestions, setPreviewQuestions] = useState(null)
  // 正在生成 Word（finalize 请求中）
  const [finalizing, setFinalizing] = useState(false)
  // 后端 Pandoc 是否可用（启动时探测）
  const [pandocOk, setPandocOk] = useState(true)
  // API 识别配置（服务商 / 密钥 / 地址 / 模型，初始值来自 localStorage）
  const [apiConfig, setApiConfig] = useState(loadApiConfig)
  // API 设置弹窗开关
  const [apiModalOpen, setApiModalOpen] = useState(false)
  // 历史任务面板开关
  const [historyOpen, setHistoryOpen] = useState(false)

  // SSE 连接引用，便于组件卸载时关闭
  const eventSourceRef = useRef(null)
  // 处理状态引用（粘贴监听里读取最新值，避免闭包过期）
  const statusRef = useRef(status)
  statusRef.current = status

  // 启动时检查后端健康状态（Pandoc 是否就绪）
  useEffect(() => {
    axios
      .get('/health')
      .then((res) => setPandocOk(res.data.pandoc))
      .catch(() =>
        message.warning('无法连接后端，请先启动后端服务（python api.py）'),
      )
    return () => eventSourceRef.current?.close()
  }, [])

  // Ctrl+V 直接粘贴剪贴板截图加入上传列表（输入框聚焦时不拦截，方便正常粘贴文字）
  useEffect(() => {
    const onPaste = (e) => {
      if (statusRef.current === 'processing') return
      const tag = document.activeElement?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      const imgs = Array.from(e.clipboardData?.files || []).filter((f) =>
        /^image\/(png|jpe?g)$/i.test(f.type),
      )
      if (imgs.length === 0) return
      e.preventDefault()
      const now = new Date()
      const pad = (n) => String(n).padStart(2, '0')
      const stamp = `${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
      const items = imgs.map((f, i) => ({
        uid: `paste-${Date.now()}-${i}`,
        name: `粘贴截图_${stamp}${imgs.length > 1 ? `_${i + 1}` : ''}.png`,
        raw: f,
        kind: 'image',
        url: URL.createObjectURL(f),
        engine: 'hybrid',
      }))
      setFiles((prev) => [...prev, ...items])
      message.success(`已粘贴 ${items.length} 张截图`)
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [])

  const pushLog = (line) => setLogs((prev) => [...prev, line])

  // 清空文件与全部状态
  const handleClear = () => {
    files.forEach((f) => URL.revokeObjectURL(f.url))
    setFiles([])
    setLogs([])
    setProgress({ current: 0, total: 0 })
    setStatus('idle')
    setTaskId(null)
    setFailed([])
    setPreviewQuestions(null)
    setFinalizing(false)
    setResultFiles(null)
  }

  // 开始处理：先上传文件，再建立 SSE 连接接收实时进度
  const handleStart = async () => {
    if (files.length === 0) {
      message.warning('请先选择题目文件（截图 / PDF / Word）')
      return
    }
    setStatus('processing')
    setLogs([])
    setFailed([])
    setTaskId(null)
    setPreviewQuestions(null)
    setResultFiles(null)
    setProgress({ current: 0, total: files.length })

    // 有题目选择 API / 混合识别时，先校验 API 配置是否完整
    // （Word 直转模式 engine 固定为 local，不会误触发校验）
    const hasApi = files.some((f) => f.engine === 'api' || f.engine === 'hybrid')
    if (hasApi && (!apiConfig.apiKey || !apiConfig.baseUrl || !apiConfig.model)) {
      setStatus('idle')
      message.warning('有题目选择了 API / 混合识别，请先点击「API 设置」按钮填好密钥、接口地址和模型名称')
      setApiModalOpen(true) // 直接弹出设置窗口，省去用户寻找
      return
    }

    try {
      // 1. 上传全部文件（附带每文件引擎选择、页码范围、Word 模式与 API 配置）
      const formData = new FormData()
      files.forEach((f) => formData.append('files', f.raw, f.name))
      formData.append('engines', JSON.stringify(files.map((f) => f.engine || 'hybrid')))
      formData.append('pages', JSON.stringify(files.map((f) => f.pages || '')))
      formData.append('docmodes', JSON.stringify(files.map((f) => f.docmode || 'direct')))
      formData.append('split', autoSplit ? '1' : '0')
      if (hasApi) {
        formData.append(
          'api_config',
          JSON.stringify({
            provider: apiConfig.provider,
            api_key: apiConfig.apiKey,
            base_url: apiConfig.baseUrl,
            model: apiConfig.model,
            workspace: apiConfig.workspace || '',
          }),
        )
      }
      pushLog(`正在上传 ${files.length} 个文件（PDF/Word 会在上传时展开，可能需要几十秒）……`)
      const { data } = await axios.post('/upload', formData)
      const id = data.task_id
      setTaskId(id)
      pushLog(`上传完成，开始识别（首次运行需下载模型，请耐心等待）……`)

      // 2. 建立 SSE 连接，实时接收日志与进度
      const es = new EventSource(
        `/process/${id}?filename=${encodeURIComponent(outputName || 'output.docx')}&font=${encodeURIComponent(fontName)}&title=${encodeURIComponent(titlePrefix || '题目')}`,
      )
      eventSourceRef.current = es

      es.onmessage = (e) => {
        const evt = JSON.parse(e.data)
        if (evt.kind === 'log') {
          pushLog(evt.message)
        } else if (evt.kind === 'progress') {
          setProgress({ current: evt.current, total: evt.total })
        } else if (evt.kind === 'preview') {
          // 识别完成：进入预览编辑阶段，等用户确认后再生成 Word
          setPreviewQuestions(evt.questions || [])
          setFailed(evt.failed || [])
          if (evt.usage) {
            // 本任务 API token 用量汇总（含历史累计在「API 设置」里查看）
            pushLog(
              `📊 本次 API 用量：${evt.usage.calls} 次调用，共 ${evt.usage.total_tokens} tokens（输入 ${evt.usage.prompt_tokens} + 输出 ${evt.usage.completion_tokens}）`,
            )
          }
          setStatus('preview')
          es.close()
        } else if (evt.kind === 'done') {
          setStatus('done')
          setFailed(evt.failed || [])
          es.close()
        } else if (evt.kind === 'error') {
          setStatus('error')
          pushLog(`❌ 处理失败：${evt.message}`)
          es.close()
        }
      }
      es.onerror = () => {
        setStatus('error')
        pushLog('❌ 与后端的连接中断')
        es.close()
      }
    } catch (err) {
      setStatus('error')
      // 无 response 说明根本没连上后端（后端窗口被关闭或未启动）
      const detail = err.response
        ? err.response.data?.detail || err.message
        : '无法连接后端服务。请确认"截图转题目-后端"黑色窗口保持打开，然后刷新本页面重试。'
      pushLog(`❌ ${detail}`)
      message.error(detail)
    }
  }

  const processing = status === 'processing'

  // 预览编辑确认：把编辑后的每题 Markdown 发给后端生成文件
  const handleFinalize = async () => {
    if (!taskId || !previewQuestions) return
    setFinalizing(true)
    try {
      const { data } = await axios.post(`/finalize/${taskId}`, {
        questions: previewQuestions,
        filename: outputName || 'output',
        font: fontName,
        title: titlePrefix || '题目',
        format,
      })
      const filesOut = data.files || { docx: true, pdf: false }
      setResultFiles(filesOut)
      setStatus('done')
      if (data.pdf_error) {
        // both 模式下 PDF 转换失败：Word 已生成，降级提示
        pushLog(`⚠️ PDF 生成失败（Word 不受影响）：${data.pdf_error}`)
        message.warning('PDF 生成失败，已仅导出 Word 文件')
      }
      const label = format === 'pdf' ? 'PDF' : 'Word'
      pushLog(`✅ ${label} 生成完成，已自动开始下载。`)
      message.success(`${label} 生成完成，已开始下载`)
      // 生成成功后自动触发浏览器下载；双格式时错开时间避免浏览器拦截
      if (format === 'pdf') {
        triggerDownload(taskId, 'pdf')
      } else if (format === 'both' && filesOut.pdf) {
        triggerDownload(taskId, 'docx')
        setTimeout(() => triggerDownload(taskId, 'pdf'), 600)
      } else {
        triggerDownload(taskId, 'docx')
      }
    } catch (err) {
      const detail = err.response?.data?.detail || err.message
      pushLog(`❌ 生成文件失败：${detail}`)
      message.error(`生成文件失败：${detail}`)
    } finally {
      setFinalizing(false)
    }
  }

  return (
    <div className="app-container">
      {/* 全屏 WebGL 丝线背景（react-bits WebThreads，参数按需求固定） */}
      <WebThreads
        className="app-background"
        color1="#5227FF"
        color2="#FF9FFC"
        color3="#FFFFFF"
        speed={0.2}
        threadCount={6}
        frequency={9.5}
        spread={0.18}
        taper={1}
        position={0.47}
        fanMode="center"
        glow={0.015}
        falloff={0.6}
        thickness={1.1}
        brightness={0.6}
        opacity={0.55}
        mirror
        shimmer={false}
        grain
        grainIntensity={0.05}
        mouseInteraction
        mouseStrength={1}
      />
      {/* 页头 */}
      <div className="app-header">
        <h1>📄 截图转题目 Word</h1>
        <p>上传题目截图，自动识别文字与公式，生成可编辑的 Word 文件（本地模型 / API 双引擎）</p>
      </div>

      {/* Pandoc 缺失警告 */}
      {!pandocOk && (
        <Alert
          type="warning"
          showIcon
          message="后端未检测到 Pandoc"
          description="请安装 Pandoc 或将便携版放入 backend/pandoc/ 目录，否则无法生成 Word 文件。"
          style={{ marginBottom: 22 }}
        />
      )}

      {/* 第一行：上传（整行，高度随文件数量自适应） */}
      <div className="main-card">
        <h2 className="section-title">1. 上传题目文件</h2>
        <Uploader files={files} setFiles={setFiles} disabled={processing} />
        <div style={{ marginTop: 8, fontSize: 12, color: '#6b7280' }}>
          支持截图（png/jpg）、PDF（可填页码范围）、Word（直转重排零误差，OCR 模式可识别嵌图）；图片与
          PDF 可切换引擎：本地＝离线识别（免费较慢）；API＝云端识别（快，插图坐标可能不准）；混合＝API
          识别文字公式 + 本地裁剪插图（默认，推荐）
        </div>
      </div>

      {/* 第二行：左输出设置 / 右处理进度 */}
      <div className="row-grid">
        <div className="main-card">
          <h2 className="section-title">2. 输出设置</h2>

          {/* 每个设置项独占一行（输入框为下划线动画样式） */}
          <div className="setting-row">
            <span className="setting-label">输出文件：</span>
            <span className="underline-field">
              <Input
                value={outputName}
                onChange={(e) => setOutputName(e.target.value)}
                placeholder="输出文件名"
                style={{ width: 300 }}
                disabled={processing}
                addonAfter={{ docx: '.docx', pdf: '.pdf', both: '.docx+.pdf' }[format]}
                // 用户输入时不需要手动加后缀
                onBlur={(e) =>
                  setOutputName(e.target.value.replace(/\.(docx|pdf)$/i, '') || 'output')
                }
              />
            </span>
          </div>
          <div className="setting-row">
            <span className="setting-label">导出格式：</span>
            <Select
              value={format}
              onChange={setFormat}
              options={FORMAT_OPTIONS}
              style={{ width: 160 }}
              disabled={processing}
            />
          </div>
          <div className="setting-row">
            <span className="setting-label">整页拆题：</span>
            <div className={`flipswitch${processing ? ' flipswitch-disabled' : ''}`}>
              <input
                id="flipswitch-split"
                className="flipswitch-cb"
                type="checkbox"
                checked={autoSplit}
                onChange={(e) => setAutoSplit(e.target.checked)}
                disabled={processing}
              />
              <label htmlFor="flipswitch-split" className="flipswitch-label">
                <div className="flipswitch-inner" />
                <div className="flipswitch-switch" />
              </label>
            </div>
            <span style={{ color: '#9ca3af', fontSize: 12 }}>
              一张截图含多道题时按题号自动拆开
            </span>
          </div>
          <div className="setting-row">
            <span className="setting-label">中文字体：</span>
            <Select
              value={fontName}
              onChange={setFontName}
              options={FONT_OPTIONS}
              style={{ width: 160 }}
              disabled={processing}
            />
          </div>
          <div className="setting-row">
            <span className="setting-label">标题前缀：</span>
            <span className="underline-field">
              <Input
                value={titlePrefix}
                onChange={(e) => setTitlePrefix(e.target.value)}
                placeholder="题目"
                style={{ width: 140 }}
                maxLength={12}
                disabled={processing}
              />
            </span>
            <span style={{ color: '#9ca3af', fontSize: 12 }}>生成"{titlePrefix || '题目'} 1"样式标题</span>
          </div>

          <div style={{ marginTop: 24, display: 'flex', gap: 18, flexWrap: 'wrap' }}>
            <GlassButton
              onClick={handleStart}
              disabled={files.length === 0}
              loading={processing}
            >
              {processing ? '处理中…' : '开始处理'}
            </GlassButton>
            <GlassButton
              onClick={handleClear}
              disabled={processing}
            >
              清空文件
            </GlassButton>
            {/* API 设置收纳为按钮，点击弹出配置窗口（选择 API/混合 引擎时需要） */}
            <GlassButton
              onClick={() => setApiModalOpen(true)}
              disabled={processing}
            >
              API 设置
            </GlassButton>
            {/* 历史任务：重新下载 / 改格式再导出，无需重新识别 */}
            <GlassButton
              onClick={() => setHistoryOpen(true)}
              disabled={processing}
            >
              历史任务
            </GlassButton>
          </div>
        </div>

        <div className="main-card">
          <h2 className="section-title">3. 处理进度</h2>
          <ProgressBar
            current={progress.current}
            total={progress.total}
            status={status}
          />
          <div style={{ marginTop: 16 }}>
            <LogView logs={logs} />
          </div>

          {/* 处理完成后的下载区 */}
          {status === 'done' && (
            <DownloadBtn taskId={taskId} failed={failed} files={resultFiles} />
          )}
        </div>
      </div>

      {/* 识别预览与编辑（识别完成后出现，确认后生成 Word） */}
      {previewQuestions && (
        <div className="main-card preview-glass-card" style={{ marginTop: 22 }}>
          <h2 className="section-title">4. 识别预览与编辑</h2>
          <div style={{ marginBottom: 14, fontSize: 12, color: '#6b7280' }}>
            检查每题的识别结果，可直接修改文字与公式（$...$
            为行内公式）；形如 ![](figures/...) 的行是题目插图，请保留。
          </div>
          {previewQuestions.map((q, idx) => (
            <div key={idx} style={{ marginBottom: 18 }}>
              <div style={{ marginBottom: 6, fontWeight: 600, color: '#374151' }}>
                第 {idx + 1} 题
              </div>
              {/* 左编辑 / 右预览，并排实时联动 */}
              <div className="preview-pair">
                <Input.TextArea
                  value={q}
                  onChange={(e) =>
                    setPreviewQuestions((prev) =>
                      prev.map((item, i) => (i === idx ? e.target.value : item)),
                    )
                  }
                  style={{
                    fontFamily: 'Consolas, monospace',
                    fontSize: 13,
                    height: 320,
                    overflow: 'auto',
                    resize: 'none',
                  }}
                />
                <QuestionPreview text={q} taskId={taskId} />
              </div>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
            <GlassButton onClick={handleFinalize} loading={finalizing}>
              {finalizing
                ? '生成中…'
                : { docx: '生成 Word 文件', pdf: '生成 PDF 文件', both: '生成 Word + PDF' }[
                    format
                  ]}
            </GlassButton>
            {/* 生成成功后预览卡片里也保留下载入口，避免找不到文件 */}
            {status === 'done' && taskId && (resultFiles?.docx ?? true) && (
              <GlassButton onClick={() => triggerDownload(taskId, 'docx')}>
                重新下载 Word
              </GlassButton>
            )}
            {status === 'done' && taskId && resultFiles?.pdf && (
              <GlassButton onClick={() => triggerDownload(taskId, 'pdf')}>
                重新下载 PDF
              </GlassButton>
            )}
          </div>
        </div>
      )}

      {/* API 设置弹窗 */}
      <Modal
        title="API 设置（选择「API / 混合」引擎时生效）"
        open={apiModalOpen}
        onCancel={() => setApiModalOpen(false)}
        footer={null}
        width={560}
        destroyOnHidden={false}
      >
        <ApiSettings config={apiConfig} onChange={setApiConfig} disabled={processing} open={apiModalOpen} />
      </Modal>

      {/* 历史任务面板 */}
      <HistoryPanel
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        exportSettings={{ filename: outputName, font: fontName, title: titlePrefix, format }}
      />
    </div>
  )
}
