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
import DocPreview from './components/DocPreview'
import ApiSettings, { loadApiConfig } from './components/ApiSettings'
import HistoryPanel from './components/HistoryPanel'
import GlassButton from './components/GlassButton'
import Sidebar from './components/Sidebar'
import WebThreads from './components/WebThreads/WebThreads'

// Word 中文字体选项（与后端 docx_builder.AVAILABLE_FONTS 保持一致）
const FONT_OPTIONS = [
  { value: '仿宋', label: '仿宋' },
  { value: '黑体', label: '黑体' },
  { value: '楷体', label: '楷体' },
  { value: '宋体', label: '宋体' },
  { value: '微软雅黑', label: '微软雅黑' },
  { value: '等线', label: '等线' },
  { value: 'Noto Serif SC', label: '思源宋体' },
]

// 重新生成答案前，先剥离题目里已有的旧答案块（ANSWER_MARK 见下方模块级常量）
const stripAnswerBlock = (q) => {
  const idx = q.indexOf(ANSWER_MARK)
  return idx === -1 ? q.trimEnd() : q.slice(0, idx).trimEnd()
}

// 导出格式选项（PDF 由后端调用本机 Word / WPS / LibreOffice 转换）
const FORMAT_OPTIONS = [
  { value: 'docx', label: 'Word（.docx）' },
  { value: 'pdf', label: 'PDF（.pdf）' },
  { value: 'both', label: 'Word + PDF' },
]

// 触发浏览器下载（kind: docx / pdf；ver: teacher 默认 / student 学生版）
const triggerDownload = (taskId, kind, ver = 'teacher') => {
  const a = document.createElement('a')
  a.href = `/download/${taskId}?kind=${kind}&ver=${ver}`
  a.download = ''
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// 答案块标记（与后端 converter.ANSWER_MARK 保持一致）：
// 答案以 "> **【答案与解析】**" 开头的块引用追加在每题末尾，学生版据此剥离
const ANSWER_MARK = '> **【答案与解析】**'

// 是否已有题目带答案块（决定双版本相关 UI 是否出现）
const hasAnswers = (questions) => (questions || []).some((q) => q.includes(ANSWER_MARK))

export default function App() {
  // 已选文件列表 [{uid, name, raw(File), url(预览地址)}]
  const [files, setFiles] = useState([])
  // 输出文件名（不含后缀，后缀由导出格式决定）
  const [outputName, setOutputName] = useState('output')
  // Word 中文字体（默认仿宋）
  const [fontName, setFontName] = useState('仿宋')
  // 导出格式：docx / pdf / both
  const [format, setFormat] = useState('docx')
  // 一页多题自动拆分（默认关，需要时手动开启）
  const [autoSplit, setAutoSplit] = useState(false)
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
  // 页眉 / 页脚：文字 + 可选图片（如校徽 logo），图片大小可调
  const [headerSpec, setHeaderSpec] = useState({ text: '', image: '', size: 50 })
  const [footerSpec, setFooterSpec] = useState({ text: '', image: '', size: 50 })
  // 水印设置：type(none/text/image)、text、image(dataURL)、size(10-100)、opacity(5-100)、angle(-90~90)
  const [watermark, setWatermark] = useState({
    type: 'none',
    text: '',
    image: '',
    size: 50,
    opacity: 30,
    angle: -45,
  })
  // 双版本导出（学生版无答案 + 教师版含答案），默认开；仅题目带答案时生效
  const [dualVersion, setDualVersion] = useState(true)
  // 教师版页眉加「教师版（含答案）」标识，默认开
  const [teacherTag, setTeacherTag] = useState(true)
  // 正在生成答案（DeepSeek 请求中）
  const [answering, setAnswering] = useState(false)

  // SSE 连接引用，便于组件卸载时关闭
  const eventSourceRef = useRef(null)
  // 文档预览组件引用：生成答案成功后自动触发文档预览
  const docPreviewRef = useRef(null)
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
        enhance: [],
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

    // 有图片勾选优化项时，校验对应服务商密钥是否已填（缺哪个提示哪个）
    const usedEnhance = new Set(files.flatMap((f) => f.enhance || []))
    if (usedEnhance.size > 0) {
      const missing = []
      if (usedEnhance.has('white') && (!apiConfig.textinAppId || !apiConfig.textinSecret))
        missing.push('转白底（TextIn x-app-id / x-secret-code）')
      if (usedEnhance.has('dewm') && (!apiConfig.aliAk || !apiConfig.aliSk))
        missing.push('去水印（阿里云 AccessKey ID / Secret）')
      if (usedEnhance.has('erase_hw')) {
        const hasBaidu = apiConfig.baiduAk && apiConfig.baiduSk
        const hasTextin = apiConfig.textinAppId && apiConfig.textinSecret
        if (!hasBaidu && !hasTextin)
          missing.push('去手写（百度 API Key / Secret Key，或 TextIn x-app-id / x-secret-code，二选一）')
      }
      if (usedEnhance.has('sharpen') && (!apiConfig.baiduAk || !apiConfig.baiduSk))
        missing.push('清晰化（百度 API Key / Secret Key）')
      if (missing.length > 0) {
        setStatus('idle')
        message.warning(`已勾选图片优化，但缺少密钥：${missing.join('、')}，请在「API 设置 → 图片优化服务」填写`)
        setApiModalOpen(true)
        return
      }
    }

    try {
      // 1. 上传全部文件（附带每文件引擎选择、页码范围、Word 模式与 API 配置）
      const formData = new FormData()
      files.forEach((f) => formData.append('files', f.raw, f.name))
      formData.append('engines', JSON.stringify(files.map((f) => f.engine || 'hybrid')))
      formData.append('pages', JSON.stringify(files.map((f) => f.pages || '')))
      formData.append('docmodes', JSON.stringify(files.map((f) => f.docmode || 'direct')))
      formData.append('enhance', JSON.stringify(files.map((f) => f.enhance || [])))
      formData.append('split', autoSplit ? '1' : '0')
      if (usedEnhance.size > 0) {
        formData.append(
          'enhance_config',
          JSON.stringify({
            textin_app_id: apiConfig.textinAppId || '',
            textin_secret: apiConfig.textinSecret || '',
            ali_ak: apiConfig.aliAk || '',
            ali_sk: apiConfig.aliSk || '',
            baidu_ak: apiConfig.baiduAk || '',
            baidu_sk: apiConfig.baiduSk || '',
          }),
        )
      }
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

  // 生成答案：把当前预览题目发给 DeepSeek，答案块追加到对应题目末尾（可再编辑）
  const handleGenAnswers = async () => {
    if (!taskId || !previewQuestions) return
    if (!apiConfig.dsKey) {
      message.warning('请先在「API 设置」的「答案生成（DeepSeek）」区块填写 API Key')
      setApiModalOpen(true)
      return
    }
    setAnswering(true)
    pushLog('正在调用 DeepSeek 生成答案与解析……')
    try {
      const { data } = await axios.post(`/generate-answers/${taskId}`, {
        questions: previewQuestions,
        ds_config: {
          api_key: apiConfig.dsKey,
          base_url: apiConfig.dsBaseUrl,
          model: apiConfig.dsModel,
        },
      })
      const blocks = data.answers_md || []
      // 先合并答案到题目末尾，再一次性更新状态（自动预览时直接用 merged，
      // 避免 setState 未生效导致预览里没有答案）
      const merged = previewQuestions.map((q, i) =>
        blocks[i] ? `${stripAnswerBlock(q)}\n\n${blocks[i]}` : q,
      )
      setPreviewQuestions(merged)
      const okCount = blocks.filter(Boolean).length
      if (data.usage) {
        pushLog(
          `📊 答案生成用量：${data.usage.calls} 次调用，共 ${data.usage.total_tokens} tokens` +
          `（输入 ${data.usage.prompt_tokens} + 输出 ${data.usage.completion_tokens}）`,
        )
      }
      if (data.failed?.length) {
        pushLog(`⚠️ 第 ${data.failed.join('、')} 题答案生成失败，其余 ${okCount} 题已生成`)
        message.warning(`${data.failed.length} 题答案生成失败，可点击按钮重试`)
      } else {
        pushLog(`✅ ${okCount} 道题的答案与解析已生成，追加在每题末尾，可直接编辑。`)
        message.success('答案已生成并追加到每题末尾')
      }
      // 有任一题生成成功：自动弹出文档预览（教师版含答案），无需再点「生成文档预览」
      if (okCount > 0) {
        docPreviewRef.current?.refresh(merged)
        // 平滑滚动到预览区，让用户立刻看到生成过程与结果
        document.getElementById('doc-preview-section')?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        })
      }
    } catch (err) {
      const detail = err.response?.data?.detail || err.message
      pushLog(`❌ 答案生成失败：${detail}`)
      message.error(detail)
    } finally {
      setAnswering(false)
    }
  }

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
        // 双版本导出（学生版+教师版）与教师版页眉标识
        dual: dualVersion ? '1' : '0',
        teacher_tag: teacherTag ? '1' : '0',
        // 页眉 / 页脚（文字+可选图片）/ 水印装饰（水印不透明度 UI 为 5-100，后端用 0-1）
        header: headerSpec,
        footer: footerSpec,
        watermark: { ...watermark, opacity: watermark.opacity / 100 },
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
      pushLog(
        filesOut.dual
          ? `✅ 双版本 ${label} 生成完成（教师版含答案 / 学生版无答案），已自动开始下载。`
          : `✅ ${label} 生成完成，已自动开始下载。`,
      )
      message.success(`${label} 生成完成，已开始下载`)
      // 生成成功后自动触发浏览器下载；双版本/双格式时错开时间避免浏览器拦截
      const jobs = []
      if (format !== 'pdf') {
        jobs.push(['docx', 'teacher'])
        if (filesOut.dual) jobs.push(['docx', 'student'])
      }
      if (format !== 'docx' && filesOut.pdf) {
        jobs.push(['pdf', 'teacher'])
        if (filesOut.dual && filesOut.pdf_student) jobs.push(['pdf', 'student'])
      }
      jobs.forEach(([kind, ver], i) =>
        setTimeout(() => triggerDownload(taskId, kind, ver), i * 600),
      )
    } catch (err) {
      const detail = err.response?.data?.detail || err.message
      pushLog(`❌ 生成文件失败：${detail}`)
      message.error(`生成文件失败：${detail}`)
    } finally {
      setFinalizing(false)
    }
  }

  return (
    <div className="app-layout">
      {/* 左侧固定导航栏（页面滚动时保持原位） */}
      <Sidebar active="screenshot2word" />
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
            <div className={`checkbox-wrapper${processing ? ' checkbox-disabled' : ''}`}>
              <label className="toggleButton">
                <input
                  type="checkbox"
                  checked={autoSplit}
                  onChange={(e) => setAutoSplit(e.target.checked)}
                  disabled={processing}
                />
                <div>
                  <svg viewBox="0 0 44 44">
                    <path
                      transform="translate(-2.000000, -2.000000)"
                      d="M14,24 L21,31 L39.7428882,11.5937758 C35.2809627,6.53125861 30.0333333,4 24,4 C12.95,4 4,12.95 4,24 C4,35.05 12.95,44 24,44 C35.05,44 44,35.05 44,24 C44,19.3 42.5809627,15.1645919 39.7428882,11.5937758"
                    />
                  </svg>
                </div>
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
          <div className="setting-row">
            <span className="setting-label">双版本导出：</span>
            <div className={`checkbox-wrapper${processing ? ' checkbox-disabled' : ''}`}>
              <label className="toggleButton">
                <input
                  type="checkbox"
                  checked={dualVersion}
                  onChange={(e) => setDualVersion(e.target.checked)}
                  disabled={processing}
                />
                <div>
                  <svg viewBox="0 0 44 44">
                    <path
                      transform="translate(-2.000000, -2.000000)"
                      d="M14,24 L21,31 L39.7428882,11.5937758 C35.2809627,6.53125861 30.0333333,4 24,4 C12.95,4 4,12.95 4,24 C4,35.05 12.95,44 24,44 C35.05,44 44,35.05 44,24 C44,19.3 42.5809627,15.1645919 39.7428882,11.5937758"
                    />
                  </svg>
                </div>
              </label>
            </div>
            <span style={{ color: '#9ca3af', fontSize: 12 }}>
              生成答案后同时导出 学生版（无答案）+ 教师版（含答案）
            </span>
          </div>
          {dualVersion && (
            <div className="setting-row">
              <span className="setting-label">教师版标识：</span>
              <div className={`checkbox-wrapper${processing ? ' checkbox-disabled' : ''}`}>
                <label className="toggleButton">
                  <input
                    type="checkbox"
                    checked={teacherTag}
                    onChange={(e) => setTeacherTag(e.target.checked)}
                    disabled={processing}
                  />
                  <div>
                    <svg viewBox="0 0 44 44">
                      <path
                        transform="translate(-2.000000, -2.000000)"
                        d="M14,24 L21,31 L39.7428882,11.5937758 C35.2809627,6.53125861 30.0333333,4 24,4 C12.95,4 4,12.95 4,24 C4,35.05 12.95,44 24,44 C35.05,44 44,35.05 44,24 C44,19.3 42.5809627,15.1645919 39.7428882,11.5937758"
                      />
                    </svg>
                  </div>
                </label>
              </div>
              <span style={{ color: '#9ca3af', fontSize: 12 }}>
                教师版页眉标注「教师版（含答案）」，防止发错版本
              </span>
            </div>
          )}

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
          {/* 文档效果预览：页眉 / 页脚 / 水印设置，所见即最终导出效果 */}
          <div id="doc-preview-section" style={{ borderTop: '1px dashed #d1d5db', margin: '4px 0 18px', paddingTop: 16 }}>
            <div style={{ marginBottom: 12, fontWeight: 600, color: '#374151' }}>
              文档效果预览（页眉 / 页脚 / 水印）
            </div>
            <DocPreview
              ref={docPreviewRef}
              taskId={taskId}
              questions={previewQuestions}
              font={fontName}
              title={titlePrefix || '题目'}
              headerSpec={headerSpec}
              setHeaderSpec={setHeaderSpec}
              footerSpec={footerSpec}
              setFooterSpec={setFooterSpec}
              watermark={watermark}
              setWatermark={setWatermark}
              dual={dualVersion && hasAnswers(previewQuestions)}
              teacherTag={teacherTag}
            />
          </div>
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexWrap: 'wrap' }}>
            <GlassButton onClick={handleGenAnswers} loading={answering} disabled={finalizing}>
              {answering
                ? '生成答案中…'
                : hasAnswers(previewQuestions)
                  ? '重新生成答案'
                  : '生成答案（DeepSeek）'}
            </GlassButton>
            <GlassButton onClick={handleFinalize} loading={finalizing} disabled={answering}>
              {finalizing
                ? '生成中…'
                : (dualVersion && hasAnswers(previewQuestions)
                    ? { docx: '生成双版本 Word', pdf: '生成双版本 PDF', both: '生成双版本 Word + PDF' }
                    : { docx: '生成 Word 文件', pdf: '生成 PDF 文件', both: '生成 Word + PDF' })[
                    format
                  ]}
            </GlassButton>
            {/* 生成成功后预览卡片里也保留下载入口，避免找不到文件 */}
            {status === 'done' && taskId && (resultFiles?.docx ?? true) && (
              <GlassButton onClick={() => triggerDownload(taskId, 'docx', 'teacher')}>
                {resultFiles?.dual ? '下载教师版 Word' : '重新下载 Word'}
              </GlassButton>
            )}
            {status === 'done' && taskId && resultFiles?.docx_student && (
              <GlassButton onClick={() => triggerDownload(taskId, 'docx', 'student')}>
                下载学生版 Word
              </GlassButton>
            )}
            {status === 'done' && taskId && resultFiles?.pdf && (
              <GlassButton onClick={() => triggerDownload(taskId, 'pdf', 'teacher')}>
                {resultFiles?.dual ? '下载教师版 PDF' : '重新下载 PDF'}
              </GlassButton>
            )}
            {status === 'done' && taskId && resultFiles?.pdf_student && (
              <GlassButton onClick={() => triggerDownload(taskId, 'pdf', 'student')}>
                下载学生版 PDF
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
        exportSettings={{
          filename: outputName, font: fontName, title: titlePrefix, format,
          dual: dualVersion, teacherTag,
        }}
      />
      </div>
    </div>
  )
}
