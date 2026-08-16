// Uploader.jsx —— 上传区组件
// 支持拖拽或点击选择多个题目文件（图片 png/jpg/jpeg、PDF、Word docx），选择后立即显示预览
import React from 'react'
import { Upload } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import FileList from './FileList'

const { Dragger } = Upload

// 允许的文件类型
const ACCEPT = '.png,.jpg,.jpeg,.pdf,.docx'

// 文件类型归类：image / pdf / docx
const kindOf = (name) => {
  if (/\.pdf$/i.test(name)) return 'pdf'
  if (/\.docx$/i.test(name)) return 'docx'
  return 'image'
}

export default function Uploader({ files, setFiles, disabled }) {
  // 选择文件（拦截默认上传行为，仅维护本地文件列表）
  const beforeUpload = (file) => {
    const ok = /\.(png|jpe?g|pdf|docx)$/i.test(file.name)
    if (!ok) return Upload.LIST_IGNORE // 不支持的类型直接忽略
    const kind = kindOf(file.name)
    // 为每个文件生成唯一 id；图片生成预览 URL，PDF/Word 用图标占位
    // 识别引擎默认混合模式（API 文字公式 + 本地裁图）；
    // Word 默认直转重排（不需要识别引擎，engine 固定 local 避免触发 API 配置校验）
    const item = {
      uid: `${Date.now()}-${file.name}`,
      name: file.name,
      raw: file,
      kind,
      url: kind === 'image' ? URL.createObjectURL(file) : null,
      engine: kind === 'docx' ? 'local' : 'hybrid',
      pages: '',          // 仅 PDF：页码范围，如 1-5,8（空 = 全部）
      docmode: 'direct',  // 仅 Word：direct 直转重排 / ocr 截图识别
    }
    setFiles((prev) => [...prev, item])
    return false // 阻止 antd 自动上传
  }

  // 删除单个文件
  const removeFile = (uid) => {
    setFiles((prev) => {
      const target = prev.find((f) => f.uid === uid)
      if (target?.url) URL.revokeObjectURL(target.url) // 释放预览内存
      return prev.filter((f) => f.uid !== uid)
    })
  }

  // 切换单个文件的识别引擎（本地模型 / API）
  const changeEngine = (uid, engine) => {
    setFiles((prev) => prev.map((f) => (f.uid === uid ? { ...f, engine } : f)))
  }

  // 修改 PDF 页码范围
  const changePages = (uid, pages) => {
    setFiles((prev) => prev.map((f) => (f.uid === uid ? { ...f, pages } : f)))
  }

  // 切换 Word 处理模式（直转重排 / OCR 识别）
  const changeDocmode = (uid, docmode) => {
    setFiles((prev) => prev.map((f) => (f.uid === uid ? { ...f, docmode } : f)))
  }

  return (
    <div>
      <Dragger
        multiple
        accept={ACCEPT}
        beforeUpload={beforeUpload}
        showUploadList={false} // 使用自定义文件列表
        disabled={disabled}
        style={{ background: '#fbfcff' }}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined style={{ color: '#4f6ef7' }} />
        </p>
        <p className="ant-upload-text">点击、拖拽或 Ctrl+V 粘贴题目截图 / PDF / Word</p>
        <p className="ant-upload-hint">
          支持 png / jpg / jpeg / pdf / docx，可一次多个，也可直接粘贴剪贴板截图
        </p>
      </Dragger>

      {/* 已选文件列表（可点击放大、可删除、可切换识别引擎 / 页码范围 / Word 模式） */}
      <FileList
        files={files}
        onRemove={removeFile}
        onEngineChange={changeEngine}
        onPagesChange={changePages}
        onDocmodeChange={changeDocmode}
        disabled={disabled}
      />
    </div>
  )
}
