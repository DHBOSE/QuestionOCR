# -*- coding: utf-8 -*-
"""
api.py —— FastAPI 主文件

接口：
    POST /upload              上传多张题目截图，返回 task_id
    GET  /process/{task_id}   开始处理（SSE 实时推送进度与日志）
    GET  /download/{task_id}  下载生成的 Word 文件

运行方式：
    python api.py           # 默认监听 http://localhost:8000
"""

import json
import logging
import queue
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from recognizer import recognize_question, detect_figure_crops
from api_recognizer import api_recognize_question, resolve_api_config, PROVIDERS
from converter import (
    elements_to_markdown, merge_questions, tag_figure_captions, reassign_question_figures,
    normalize_option_lines, restore_fill_blanks, merge_figure_only_items,
)
from docx_builder import markdown_to_docx, find_pandoc, AVAILABLE_FONTS, DEFAULT_FONT
from pdf_builder import docx_to_pdf
from doc_decor import apply_decorations, normalize_watermark, normalize_hf
from splitter import split_image
from doc_loader import pdf_to_images, docx_to_pdf_pages, docx_to_questions

# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent

# 日志同时输出到控制台和 backend.log 文件，窗口关闭后也能排查问题
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "backend.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

TEMP_ROOT = BASE_DIR / "temp"            # 所有任务的临时文件根目录
TASK_EXPIRE_SECONDS = 2 * 3600           # 未产出结果的任务保留时长（2 小时）
HISTORY_KEEP_SECONDS = 7 * 24 * 3600     # 有识别结果的历史任务保留时长（7 天）
ALLOWED_SUFFIX = {".png", ".jpg", ".jpeg", ".pdf", ".docx"}
IMAGE_SUFFIX = {".png", ".jpg", ".jpeg"}
# API / 混合 识别是网络瓶颈（单次约 20 秒），并行收益大；
# 本地 CPU 识别是计算瓶颈，仍保持串行（并行只会互相抢占更慢）
API_MAX_WORKERS = 3

TEMP_ROOT.mkdir(exist_ok=True)

# 任务状态表（内存存储 + 每任务 task.json 落盘，重启后历史任务可恢复）
TASKS: dict[str, dict] = {}

# API token 用量累计统计（跨任务持久化，本地单用户场景用 JSON 文件即可）
USAGE_FILE = BASE_DIR / "api_usage.json"
_usage_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 任务持久化与用量统计
# ---------------------------------------------------------------------------
def save_task_meta(task_id: str) -> None:
    """把任务的关键信息写入 task_dir/task.json，供历史任务列表与重启恢复使用。"""
    task = TASKS.get(task_id)
    if not task:
        return
    meta = {
        "task_id": task_id,
        "created_at": task.get("created_at"),
        "orig_names": task.get("orig_names", []),
        "engines": task.get("engines", []),
        "split": task.get("split", True),
        "question_mds": task.get("question_mds"),
        "failed": task.get("failed", []),
        "usage": task.get("usage", []),
        "output_stem": task.get("output_stem"),
        "has_docx": bool(task.get("docx_path") and Path(task["docx_path"]).exists()),
        "has_pdf": bool(task.get("pdf_path") and Path(task["pdf_path"]).exists()),
    }
    try:
        (task["dir"] / "task.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as e:
        logger.warning("任务 %s 元信息写入失败：%s", task_id[:8], e)


def load_persisted_tasks() -> None:
    """启动时扫描 temp 目录，把未过期的历史任务恢复到内存（可重新下载/再导出）。"""
    now = time.time()
    for task_dir in sorted(TEMP_ROOT.iterdir() if TEMP_ROOT.exists() else []):
        meta_path = task_dir / "task.json"
        if not task_dir.is_dir() or not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        created = meta.get("created_at") or task_dir.stat().st_mtime
        if now - created > HISTORY_KEEP_SECONDS:
            shutil.rmtree(task_dir, ignore_errors=True)
            continue
        task_id = meta.get("task_id") or task_dir.name
        if task_id in TASKS:
            continue
        uploads = sorted((task_dir / "uploads").glob("*.*")) if (task_dir / "uploads").exists() else []
        stem = meta.get("output_stem")
        docx_path = task_dir / f"{stem}.docx" if stem else None
        pdf_path = task_dir / f"{stem}.pdf" if stem else None
        TASKS[task_id] = {
            "dir": task_dir,
            "files": [str(p) for p in uploads],
            "engines": meta.get("engines", []),
            "api_config": {},  # 密钥从不落盘，历史任务不含 API 配置
            "split": meta.get("split", True),
            "orig_names": meta.get("orig_names", []),
            "question_mds": meta.get("question_mds"),
            "failed": meta.get("failed", []),
            "usage": meta.get("usage", []),
            "output_stem": stem,
            "status": "finished",
            "created_at": created,
            "docx_path": str(docx_path) if docx_path and docx_path.exists() else None,
            "pdf_path": str(pdf_path) if pdf_path and pdf_path.exists() else None,
            "error": None,
        }
    if TASKS:
        logger.info("已恢复 %d 个历史任务", len(TASKS))


def record_api_usage(usage: dict) -> None:
    """把单次 API 识别的 token 用量累计到本地统计文件（线程安全）。"""
    if not usage:
        return
    with _usage_lock:
        try:
            data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {"total": {}, "by_model": {}}
        for bucket in (data.setdefault("total", {}),
                       data.setdefault("by_model", {}).setdefault(usage.get("model") or "unknown", {})):
            bucket["calls"] = bucket.get("calls", 0) + 1
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                bucket[k] = bucket.get(k, 0) + int(usage.get(k) or 0)
        try:
            USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            logger.warning("用量统计写入失败：%s", e)

app = FastAPI(title="截图转题目 Word")

# 允许前端 dev server（默认 5173 端口）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def cleanup_expired_tasks() -> None:
    """清理过期的任务临时目录：未产出结果的 2 小时清掉，有识别结果的历史任务保留 7 天。"""
    now = time.time()
    for task_id, task in list(TASKS.items()):
        keep = HISTORY_KEEP_SECONDS if task.get("question_mds") else TASK_EXPIRE_SECONDS
        if now - task.get("created_at", now) > keep:
            shutil.rmtree(task["dir"], ignore_errors=True)
            TASKS.pop(task_id, None)
            logger.info("已清理过期任务：%s", task_id)


def _expand_upload_plan(saved, orig_names, engines, pages, docmodes, task_dir) -> list:
    """
    把上传文件展开成有序工作清单（上传时一次性完成）：
    - 图片     → 一条 image 项；
    - PDF      → 指定页渲染成高清图，每页一条 image 项（继承该文件的引擎选择）；
    - Word 直转 → pandoc 转 Markdown 按题号切题，每题一条 preset 项（不占用识别额度）；
    - Word OCR → docx → PDF → 页图，每页一条 image 项（用于 Word 里嵌的是题目截图的场景）。
    展开失败（加密 PDF、页码范围非法、Word 解析失败等）抛 RuntimeError，由上层转 400。
    """
    plan = []
    for idx, path in enumerate(saved):
        suffix = Path(path).suffix.lower()
        engine = engines[idx] if idx < len(engines) else "local"
        name = orig_names[idx] if idx < len(orig_names) else Path(path).name
        if suffix in IMAGE_SUFFIX:
            plan.append({"kind": "image", "path": path, "engine": engine, "name": name})
        elif suffix == ".pdf":
            page_spec = pages[idx] if idx < len(pages) else ""
            logger.info("PDF 文件 %s：渲染页图（范围：%s）", name, page_spec or "全部")
            page_imgs = pdf_to_images(path, str(task_dir / "pages" / f"f{idx + 1:02d}"), page_spec)
            for pno, p in enumerate(page_imgs, 1):
                plan.append({"kind": "image", "path": p, "engine": engine,
                             "name": f"{name}（第 {pno} 页）"})
        elif suffix == ".docx":
            mode = docmodes[idx] if idx < len(docmodes) else "direct"
            if mode == "ocr":
                logger.info("Word 文件 %s：OCR 模式，先转 PDF 再渲染页图", name)
                page_imgs = docx_to_pdf_pages(path, str(task_dir / "pages" / f"f{idx + 1:02d}"))
                for pno, p in enumerate(page_imgs, 1):
                    plan.append({"kind": "image", "path": p, "engine": engine,
                                 "name": f"{name}（第 {pno} 页）"})
            else:
                logger.info("Word 文件 %s：直转模式，pandoc 解析为 Markdown", name)
                for label, md in docx_to_questions(path, str(task_dir), f"direct/f{idx + 1:02d}"):
                    plan.append({"kind": "preset", "label": label, "md": md})
    if not plan:
        raise RuntimeError("上传的文件没有可处理的内容。")
    return plan


def process_task(task_id: str, output_name: str, font_name: str, log_queue: queue.Queue, title_prefix: str = "题目") -> None:
    """
    后台线程：并行识别（API/混合 最多 API_MAX_WORKERS 张并发，本地引擎串行）
    → 推送预览 Markdown → 等 /finalize 合并生成文件。
    所有进度与日志通过 log_queue 推送给 SSE 前端。
    每张图片按 task["engines"] 独立选择本地模型或远程 API 识别。
    """
    task = TASKS[task_id]

    def log(msg: str):
        log_queue.put({"kind": "log", "message": msg})
        logger.info("[%s] %s", task_id[:8], msg)

    try:
        task_dir = task["dir"]
        api_config = task.get("api_config", {})
        # 工作清单：上传时已把 PDF/Word 展开成 plan（页图 / 直转题目）；
        # 无 plan 的老任务（重启恢复等）从 files/engines 重建纯图片清单
        plan = task.get("plan")
        if plan is None:
            engines = task.get("engines", [])
            orig = task.get("orig_names") or []
            plan = [
                {"kind": "image", "path": p,
                 "engine": engines[i] if i < len(engines) else "local",
                 "name": orig[i] if i < len(orig) else Path(p).name}
                for i, p in enumerate(task["files"])
            ]
        # 一页多题自动拆分（默认开，前端可关）：图片项先串行检测拆分，
        # 展开成工作项列表 (label, 图片路径, 引擎, 题号, 直转Markdown)，再并行识别
        split_enabled = task.get("split", True)
        work_items = []  # (label, img_path, engine, qnum, preset_md)
        split_seq = 0
        for item in plan:
            if item["kind"] == "preset":
                # Word 直转出的题目：无需识别，Markdown 直接进预览
                work_items.append((item["label"], None, None, None, item["md"]))
                continue
            img_path = item["path"]
            engine = item.get("engine", "local")
            name = item.get("name") or Path(img_path).name
            if split_enabled:
                split_seq += 1
                log(f"正在检测 {name} 是否包含多道题……")
                sub_items = split_image(img_path, str(task_dir / "splits" / f"p{split_seq}"), log=log)
            else:
                sub_items = [(img_path, None)]
            for sub_idx, (sub, qnum) in enumerate(sub_items, start=1):
                label = name if len(sub_items) == 1 else f"{name} 第 {sub_idx} 题"
                work_items.append((label, sub, engine, qnum, None))

        total = len(work_items)
        results = {}          # 工作项序号 -> (元素列表, 题号)（保证最终顺序与上传一致）
        failed = []
        done_count = [0]
        # 本地 CPU 识别串行锁：Pix2Text 单例非线程安全，且 CPU 并行无收益
        local_lock = threading.Lock()

        def recognize_one(widx: int, label: str, img_path: str, engine: str, preset_md: str | None):
            """识别单张（子）图，返回 (widx, label, engine, 元素或Markdown)；异常抛给上层汇总。
            preset_md 非空时是 Word 直转出的题目，无需识别直接短路返回。"""
            if preset_md is not None:
                return widx, label, "preset", preset_md
            figure_dir = task_dir / "figures" / f"w{widx}"  # 每个工作项独立的插图目录
            usage = None
            if engine in ("api", "hybrid"):
                # 远程 API 识别：密钥不落盘，仅在本任务内存中使用
                cfg = resolve_api_config(**api_config)
                result = api_recognize_question(
                    img_path, str(figure_dir), cfg, log=log,
                    skip_figures=(engine == "hybrid"),
                )
                usage = result.get("usage")
                if engine == "hybrid":
                    # 混合模式：插图改由本地版面分析裁剪（API bbox 不够准）
                    # API 已输出 Markdown 表格时跳过本地表格裁图，避免一表两出
                    has_md_table = any(
                        "---" in ln and "|" in ln
                        for el in result["elements"] if el.get("type") != "image"
                        for ln in (el.get("text") or "").split("\n")
                    )
                    result["elements"].extend(
                        detect_figure_crops(img_path, str(figure_dir), log=log,
                                            skip_tables=has_md_table)
                    )
            else:
                # 本地模型识别（Pix2Text + UniMERNet）：CPU 计算瓶颈，串行执行
                with local_lock:
                    result = recognize_question(img_path, str(figure_dir), log=log)
            # 元素级图注配对（插图元素写入 caption），供跨题重分配与 Markdown 图注使用
            tag_figure_captions(result["elements"])
            if usage:
                # 本任务用量汇总 + 全局累计统计（写 api_usage.json）
                usage = {**usage, "label": label}
                task.setdefault("usage", []).append(usage)
                record_api_usage(usage)
                log(f"  本次 API 用量：输入 {usage['prompt_tokens']} + 输出 {usage['completion_tokens']} = {usage['total_tokens']} tokens")
            return widx, label, engine, result["elements"]

        log_queue.put({"kind": "progress", "current": 0, "total": total})
        log(f"开始并行识别 {total} 道题（API/混合最多 {min(API_MAX_WORKERS, total)} 路并发，本地引擎串行）……")
        with ThreadPoolExecutor(max_workers=API_MAX_WORKERS) as pool:
            futures = {
                pool.submit(recognize_one, widx, label, path, engine, preset_md): (widx, label)
                for widx, (label, path, engine, _qnum, preset_md) in enumerate(work_items, start=1)
            }
            for fut in as_completed(futures):
                widx, label = futures[fut]
                try:
                    _, label, engine, payload = fut.result()
                    results[widx] = {
                        "kind": "preset" if engine == "preset" else "elements",
                        "md": payload if engine == "preset" else None,
                        "elements": None if engine == "preset" else payload,
                        "qnum": work_items[widx - 1][3],
                    }
                    engine_label = {"api": "API", "hybrid": "混合", "preset": "直转"}.get(engine, "本地")
                    log(f"✅ 处理成功（{engine_label}）：{label}")
                except Exception as e:  # 单项失败不中断整体任务
                    failed.append(label)
                    log(f"❌ 识别失败：{label}（{e}）")
                done_count[0] += 1
                # 并行模式下进度按"已完成数"推进（current=已完成数）
                log_queue.put({"kind": "progress", "current": done_count[0], "total": total, "file": label})

        # 按上传顺序排列，与并行完成顺序无关
        ordered = [results[i] for i in sorted(results)]
        if not ordered:
            raise RuntimeError("所有内容均处理失败，无法生成 Word 文件。")

        # 纯图项并入上一题：PDF 换页可能把某题的插图单独切到下一页（整页只有图），
        # 单列一道"题"会让用户困惑；合并后再做跨题重分配，带"第 N 题图"注的图仍会搬对
        ordered, fig_merges = merge_figure_only_items(ordered)
        if fig_merges:
            log(f"  检测到 {fig_merges} 个纯插图项（PDF 换页所致），已并入上一题")

        # 跨题插图重分配：一页多题插图混排时（两题的图并排放在中间），
        # 图注"第 N 题图"的插图搬到对应题号名下（只对真实识别出的题目做，直转题目不参与）
        el_items = [r for r in ordered if r["kind"] == "elements"]
        if el_items:
            reassign_question_figures([r["elements"] for r in el_items],
                                      [r["qnum"] for r in el_items])

        question_mds = []
        for r in ordered:
            if r["kind"] == "preset":
                # 直转 Markdown 走与识别结果相同的后处理（选项分行/填空还原，幂等），预览风格一致
                question_mds.append(restore_fill_blanks(normalize_option_lines(r["md"])))
            else:
                question_mds.append(elements_to_markdown(r["elements"], str(task_dir)))

        # 识别完成：把每题的 Markdown 推给前端预览编辑，
        # 等用户在前端确认后由 /finalize 接口合并生成 Word
        task["question_mds"] = question_mds
        task["failed"] = failed
        # 本任务 API 用量汇总（无 API 识别时为 None）
        task_usage = None
        if task.get("usage"):
            task_usage = {
                "calls": len(task["usage"]),
                "prompt_tokens": sum(u["prompt_tokens"] for u in task["usage"]),
                "completion_tokens": sum(u["completion_tokens"] for u in task["usage"]),
                "total_tokens": sum(u["total_tokens"] for u in task["usage"]),
            }
        save_task_meta(task_id)  # 落盘，历史任务可恢复
        log_queue.put({"kind": "preview", "questions": question_mds, "failed": failed, "usage": task_usage})
        log("✅ 识别完成，请在下方检查并编辑识别结果，确认后点击「生成 Word 文件」。")

    except Exception as e:
        task["error"] = str(e)
        log_queue.put({"kind": "error", "message": str(e)})
        logger.error("任务 %s 处理失败：%s", task_id, e)
    finally:
        task["status"] = "finished"


# ---------------------------------------------------------------------------
# API 接口
# ---------------------------------------------------------------------------
@app.post("/upload")
async def upload(
    files: list[UploadFile] = File(...),
    engines: str = Form(""),
    api_config: str = Form(""),
    split: str = Form("1"),
    pages: str = Form(""),
    docmodes: str = Form(""),
):
    """
    接收题目文件（截图 / PDF / Word），保存到临时目录并展开成工作清单，返回任务 ID。
    engines:    JSON 数组字符串，与 files 一一对应（"local" / "api" / "hybrid"），缺省全为本地
    api_config: JSON 字符串 {"provider", "api_key", "base_url", "model"}，供 API 引擎使用
    split:      "1"（默认）开启一页多题自动拆分，"0" 关闭
    pages:      JSON 数组字符串，与 files 一一对应，仅 PDF 用（"1-5,8"，空 = 全部页）
    docmodes:   JSON 数组字符串，与 files 一一对应，仅 Word 用（"direct" 直转 / "ocr"）
    """
    cleanup_expired_tasks()

    if not files:
        raise HTTPException(status_code=400, detail="未接收到任何文件")

    # 解析每题识别引擎选择（非法 JSON 按全本地处理，不阻塞上传）
    # 合法值：local（本地模型）/ api（纯 API）/ hybrid（API + 本地裁剪插图）
    engine_list = []
    if engines:
        try:
            parsed = json.loads(engines)
            if isinstance(parsed, list):
                engine_list = [
                    str(e) if str(e) in ("api", "hybrid") else "local" for e in parsed
                ]
        except (ValueError, TypeError):
            logger.warning("engines 参数解析失败，按全部本地识别处理：%s", engines[:200])
    # 长度对齐：前端漏传时默认本地
    engine_list += ["local"] * (len(files) - len(engine_list))

    # 解析每文件页码范围（仅 PDF 用）与 Word 处理模式（direct 直转 / ocr）
    def _str_list(raw: str, n: int, default: str) -> list:
        try:
            v = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            v = []
        if not isinstance(v, list):
            v = []
        out = [str(x) for x in v[:n]]
        out += [default] * (n - len(out))
        return out

    pages_list = _str_list(pages, len(files), "")
    docmode_list = [m if m in ("direct", "ocr") else "direct"
                    for m in _str_list(docmodes, len(files), "direct")]

    # 解析 API 配置（仅当有题目选择 API 引擎时才需要）
    cfg_dict = {}
    if api_config:
        try:
            parsed_cfg = json.loads(api_config)
            if isinstance(parsed_cfg, dict):
                cfg_dict = {
                    k: str(parsed_cfg.get(k, ""))
                    for k in ("provider", "api_key", "base_url", "model", "workspace")
                }
        except (ValueError, TypeError):
            logger.warning("api_config 参数解析失败，已忽略")

    if any(e in ("api", "hybrid") for e in engine_list) and not cfg_dict.get("provider"):
        raise HTTPException(
            status_code=400,
            detail="有题目选择了 API 识别，但未提供 API 配置，请在前端「API 设置」中填写。",
        )

    task_id = uuid.uuid4().hex
    task_dir = TEMP_ROOT / task_id
    upload_dir = task_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        suffix = Path(f.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIX:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{f.filename}")
        # 重命名为序号文件名，避免中文文件名与重名问题
        save_path = upload_dir / f"{len(saved) + 1:03d}{suffix}"
        save_path.write_bytes(await f.read())
        saved.append(str(save_path))

    # 展开工作清单：PDF 渲染页图 / Word 解析可能耗时数秒到数十秒（docx→PDF 走 COM），
    # 放线程池执行避免阻塞事件循环；失败（加密 PDF、页码非法等）清理任务目录并返回 400
    try:
        plan = await run_in_threadpool(
            _expand_upload_plan, saved, [f.filename for f in files],
            engine_list, pages_list, docmode_list, task_dir,
        )
    except RuntimeError as e:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e))

    TASKS[task_id] = {
        "dir": task_dir,
        "files": saved,
        "plan": plan,
        "engines": engine_list,
        "api_config": cfg_dict,  # 仅内存保存，任务过期后随 TASKS 一起清除
        "split": split != "0",   # 一页多题自动拆分开关
        "orig_names": [f.filename for f in files],  # 原始文件名，历史任务里展示用
        "status": "uploaded",
        "created_at": time.time(),
        "docx_path": None,
        "failed": [],
        "error": None,
    }
    save_task_meta(task_id)
    logger.info(
        "新任务 %s：收到 %d 个文件，展开为 %d 个工作项（引擎：%s）",
        task_id[:8], len(saved), len(plan),
        ",".join(engine_list[: len(saved)]),
    )
    return {"task_id": task_id, "count": len(plan)}


@app.get("/process/{task_id}")
async def process(task_id: str, filename: str = "output.docx", font: str = DEFAULT_FONT, title: str = "题目"):
    """
    开始处理任务，以 SSE（Server-Sent Events）实时推送进度与日志。
    事件类型：log / progress / done / error。
    font 参数指定 Word 中文字体（仿宋 / 黑体 / 楷体 / 宋体 / 微软雅黑 / 等线）。
    title 参数指定每道题标题前缀（默认"题目"，生成"题目 N"）。
    """
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    if find_pandoc() is None:
        raise HTTPException(
            status_code=500,
            detail="未找到 Pandoc，无法生成 Word。请安装 Pandoc 或将便携版放入 backend/pandoc/ 目录。",
        )

    # 文件名安全处理：只保留基本字符，强制 .docx 后缀
    safe_name = Path(filename).name or "output.docx"
    if not safe_name.lower().endswith(".docx"):
        safe_name += ".docx"

    # 字体白名单校验，非法值回退默认字体
    if font not in AVAILABLE_FONTS:
        font = DEFAULT_FONT

    # 标题前缀安全处理：去空白、限长，避免注入异常内容
    title_prefix = title.strip()[:12] or "题目"

    log_queue: queue.Queue = queue.Queue()
    thread = threading.Thread(
        target=process_task, args=(task_id, safe_name, font, log_queue, title_prefix), daemon=True
    )
    thread.start()

    def event_stream():
        """从队列消费事件并按 SSE 格式输出，直到 done / error。"""
        while True:
            try:
                event = log_queue.get(timeout=300)  # 5 分钟无消息视为超时
            except queue.Empty:
                yield 'data: {"kind": "error", "message": "处理超时"}\n\n'
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["kind"] in ("done", "error"):
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/finalize/{task_id}")
async def finalize(task_id: str, request: Request):
    """
    预览编辑确认后生成 Word。
    请求体 JSON：
        questions: 编辑后的每题 Markdown 数组（顺序与预览一致）
        filename:  输出文件名（缺省 output.docx）
        font:      中文字体（白名单校验）
        title:     标题前缀（缺省"题目"）
    """
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期，请重新上传")

    if find_pandoc() is None:
        raise HTTPException(
            status_code=500,
            detail="未找到 Pandoc，无法生成 Word。请安装 Pandoc 或将便携版放入 backend/pandoc/ 目录。",
        )

    payload = await request.json()
    questions = payload.get("questions") or []
    # 过滤掉被用户清空的题目；全部为空则报错
    questions = [str(q) for q in questions if str(q).strip()]
    if not questions:
        raise HTTPException(status_code=400, detail="编辑后的内容为空，无法生成 Word。")

    # 文件名 / 字体 / 标题前缀的安全处理（与 /process 一致）
    filename = Path(str(payload.get("filename") or "output.docx")).name
    # 用户可能随手输入 .docx / .pdf 后缀，统一剥掉再按导出格式添加
    stem = re.sub(r"\.(docx|pdf)$", "", filename, flags=re.IGNORECASE) or "output"
    font = str(payload.get("font") or DEFAULT_FONT)
    if font not in AVAILABLE_FONTS:
        font = DEFAULT_FONT
    title_prefix = str(payload.get("title") or "题目").strip()[:12] or "题目"
    # 导出格式：docx（默认）/ pdf / both（两者都生成）
    fmt = str(payload.get("format") or "docx")
    if fmt not in ("docx", "pdf", "both"):
        fmt = "docx"

    task_dir = task["dir"]
    try:
        md_path = task_dir / "questions.md"
        merge_questions(questions, str(md_path), title_prefix)
        # docx 总是生成（PDF 由它转换而来，也便于用户补下载 Word 版）
        docx_path = task_dir / f"{stem}.docx"
        logger.info("[%s] 正在调用 Pandoc 生成 Word 文件……", task_id[:8])
        markdown_to_docx(str(md_path), str(docx_path), str(task_dir), font)
        # 页眉 / 页脚 / 页面水印（可选；PDF 由装饰后的 docx 转换，装饰自动带入）
        wm = normalize_watermark(payload.get("watermark"))
        header = normalize_hf(payload.get("header"))
        footer = normalize_hf(payload.get("footer"))
        if wm or header or footer:
            apply_decorations(str(docx_path), header, footer, wm, font)
            logger.info("[%s] 已应用页眉/页脚/水印装饰", task_id[:8])
        task["docx_path"] = str(docx_path)
        logger.info("[%s] Word 生成完成：%s.docx", task_id[:8], stem)

        # 按需转换 PDF（Word → WPS → LibreOffice 逐级回退）
        pdf_error = None
        if fmt in ("pdf", "both"):
            pdf_path = task_dir / f"{stem}.pdf"
            try:
                docx_to_pdf(str(docx_path), str(pdf_path),
                            log=lambda m: logger.info("[%s] %s", task_id[:8], m))
                task["pdf_path"] = str(pdf_path)
            except Exception as e:
                pdf_error = str(e)
                logger.error("[%s] PDF 生成失败：%s", task_id[:8], e)
                # 用户只想要 PDF 时失败即整体失败；both 则降级为只给 Word
                if fmt == "pdf":
                    raise RuntimeError(pdf_error)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("任务 %s 生成文件失败：%s", task_id, e)
        raise HTTPException(status_code=500, detail=f"生成文件失败：{e}")

    files = {"docx": True, "pdf": bool(task.get("pdf_path"))}
    # 保存最终版本（含用户编辑）与输出信息，历史任务再导出 / 重启用它恢复
    task["question_mds"] = questions
    task["output_stem"] = stem
    save_task_meta(task_id)
    logger.info("[%s] 生成完成：%s", task_id[:8], files)
    resp = {"ok": True, "failed": task.get("failed", []), "files": files}
    if pdf_error:
        resp["pdf_error"] = pdf_error
    return resp


def _build_doc_preview(
    task: dict, task_id: str, questions: list, font: str, title_prefix: str,
    header: dict | None, footer: dict | None, wm: dict | None,
) -> list:
    """
    生成文档效果预览（在线程池中执行）：
    当前编辑内容 → preview.docx → 应用页眉/页脚/水印 → preview.pdf → 逐页 PNG。
    返回相对任务目录的图片路径列表。
    """
    task_dir = task["dir"]
    md_path = task_dir / "preview.md"
    merge_questions(questions, str(md_path), title_prefix)
    docx_path = task_dir / "preview.docx"
    markdown_to_docx(str(md_path), str(docx_path), str(task_dir), font)
    if wm or header or footer:
        apply_decorations(str(docx_path), header, footer, wm, font)
    pdf_path = task_dir / "preview.pdf"
    docx_to_pdf(str(docx_path), str(pdf_path),
                log=lambda m: logger.info("[%s] 预览：%s", task_id[:8], m))

    import pymupdf

    pages_dir = task_dir / "preview_pages"
    shutil.rmtree(pages_dir, ignore_errors=True)  # 清掉上一次预览的页图
    pages_dir.mkdir(exist_ok=True)
    pages = []
    matrix = pymupdf.Matrix(1.6, 1.6)  # ~115dpi，清晰度与体积平衡
    doc = pymupdf.open(str(pdf_path))
    try:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            rel = f"preview_pages/page-{i + 1}.png"
            pix.save(str(task_dir / rel))
            pages.append(rel)
    finally:
        doc.close()
    return pages


@app.post("/preview-doc/{task_id}")
async def preview_doc(task_id: str, request: Request):
    """
    文档效果预览：按当前编辑内容与页眉/页脚/水印设置生成 docx 并渲染为逐页图片。
    请求体 JSON：
        questions: 当前编辑后的每题 Markdown 数组
        font / title: 与 /finalize 同义
        header / footer: 页眉 / 页脚文字（可空）
        watermark: { type: text|image, text, image(dataURL), size, opacity, angle }
    返回：{ ok, pages: ["preview_pages/page-1.png", ...] }（经 /task-files 访问）
    """
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期，请重新上传")
    if find_pandoc() is None:
        raise HTTPException(status_code=500, detail="未找到 Pandoc，无法生成预览。")

    payload = await request.json()
    questions = [str(q) for q in (payload.get("questions") or []) if str(q).strip()]
    if not questions:
        raise HTTPException(status_code=400, detail="内容为空，无法生成预览。")
    font = str(payload.get("font") or DEFAULT_FONT)
    if font not in AVAILABLE_FONTS:
        font = DEFAULT_FONT
    title_prefix = str(payload.get("title") or "题目").strip()[:12] or "题目"
    wm = normalize_watermark(payload.get("watermark"))
    header = normalize_hf(payload.get("header"))
    footer = normalize_hf(payload.get("footer"))

    try:
        pages = await run_in_threadpool(
            _build_doc_preview, task, task_id, questions, font, title_prefix,
            header, footer, wm,
        )
    except Exception as e:
        logger.error("任务 %s 生成文档预览失败：%s", task_id, e)
        raise HTTPException(status_code=500, detail=f"生成文档预览失败：{e}")
    return {"ok": True, "pages": pages}


@app.get("/task-files/{task_id}/{file_path:path}")
async def task_files(task_id: str, file_path: str):
    """
    返回任务目录内的文件（供前端预览插图）。
    仅限当前任务的临时目录，校验路径防止越界访问。
    """
    task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")

    root = task["dir"].resolve()
    target = (root / file_path).resolve()
    # 路径必须仍在任务目录内（阻止 ../ 越界）
    if root not in target.parents and target != root:
        raise HTTPException(status_code=403, detail="非法的文件路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(str(target))


@app.get("/download/{task_id}")
async def download(task_id: str, kind: str = "docx"):
    """
    返回生成的文件（作为附件下载）。
    kind=docx（默认）下载 Word；kind=pdf 下载 PDF（需 /finalize 时选择了 PDF 导出）。
    """
    task = TASKS.get(task_id)
    key = "pdf_path" if kind == "pdf" else "docx_path"
    if task is None or not task.get(key):
        raise HTTPException(status_code=404, detail="文件不存在或任务尚未完成")

    file_path = Path(task[key])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件已被清理，请重新处理")

    media_type = (
        "application/pdf" if kind == "pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path=str(file_path), filename=file_path.name, media_type=media_type)


@app.get("/health")
async def health():
    """健康检查：返回后端与 Pandoc 状态，供前端启动时探测。"""
    return {"status": "ok", "pandoc": find_pandoc() is not None}


@app.get("/api-providers")
async def api_providers():
    """返回可选的 API 服务商预设（不含密钥），供前端渲染「API 设置」下拉框。"""
    return {
        "providers": [
            {
                "value": key,
                "label": p["name"],
                "base_url": p["base_url"],
                "model": p["model"],
            }
            for key, p in PROVIDERS.items()
        ]
    }


# ---------------------------------------------------------------------------
# 历史任务与用量统计
# ---------------------------------------------------------------------------
def _history_summary(task_id: str, task: dict) -> dict:
    """历史任务列表的单条摘要。"""
    usage = task.get("usage") or []
    return {
        "task_id": task_id,
        "created_at": task.get("created_at"),
        "orig_names": task.get("orig_names", []),
        "question_count": len(task.get("question_mds") or []),
        "failed": task.get("failed", []),
        "output_stem": task.get("output_stem"),
        "has_docx": bool(task.get("docx_path") and Path(task["docx_path"]).exists()),
        "has_pdf": bool(task.get("pdf_path") and Path(task["pdf_path"]).exists()),
        "api_calls": len(usage),
        "total_tokens": sum(u.get("total_tokens", 0) for u in usage) if usage else 0,
    }


@app.get("/history")
async def history_list():
    """历史任务列表（7 天内有识别结果的任务，新的在前）。"""
    cleanup_expired_tasks()
    items = [
        _history_summary(tid, t) for tid, t in TASKS.items() if t.get("question_mds")
    ]
    items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return {"items": items}


@app.get("/history/{task_id}")
async def history_detail(task_id: str):
    """历史任务详情：含每题最终 Markdown，供「重新导出」使用。"""
    task = TASKS.get(task_id)
    if task is None or not task.get("question_mds"):
        raise HTTPException(status_code=404, detail="历史任务不存在或已被清理")
    return {**_history_summary(task_id, task), "question_mds": task["question_mds"]}


@app.delete("/history/{task_id}")
async def history_delete(task_id: str):
    """删除历史任务及其全部临时文件。"""
    task = TASKS.pop(task_id, None)
    if task is None:
        raise HTTPException(status_code=404, detail="历史任务不存在")
    shutil.rmtree(task["dir"], ignore_errors=True)
    logger.info("历史任务已删除：%s", task_id[:8])
    return {"ok": True}


@app.get("/usage")
async def usage_stats():
    """API token 用量的本地累计统计（api_usage.json）。"""
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"total": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "by_model": {}}


# 启动时恢复未过期的历史任务（在挂载静态文件与启动服务之前执行）
load_persisted_tasks()


# ---------------------------------------------------------------------------
# 托管前端静态文件（单进程模式）
# 先在 frontend 目录执行 npm run build 生成 dist 后，
# 浏览器直接访问 http://localhost:8000 即可使用，无需单独启动前端。
# 注意：必须挂在所有 API 路由之后，避免抢走路由匹配。
# ---------------------------------------------------------------------------
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
    logger.info("已挂载前端静态文件目录：%s", FRONTEND_DIST)
else:
    logger.warning("未找到前端构建产物 %s，请先执行 npm run build", FRONTEND_DIST)


if __name__ == "__main__":
    import sys
    import uvicorn

    # 未捕获的异常也写入 backend.log，避免窗口里一闪而过无法排查
    def _excepthook(exc_type, exc_value, exc_tb):
        logger.exception("后端发生未捕获异常", exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _excepthook

    try:
        uvicorn.run(app, host="127.0.0.1", port=8000)
    except Exception:
        logger.exception("后端异常退出")
        raise
