// FileList.jsx —— 已选文件列表组件
// 图片：缩略图 + 引擎选择；PDF：图标 + 页码范围 + 引擎选择；
// Word：图标 + 处理模式（直转重排 / OCR 识别），OCR 模式下才显示引擎选择
// 引擎：本地（离线 Pix2Text）/ API（云端大模型）/ 混合（API 文字公式 + 本地裁剪插图）
import React from 'react'
import { Image, Button, Segmented, Tooltip, Input } from 'antd'
import { DeleteOutlined, FilePdfOutlined, FileWordOutlined } from '@ant-design/icons'

// 识别引擎选项（值与后端约定一致：local / api / hybrid）
const ENGINE_OPTIONS = [
  { label: '本地', value: 'local' },
  { label: 'API', value: 'api' },
  { label: '混合', value: 'hybrid' },
]

// 各引擎的说明（hover 提示）
const ENGINE_TIPS = {
  local: '本地模型：完全离线，速度慢但免费',
  api: '纯 API：云端识别文字公式，插图按模型坐标裁剪（可能不准）',
  hybrid: '混合（推荐）：API 识别文字公式 + 本地版面分析裁剪插图',
}

// Word 处理模式选项
const DOCMODE_OPTIONS = [
  { label: '直转重排', value: 'direct' },
  { label: 'OCR 识别', value: 'ocr' },
]

const DOCMODE_TIPS = {
  direct: '直转重排（推荐）：Word 里是文字版题目时，文字公式零误差直接转换，不占识别额度',
  ocr: 'OCR 识别：Word 里嵌的是题目截图时选这个，先转成图片再走识别引擎',
}

export default function FileList({
  files,
  onRemove,
  onEngineChange,
  onPagesChange,
  onDocmodeChange,
  disabled,
}) {
  if (files.length === 0) return null

  return (
    <div className="thumb-list">
      {/* antd Image.PreviewGroup 提供点击图片放大浏览能力 */}
      <Image.PreviewGroup>
        {files.map((f, idx) => {
          const kind = f.kind || 'image' // 兼容没有 kind 字段的文件项（如粘贴的截图）
          return (
          <div className="thumb-item" key={f.uid}>
            {kind === 'image' ? (
              <Image src={f.url} alt={f.name} width={96} height={96} />
            ) : (
              <div className="thumb-file-icon">
                {kind === 'pdf' ? <FilePdfOutlined /> : <FileWordOutlined />}
              </div>
            )}
            <div className="thumb-name" title={f.name}>
              {idx + 1}. {f.name}
            </div>
            {/* PDF：页码范围（空 = 全部页） */}
            {kind === 'pdf' && (
              <Tooltip title="只识别部分页时填写，如 1-5,8；留空为全部页（单次最多 20 页）">
                <Input
                  size="small"
                  placeholder="页码：全部，如 1-5,8"
                  value={f.pages}
                  onChange={(e) => onPagesChange(f.uid, e.target.value)}
                  disabled={disabled}
                  style={{ marginTop: 6, fontSize: 12 }}
                />
              </Tooltip>
            )}
            {/* Word：处理模式（直转重排 / OCR 识别） */}
            {kind === 'docx' && (
              <Tooltip title={DOCMODE_TIPS[f.docmode || 'direct']}>
                <Segmented
                  size="small"
                  options={DOCMODE_OPTIONS}
                  value={f.docmode || 'direct'}
                  onChange={(value) => onDocmodeChange(f.uid, value)}
                  disabled={disabled}
                />
              </Tooltip>
            )}
            {/* 识别引擎：图片 / PDF 总是显示；Word 仅 OCR 模式需要 */}
            {(kind !== 'docx' || f.docmode === 'ocr') && (
              <Tooltip title={ENGINE_TIPS[f.engine || 'hybrid']}>
                <Segmented
                  size="small"
                  options={ENGINE_OPTIONS}
                  value={f.engine || 'hybrid'}
                  onChange={(value) => onEngineChange(f.uid, value)}
                  disabled={disabled}
                />
              </Tooltip>
            )}
            {!disabled && (
              <Button
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => onRemove(f.uid)}
              />
            )}
          </div>
          )
        })}
      </Image.PreviewGroup>
    </div>
  )
}
