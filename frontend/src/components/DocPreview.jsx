// DocPreview.jsx —— 文档效果预览 + 页眉 / 页脚 / 水印设置
// 页眉、页脚：文字 + 可选图片（如校徽 logo），图片大小可调；
// 水印：文字 / 图片两种，均可调 大小、不透明度、角度。
// 点击「生成文档预览」后，后端把当前编辑内容生成 docx（含装饰），
// 经本机 Word/WPS 转成 PDF 再渲染为逐页图片返回，所见即最终导出效果。
import React, { useState } from 'react'
import { Input, Select, Slider, Upload, message } from 'antd'
import axios from 'axios'
import GlassButton from './GlassButton'

// 水印类型选项
const WM_TYPE_OPTIONS = [
  { value: 'none', label: '无水印' },
  { value: 'text', label: '文字水印' },
  { value: 'image', label: '图片水印' },
]

// 读取本地图片为 dataURL（不发网络请求，生成预览 / 导出时随 JSON 发给后端）
const readImage = (file, onDone) => {
  if (!/^image\/(png|jpe?g)$/i.test(file.type)) {
    message.warning('图片仅支持 png / jpg')
    return false
  }
  const reader = new FileReader()
  reader.onload = () => onDone(reader.result)
  reader.readAsDataURL(file)
  return false
}

// 页眉 / 页脚编辑行：文字输入 + 图片选择 + 图片大小
function HFEditor({ label, spec, setSpec }) {
  return (
    <>
      <div className="setting-row">
        <span className="setting-label">{label}：</span>
        <span className="underline-field">
          <Input
            value={spec.text}
            onChange={(e) => setSpec((prev) => ({ ...prev, text: e.target.value }))}
            placeholder={`留空且不选图片则无${label}`}
            style={{ width: 260 }}
            maxLength={100}
          />
        </span>
        <Upload
          accept="image/png,image/jpeg"
          showUploadList={false}
          beforeUpload={(f) => readImage(f, (url) => setSpec((prev) => ({ ...prev, image: url })))}
        >
          <GlassButton style={{ marginLeft: 12 }}>
            {spec.image ? '更换图片' : '添加图片'}
          </GlassButton>
        </Upload>
        {spec.image && (
          <>
            <img
              src={spec.image}
              alt={`${label}图片`}
              style={{
                height: 36,
                marginLeft: 10,
                borderRadius: 6,
                border: '1px solid #e5e7eb',
                verticalAlign: 'middle',
              }}
            />
            <a
              style={{ marginLeft: 8, fontSize: 12 }}
              onClick={() => setSpec((prev) => ({ ...prev, image: '' }))}
            >
              移除
            </a>
          </>
        )}
      </div>
      {/* 选了图片才显示大小调节 */}
      {spec.image && (
        <div className="setting-row">
          <span className="setting-label">{label}图片大小：</span>
          <Slider
            style={{ width: 220 }}
            min={10}
            max={100}
            value={spec.size}
            onChange={(v) => setSpec((prev) => ({ ...prev, size: v }))}
          />
          <span style={{ color: '#6b7280', fontSize: 12, width: 44 }}>{spec.size}%</span>
        </div>
      )}
    </>
  )
}

export default function DocPreview({
  taskId,
  questions,
  font,
  title,
  headerSpec,
  setHeaderSpec,
  footerSpec,
  setFooterSpec,
  watermark,
  setWatermark,
}) {
  // 预览页图片相对路径列表；stamp 用于强制浏览器刷新同名图片
  const [pages, setPages] = useState([])
  const [stamp, setStamp] = useState(0)
  const [loading, setLoading] = useState(false)

  // 局部更新水印设置
  const setWm = (patch) => setWatermark((prev) => ({ ...prev, ...patch }))

  // 生成 / 刷新文档预览
  const handlePreview = async () => {
    if (!taskId || !questions?.length) return
    setLoading(true)
    try {
      const { data } = await axios.post(`/preview-doc/${taskId}`, {
        questions,
        font,
        title,
        header: headerSpec,
        footer: footerSpec,
        watermark: { ...watermark, opacity: watermark.opacity / 100 },
      })
      setPages(data.pages || [])
      setStamp(Date.now())
      message.success(`预览已生成，共 ${(data.pages || []).length} 页`)
    } catch (err) {
      const detail = err.response?.data?.detail || err.message
      message.error(detail)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      {/* 页眉 / 页脚（文字 + 可选图片） */}
      <HFEditor label="页眉" spec={headerSpec} setSpec={setHeaderSpec} />
      <HFEditor label="页脚" spec={footerSpec} setSpec={setFooterSpec} />

      {/* 水印设置 */}
      <div className="setting-row">
        <span className="setting-label">水印：</span>
        <Select
          value={watermark.type}
          onChange={(v) => setWm({ type: v })}
          options={WM_TYPE_OPTIONS}
          style={{ width: 130 }}
        />
        {watermark.type === 'text' && (
          <span className="underline-field" style={{ marginLeft: 12 }}>
            <Input
              value={watermark.text}
              onChange={(e) => setWm({ text: e.target.value })}
              placeholder="水印文字"
              style={{ width: 220 }}
              maxLength={30}
            />
          </span>
        )}
        {watermark.type === 'image' && (
          <span style={{ marginLeft: 12, display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            <Upload
              accept="image/png,image/jpeg"
              showUploadList={false}
              beforeUpload={(f) => readImage(f, (url) => setWm({ image: url, type: 'image' }))}
            >
              <GlassButton>{watermark.image ? '更换图片' : '选择图片'}</GlassButton>
            </Upload>
            {watermark.image && (
              <img
                src={watermark.image}
                alt="水印图片"
                style={{ height: 40, borderRadius: 6, border: '1px solid #e5e7eb' }}
              />
            )}
          </span>
        )}
      </div>

      {/* 水印参数：大小 / 不透明度 / 角度（选了水印才显示） */}
      {watermark.type !== 'none' && (
        <>
          <div className="setting-row">
            <span className="setting-label">大小：</span>
            <Slider
              style={{ width: 220 }}
              min={10}
              max={100}
              value={watermark.size}
              onChange={(v) => setWm({ size: v })}
            />
            <span style={{ color: '#6b7280', fontSize: 12, width: 44 }}>{watermark.size}%</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">不透明度：</span>
            <Slider
              style={{ width: 220 }}
              min={5}
              max={100}
              value={watermark.opacity}
              onChange={(v) => setWm({ opacity: v })}
            />
            <span style={{ color: '#6b7280', fontSize: 12, width: 44 }}>{watermark.opacity}%</span>
          </div>
          <div className="setting-row">
            <span className="setting-label">角度：</span>
            <Slider
              style={{ width: 220 }}
              min={-90}
              max={90}
              value={watermark.angle}
              onChange={(v) => setWm({ angle: v })}
            />
            <span style={{ color: '#6b7280', fontSize: 12, width: 44 }}>{watermark.angle}°</span>
          </div>
        </>
      )}

      {/* 生成预览按钮与提示 */}
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', margin: '6px 0 14px' }}>
        <GlassButton onClick={handlePreview} loading={loading}>
          {loading ? '生成预览中…' : pages.length ? '刷新文档预览' : '生成文档预览'}
        </GlassButton>
        <span style={{ color: '#9ca3af', fontSize: 12 }}>
          预览 = 最终导出效果（经本机 Word/WPS 转换渲染，约需几秒）
        </span>
      </div>

      {/* 逐页预览图 */}
      {pages.length > 0 && (
        <div
          style={{
            maxHeight: 620,
            overflowY: 'auto',
            background: '#f3f4f6',
            borderRadius: 10,
            padding: 14,
          }}
        >
          {pages.map((p) => (
            <img
              key={p}
              src={`/task-files/${taskId}/${p}?t=${stamp}`}
              alt={`第 ${p} 页预览`}
              style={{
                display: 'block',
                width: '100%',
                maxWidth: 720,
                margin: '0 auto 14px',
                boxShadow: '0 2px 10px rgba(0,0,0,0.18)',
                borderRadius: 4,
                background: '#fff',
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}
