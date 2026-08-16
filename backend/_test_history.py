# -*- coding: utf-8 -*-
"""端到端验证：历史任务持久化 + 重新导出 + 用量接口"""
import json, io, sys, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:8000"

def post_json(path, payload, timeout=240):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))

def get(path, timeout=60):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read().decode("utf-8"))

# 1. 读取最新任务（刚才演示任务 913a93a3 已 finalize 过，但元信息是旧代码生成的，无 task.json）
#    所以跑一个新任务：上传 → SSE → finalize
import subprocess
up = subprocess.run(
    ["curl", "-s", "-X", "POST", BASE + "/upload",
     "-F", "files=@E:/PICTURE-TO-WORD/必刷题第四页第六题.png",
     "-F", "engine=local", "-F", "split=1"],
    capture_output=True, text=True).stdout
task_id = json.loads(up)["task_id"]
print("task_id:", task_id[:8])

questions = None
req = urllib.request.Request(BASE + f"/process/{task_id}")
with urllib.request.urlopen(req, timeout=280) as resp:
    for raw in resp:
        line = raw.decode("utf-8").strip()
        if line.startswith("data:"):
            ev = json.loads(line[5:])
            if ev["kind"] == "preview":
                questions = ev["questions"]
            if ev["kind"] in ("preview", "error", "done"):
                break
assert questions, "未收到预览事件"
print("识别完成，题数:", len(questions))

r = post_json(f"/finalize/{task_id}", {"questions": questions, "filename": "历史测试.docx", "format": "docx"})
print("finalize:", r)

# 2. 历史列表
h = get("/history")
print("历史任务数:", len(h["items"]))
item = next((x for x in h["items"] if x["task_id"] == task_id), None)
assert item, "新任务未出现在历史列表"
print("条目:", json.dumps(item, ensure_ascii=False))

# 3. 详情含最终 Markdown
d = get(f"/history/{task_id}")
assert d["question_mds"] == questions, "详情 question_mds 不一致"
print("详情 OK，题数:", len(d["question_mds"]))

# 4. 重新导出（改 PDF 格式，无需重新识别）
r2 = post_json(f"/finalize/{task_id}",
               {"questions": d["question_mds"], "filename": "历史测试_再导出.pdf", "format": "pdf"})
print("重新导出:", r2)
assert r2["files"]["pdf"], "PDF 未生成"

# 5. 重新下载
import urllib.request as u2
data = u2.urlopen(BASE + f"/download/{task_id}?kind=pdf", timeout=60).read()
print("PDF 下载字节数:", len(data))
assert len(data) > 10000

print("TASK_ID=" + task_id)
print("全部通过")
